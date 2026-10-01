import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from utils import (
    FAST_LANE_MEDIA,
    SECOND_LANE_MEDIA,
    THIRD_LANE_MEDIA,
    build_general_queries,
    build_site_queries_for_media,
    canonicalize_url,
    classify_media,
    dedupe_strings,
    extract_keywords,
    extract_source_id,
    keyword_overlap,
    safe_str,
    sim,
    title_core,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

DMENU_CATEGORIES = [
    "life",
    "trend",
    "economy",
    "entertainment",
    "sports",
    "world",
    "domestic",
    "culture",
    "it",
    "car",
    "gourmet",
    "health",
]

BIGLOBE_CATEGORIES = [
    "entertainment",
    "domestic",
    "economy",
    "trend",
    "world",
    "sports",
    "life",
    "gourmet",
    "health",
    "it",
    "culture",
]

GOO_CATEGORIES = [
    "life",
    "trend",
    "economy",
    "entertainment",
    "sports",
    "world",
]


# ─────────────────────────────────────────────
# 内部ユーティリティ
# ─────────────────────────────────────────────

def _resolve_google_redirect(url):
    try:
        parsed = urlparse(url)
        if "google." in parsed.netloc and parsed.path.startswith("/url"):
            qs = parse_qs(parsed.query)
            for key in ["q", "url"]:
                if qs.get(key):
                    return unquote(qs[key][0])
    except Exception:
        pass
    return url


def _get(url, timeout=10):
    try:
        response = SESSION.get(url, timeout=timeout, allow_redirects=True)
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return response.text
    except Exception:
        return ""


def _result_item(url, title="", snippet="", engine="", rank=9999):
    final_url = _resolve_google_redirect(safe_str(url))
    return {
        "url": final_url,
        "canonical_url": canonicalize_url(final_url),
        "title": safe_str(title),
        "snippet": safe_str(snippet),
        "engine": engine,
        "rank": rank,
        "media": classify_media(final_url, title),
    }


def _stable_result_sort_key(row):
    return (
        safe_str(row.get("canonical_url") or row.get("url")),
        safe_str(row.get("media")),
        int(row.get("rank", 9999) or 9999),
        -len(safe_str(row.get("title"))),
        safe_str(row.get("engine")),
    )


# ─────────────────────────────────────────────
# 検索エンジン
# ─────────────────────────────────────────────

def search_bing(query, max_results=10):
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={max_results}&setlang=ja&mkt=ja-JP"
    html = _get(url, timeout=10)
    soup = BeautifulSoup(html, "lxml")
    out = []

    for i, li in enumerate(soup.select("li.b_algo"), start=1):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        title = a.get_text(" ", strip=True)
        snippet = ""
        p = li.select_one(".b_caption p")
        if p:
            snippet = p.get_text(" ", strip=True)
        out.append(_result_item(a["href"], title, snippet, "bing", i))
        if len(out) >= max_results:
            break

    return out


def search_google(query, max_results=10):
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=ja&num={max_results}&gl=JP"
    html = _get(url, timeout=10)
    soup = BeautifulSoup(html, "lxml")
    out = []
    rank = 1

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href.startswith("/url?"):
            continue
        final_url = _resolve_google_redirect("https://www.google.com" + href)
        if not final_url.startswith("http"):
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            h3 = a.find("h3")
            if h3:
                title = h3.get_text(" ", strip=True)
        if not title:
            continue
        out.append(_result_item(final_url, title, "", "google", rank))
        rank += 1
        if len(out) >= max_results:
            break

    return out


def search_yahoo(query, max_results=10):
    url = f"https://search.yahoo.co.jp/search?p={quote_plus(query)}&n={max_results}"
    html = _get(url, timeout=10)
    soup = BeautifulSoup(html, "lxml")
    out = []
    rank = 1

    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href.startswith("http"):
            continue
        if "search.yahoo.co.jp" in href:
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 6:
            continue
        out.append(_result_item(href, title, "", "yahoo", rank))
        rank += 1
        if len(out) >= max_results:
            break

    return out


# ─────────────────────────────────────────────
# 直接URL生成
# ─────────────────────────────────────────────

def build_all_direct_candidates(source_id):
    """
    source_idからURLが確実に確定できるメディアのみdirect候補を生成する。
    カテゴリ総当たり・IDが異なる媒体（mixi/ニコニコ/NewsPicks等）は含めない。
    """
    if not source_id:
        return []

    sid = source_id
    # source_idがURLに直接埋め込まれることが確認されているメディアのみ
    candidates = [
        f"https://www.excite.co.jp/news/article/Ovo_{sid}/",
        f"https://news.infoseek.co.jp/article/ovo_O{sid}/",
        # GIGAパークは robots.txt で一般クローラを全面禁止しているため除外
        # nifty ニュースは2025年サービス終了のため除外
        # BIGLOBEニュースは2025年6月サービス終了のため除外
    ]

    # dメニュー：全カテゴリを総当たり（実際にはカテゴリ1つだが、事前に特定不可）
    for cat in DMENU_CATEGORIES:
        candidates.append(f"https://topics.smt.docomo.ne.jp/article/ovo/{cat}/ovo-O{sid}")

    seen = set()
    out = []
    for url in candidates:
        cu = canonicalize_url(url)
        if cu and cu not in seen:
            seen.add(cu)
            out.append(url)
    return out


# ─────────────────────────────────────────────
# ソースページ・周辺導線クロール
# ─────────────────────────────────────────────

def crawl_excite_ovo_source(source_id, max_results=40):
    out = []
    seen = set()

    if source_id:
        direct_url = f"https://www.excite.co.jp/news/article/Ovo_{source_id}/"
        item = _result_item(direct_url, "", "", "direct_excite", 0)
        out.append(item)
        seen.add(item["canonical_url"])

    html = _get("https://www.excite.co.jp/news/source/Ovo/", timeout=10)
    if not html:
        return out

    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if href.startswith("/"):
            href = "https://www.excite.co.jp" + href
        if "/news/article/" not in href:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        item = _result_item(href, title, "", "excite_source", len(out) + 1)
        key = item["canonical_url"]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= max_results:
            break

    return out


def crawl_infoseek_direct(source_id):
    if not source_id:
        return []
    return [_result_item(f"https://news.infoseek.co.jp/article/ovo_O{source_id}/", "", "", "direct_infoseek", 0)]


def crawl_dmenu_all_categories(source_id):
    if not source_id:
        return []
    out = []
    seen = set()
    # 全カテゴリを総当たり（実際にはいずれか1つに記事が存在）
    for cat in DMENU_CATEGORIES:
        url = f"https://topics.smt.docomo.ne.jp/article/ovo/{cat}/ovo-O{source_id}"
        cu = canonicalize_url(url)
        if cu not in seen:
            seen.add(cu)
            out.append(_result_item(url, "", "", "direct_dmenu", 0))
    return out


def crawl_biglobe_all_categories(source_id):
    # Biglobeはbuild_all_direct_candidatesで全カテゴリURL生成済みのため不要
    return []


def crawl_goo_all_categories(source_id):
    # gooはカテゴリが確定しないため直接生成は行わない（検索経由で取得）
    return []


def _make_compare_title(source_title, override_keywords):
    """
    タイトル類似判定に使う比較テキストを返す。
    source_title が空の場合は override_keywords を連結したテキストで代替する。
    これにより「bzページでJS未レンダリング＋補足KW入力」の組み合わせで
    クローラーが正しく機能する。
    """
    if source_title:
        return source_title
    if override_keywords:
        return " ".join(override_keywords)
    return ""


def crawl_goo_news_search(source_title, override_keywords=None):
    """
    gooニュース内検索でOVO記事を探す。
    override_keywords 指定時は source_title が空でも実行する。
    """
    out = []
    seen = set()
    source_title = safe_str(source_title)
    # source_title が空でも override_keywords があれば続行する
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    if override_keywords:
        search_terms = list(override_keywords)[:3]
    else:
        core = title_core(source_title)
        kws = extract_keywords(source_title)
        long_kws = [kw for kw in kws if len(kw) >= 3][:3]
        clean_core = re.sub(r"^[「」『』【】（）()\[\]〔〕\s]+", "", core)
        fallback_prefix = clean_core[:15] if clean_core else core[:15]
        search_terms = long_kws if long_kws else ([fallback_prefix] if fallback_prefix else [])
    if not search_terms:
        return out

    for term in search_terms:
        search_url = f"https://news.goo.ne.jp/search.html?MT={quote_plus(term)}&kind=&from=&newsTopic="
        html = _get(search_url, timeout=10)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://news.goo.ne.jp" + href
            if "news.goo.ne.jp/article/" not in href:
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
            kw = keyword_overlap(title, compare_title)
            if ts < 0.40 and kw < 0.20:
                continue
            out.append(_result_item(href, title, "", "goo_search", len(out) + 1))
            if len(out) >= 10:
                break
        if out:
            break

    return out


def crawl_livedoor_kyodo(source_title, override_keywords=None):
    """
    livedoorニュースからOVO転載記事を探す。
    まずキーワード検索（全期間対応）を試み、結果が少ない場合は一覧ページも走査する。
    override_keywords 指定時は source_title が空でも実行する。
    """
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    if override_keywords:
        search_terms = list(override_keywords)[:3]
    else:
        kws = extract_keywords(source_title)
        long_kws = [kw for kw in kws if len(kw) >= 3][:3]
        search_terms = long_kws if long_kws else kws[:2]

    def _add(href, title, engine):
        nonlocal out
        key = canonicalize_url(href)
        if key in seen:
            return
        seen.add(key)
        ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
        kw = keyword_overlap(title, compare_title)
        if ts < 0.40 and kw < 0.20:
            return
        out.append(_result_item(href, title, "", engine, len(out) + 1))

    # ── キーワード検索（古い記事も対応）──
    for term in search_terms:
        if len(out) >= 12:
            break
        search_url = f"https://news.livedoor.com/search/?q={quote_plus(term)}"
        html = _get(search_url, timeout=10)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://news.livedoor.com" + href
            if "news.livedoor.com/article/detail/" not in href:
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            _add(href, title, "livedoor_search")
            if len(out) >= 12:
                break

    if len(out) >= 3:
        return out

    # ── フォールバック：一覧ページ（直近記事のみ）──
    category_urls = [
        "https://news.livedoor.com/category/editorial/45/",  # 国内・社会
        "https://news.livedoor.com/category/editorial/46/",  # 経済
        "https://news.livedoor.com/category/editorial/47/",  # 国際
        "https://news.livedoor.com/category/editorial/48/",  # 政治
        "https://news.livedoor.com/category/editorial/49/",  # 科学・IT
        "https://news.livedoor.com/category/editorial/50/",  # 生活
        "https://news.livedoor.com/category/editorial/52/",  # 芸能
        "https://news.livedoor.com/category/editorial/55/",  # スポーツ
    ]
    for cat_url in category_urls:
        if len(out) >= 12:
            break
        html = _get(cat_url, timeout=10)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://news.livedoor.com" + href
            if "news.livedoor.com/article/detail/" not in href:
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            _add(href, title, "livedoor_kyodo")

    return out


def crawl_47news_latest(source_title, override_keywords=None):
    """
    47NEWSからOVO転載記事を探す。
    まずキーワード検索（全期間対応）を試み、結果が少ない場合は一覧ページも走査する。
    override_keywords 指定時は source_title が空でも実行する。
    """
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    if override_keywords:
        search_terms = list(override_keywords)[:3]
    else:
        kws = extract_keywords(source_title)
        long_kws = [kw for kw in kws if len(kw) >= 3][:3]
        search_terms = long_kws if long_kws else kws[:2]

    def _add(href, title, engine):
        nonlocal out
        key = canonicalize_url(href)
        if key in seen:
            return
        seen.add(key)
        ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
        kw = keyword_overlap(title, compare_title)
        if ts < 0.40 and kw < 0.20:
            return
        out.append(_result_item(href, title, "", engine, len(out) + 1))

    # ── キーワード検索（古い記事も対応）──
    for term in search_terms:
        if len(out) >= 12:
            break
        search_url = f"https://www.47news.jp/?s={quote_plus(term)}"
        html = _get(search_url, timeout=10)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.47news.jp" + href
            if not re.search(r"47news\.jp/\d+\.html", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            _add(href, title, "47news_search")
            if len(out) >= 12:
                break

    if len(out) >= 3:
        return out

    # ── フォールバック：一覧ページ（直近記事のみ）──
    list_urls = [
        "https://www.47news.jp/",
        "https://www.47news.jp/economy/",
        "https://www.47news.jp/domestic/",
        "https://www.47news.jp/politics/",
        "https://www.47news.jp/world/",
        "https://www.47news.jp/culture/",
        "https://www.47news.jp/entertainment/",
        "https://www.47news.jp/sports/",
        "https://www.47news.jp/life/",
        "https://www.47news.jp/science/",
    ]
    for list_url in list_urls:
        if len(out) >= 12:
            break
        html = _get(list_url, timeout=10)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.47news.jp" + href
            if not re.search(r"47news\.jp/\d+\.html", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            _add(href, title, "47news_latest")

    return out


def crawl_yahoo_news_search(source_title, override_keywords=None):
    """
    Yahoo!ニュース内検索でOVO転載記事を探す（検索エンジン非依存）。
    override_keywords 指定時は source_title が空でも実行する。
    """
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    if override_keywords:
        search_terms = list(override_keywords)[:3]
    else:
        kws = extract_keywords(source_title)
        long_kws = [kw for kw in kws if len(kw) >= 3][:3]
        search_terms = long_kws if long_kws else kws[:2]
    if not search_terms:
        return out

    for term in search_terms:
        search_url = f"https://news.yahoo.co.jp/search?p={quote_plus(term)}&ei=UTF-8&rkf=2"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if not re.search(r"news\.yahoo\.co\.jp/articles/[A-Za-z0-9]+", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
            kw = keyword_overlap(title, compare_title)
            if ts < 0.40 and kw < 0.20:
                continue
            out.append(_result_item(href, title, "", "yahoo_news_search", len(out) + 1))
            if len(out) >= 10:
                break
        if out:
            break

    return out


def crawl_bing_news(source_title, override_keywords=None):
    """
    Bing News専用エンドポイント（bing.com/news/search）でKyodo転載記事を横断検索。
    a.title セレクタで実際のニュース記事URLを取得できる（通常Bing検索とは別構造）。
    1クエリでlivedoor/ニコニコ/Yahoo/MSN/antenna/47news/PR TIMES等を横断的にカバーする。
    """
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    # クエリ構築: タイトルプレフィクス・KW+共同通信・KW単体の3パターン
    queries = []
    clean_core = re.sub(r"^[「」『』【】（）()\[\]〔〕\s]+", "", title_core(compare_title))
    prefix18 = clean_core[:18] if clean_core else ""
    if prefix18:
        queries.append(f'"{prefix18}"')
        queries.append(f'"{prefix18}" 共同通信')

    if override_keywords:
        base_terms = list(override_keywords)[:3]
    else:
        kws = extract_keywords(source_title)
        base_terms = [kw for kw in kws if len(kw) >= 3][:3]

    for term in base_terms[:2]:
        queries.append(f"{term} 共同通信")
    for term in base_terms[:2]:
        queries.append(term)

    queries = dedupe_strings(queries)

    for query in queries:
        search_url = f"https://www.bing.com/news/search?q={quote_plus(query)}&setlang=ja&mkt=ja-JP"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        found_new = False
        for a in soup.select("a.title"):
            href = a.get("href", "")
            if not href or not href.startswith("http"):
                continue
            if "bing.com" in href or "microsoft.com" in href:
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
            kw_score = keyword_overlap(title, compare_title)
            if ts < 0.36 and kw_score < 0.16:
                continue
            out.append(_result_item(href, title, "", "bing_news", len(out) + 1))
            found_new = True
            if len(out) >= 25:
                return out
        if len(out) >= 15 and not found_new:
            break

    return out


def crawl_nicovideo_news_search(source_title, override_keywords=None):
    """ニコニコニュースのキーワード検索でKyodo転載記事を探す（検索エンジン非依存）"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    if override_keywords:
        search_terms = list(override_keywords)[:2]
    else:
        kws = extract_keywords(source_title)
        long_kws = [kw for kw in kws if len(kw) >= 3][:2]
        search_terms = long_kws if long_kws else kws[:2]
    if not search_terms:
        return out

    for term in search_terms:
        search_url = f"https://news.nicovideo.jp/search/?q={quote_plus(term)}"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://news.nicovideo.jp" + href
            if not re.search(r"news\.nicovideo\.jp/watch/nw\d+", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
            kw = keyword_overlap(title, compare_title)
            if ts < 0.36 and kw < 0.16:
                continue
            out.append(_result_item(href, title, "", "nicovideo_search", len(out) + 1))
            if len(out) >= 8:
                break
        if out:
            break

    return out


def crawl_antenna_for_source(source_title, override_keywords=None):
    """antennaのキーワード検索でKyodo転載記事を探す（検索エンジン非依存）"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    if override_keywords:
        search_terms = list(override_keywords)[:2]
    else:
        kws = extract_keywords(source_title)
        long_kws = [kw for kw in kws if len(kw) >= 3][:2]
        search_terms = long_kws if long_kws else kws[:2]
    if not search_terms:
        return out

    for term in search_terms:
        search_url = f"https://antenna.jp/search?q={quote_plus(term)}"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://antenna.jp" + href
            if not re.search(r"antenna\.jp/articles/\w+", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
            kw = keyword_overlap(title, compare_title)
            if ts < 0.36 and kw < 0.16:
                continue
            out.append(_result_item(href, title, "", "antenna_search", len(out) + 1))
            if len(out) >= 8:
                break
        if out:
            break

    return out


def crawl_nifty_patterns(source_id):
    # nifty ニュースは2025年サービス終了
    return []


def crawl_misc_direct_patterns(source_id):
    """
    source_idがURLに直接使われることが確認されているメディアのみ。
    ニコニコ(nwID≠source_id)・mixi(記事ID≠source_id)・
    NewsPicks・NEWSjp・antenna・livedoor・ameba は除外。
    """
    # GIGAパーク: robots.txt で一般クローラを全面禁止しているため除外
    # めるもニュース: サーバーに接続できない（サービス終了とみられる）ため除外
    return []


def crawl_mixi_ovo_source(source_id=None, max_results=30):
    """
    mixiニュースのOVO媒体一覧ページ（media_id=201）をクロールして記事URLを収集する。
    mixi記事IDはsource_idと異なるため、直接URL生成は不可。ページクロールで対応。
    """
    out = []
    seen = set()

    for page in range(1, 3):
        # media_id=201 は誤り。id=201 が正しいパラメータ名（media_crawlers.pyコメント参照）
        url = f"https://news.mixi.jp/list_news_media.pl?id=201&page={page}"
        html = _get(url, timeout=10)
        if not html:
            break

        soup = BeautifulSoup(html, "lxml")
        found_any = False
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "view_news.pl" not in href:
                continue
            if href.startswith("/"):
                href = "https://news.mixi.jp" + href
            elif not href.startswith("http"):
                continue
            title = a.get_text(" ", strip=True)
            item = _result_item(href, title, "", "mixi_source", len(out) + 1)
            key = item["canonical_url"]
            if key and key not in seen:
                seen.add(key)
                out.append(item)
                found_any = True
            if len(out) >= max_results:
                break

        if not found_any or len(out) >= max_results:
            break

    return out


# ─────────────────────────────────────────────
# クエリ補強
# ─────────────────────────────────────────────

def build_extra_queries(source_title, keywords, source_id):
    """
    補強クエリ生成。
    - 通常KW（全記事共通）を基本とし、
    - 英数字KWが2個以上の記事では追加クエリを生成（補助）。
    - タイトル前半15文字の引用符付きクエリも追加（汎用的で確実にヒット）。
    """
    from utils import extract_alpha_keywords
    queries = []
    core = title_core(source_title)
    short_core = core if len(core) <= 25 else ""
    # 先頭の「」『』【】等を除去してからprefix取得（「で始まるタイトルのクエリ品質を改善）
    clean_core = re.sub(r"^[「」『』【】（）()\[\]〔〕〈〉《》\s]+", "", core)
    prefix15 = clean_core[:15] if clean_core else core[:15]
    prefix12 = clean_core[:12] if clean_core else core[:12]
    alpha_kws = extract_alpha_keywords(source_title, max_count=4)

    # ── タイトル前半引用符クエリ（汎用的に最も効果的）──
    # 前半15文字の引用符付き検索は、フルタイトル検索より短く
    # かつ記事を特定できるため検索エンジンで確実にヒットする
    if prefix15:
        queries.append(f'"{prefix15}"')
        queries.append(f'"{prefix15}" NewsPicks')
        queries.append(f'"{prefix15}" SmartNews')
        queries.append(f'"{prefix15}" NEWSjp')
        queries.append(f'"{prefix15}" mixi')
        queries.append(f'"{prefix15}" site:newspicks.com')
        queries.append(f'"{prefix15}" site:news.jp')
        queries.append(f'"{prefix15}" site:news.mixi.jp')
        queries.append(f'"{prefix15}" site:smartnews.com')
        queries.append(f'"{prefix15}" site:www.smartnews.com')
        queries.append(f'"{prefix15}" site:news.nicovideo.jp')
        queries.append(f'"{prefix15}" site:www.chiba-tv.com')
        queries.append(f'"{prefix15}" site:www.47news.jp')
        queries.append(f'"{prefix15}" site:news.yahoo.co.jp')
        queries.append(f'"{prefix15}" site:www.msn.com')
        # PR配信サイト（biz/プレスリリース記事のカバー）
        queries.append(f'"{prefix15}" site:prtimes.jp')
        queries.append(f'"{prefix15}" site:at-press.net')
        queries.append(f'"{prefix15}" site:dreamnews.jp')
        queries.append(f'"{prefix15}" site:finance.yahoo.co.jp')
    if prefix12 and prefix12 != prefix15:
        # より短い12文字版（前半15文字で零ヒットの場合の保険）
        queries.append(f'"{prefix12}" site:newspicks.com')
        queries.append(f'"{prefix12}" site:news.jp')
        queries.append(f'"{prefix12}" site:news.mixi.jp')
        queries.append(f'"{prefix12}" site:smartnews.com')

    # ── 通常KW（全記事で機能するベースクエリ）──
    if len(keywords) >= 2:
        kw2 = f"{keywords[0]} {keywords[1]}"
        kw2q = f'"{keywords[0]}" "{keywords[1]}"'
        queries.append(kw2q)
        queries.append(f"{kw2q} 共同通信")
        queries.append(f"{kw2q} ニュース")
        queries.append(f"{kw2q} MSN")
        queries.append(f"{kw2q} Yahoo")
        queries.append(f"{kw2q} 47NEWS")
        queries.append(f"{kw2q} antenna")
        queries.append(f"{kw2q} GIGAパーク")
        queries.append(f"{kw2q} ニコニコニュース")
        queries.append(f"{kw2q} チバテレ")
        queries.append(f"{kw2q} NewsPicks")
        queries.append(f"{kw2q} NEWSjp")
        queries.append(f"{kw2q} SmartNews")
        queries.append(f"{kw2q} mixi")
        queries.append(f"site:news.mixi.jp {kw2}")
        queries.append(f"site:newspicks.com {kw2}")
        queries.append(f"site:smartnews.com {kw2}")
        queries.append(f"site:news.jp {kw2}")
        # PR配信サイト
        queries.append(f"site:prtimes.jp {kw2}")
        queries.append(f"site:at-press.net {kw2}")
        queries.append(f"site:dreamnews.jp {kw2}")
        queries.append(f"site:finance.yahoo.co.jp {kw2}")

    if len(keywords) >= 3:
        kw3 = f"{keywords[0]} {keywords[1]} {keywords[2]}"
        kw3q = f'"{keywords[0]}" "{keywords[1]}" "{keywords[2]}"'
        queries.append(kw3q)
        queries.append(f"{kw3q} MSN")
        queries.append(f"{kw3q} GIGAパーク")
        queries.append(f"{kw3q} ニコニコニュース")
        queries.append(f"{kw3q} チバテレ")
        queries.append(f"{kw3q} NewsPicks")
        queries.append(f"{kw3q} SmartNews")
        queries.append(f"site:news.mixi.jp {kw3}")
        queries.append(f"site:newspicks.com {kw3}")
        queries.append(f"site:smartnews.com {kw3}")
        queries.append(f"site:news.jp {kw3}")
        queries.append(f"site:news.nicovideo.jp {kw3}")
        queries.append(f"site:www.chiba-tv.com {kw3}")

    # ── 中位・後位KW（タイトル後半の固有名詞カバー）──
    # keywords[0-1]が汎用語の場合、後位の具体的KWで補完
    if len(keywords) >= 4:
        kw2b = f"{keywords[2]} {keywords[3]}"
        kw2bq = f'"{keywords[2]}" "{keywords[3]}"'
        queries.append(kw2bq)
        # broadcast クエリ（タイトル前半が汎用語の記事で主力クエリとなる）
        queries.append(f"{kw2bq} 共同通信")
        queries.append(f"{kw2bq} ニュース")
        queries.append(f"{kw2bq} MSN")
        queries.append(f"{kw2bq} GIGAパーク")
        queries.append(f"{kw2bq} ニコニコニュース")
        queries.append(f"{kw2bq} チバテレ")
        queries.append(f"{kw2bq} NewsPicks")
        queries.append(f"{kw2bq} SmartNews")
        queries.append(f"{kw2bq} NEWSjp")
        queries.append(f"{kw2bq} mixi")
        queries.append(f"{kw2bq} PR TIMES")
        # site: クエリ
        queries.append(f"site:news.mixi.jp {kw2b}")
        queries.append(f"site:newspicks.com {kw2b}")
        queries.append(f"site:smartnews.com {kw2b}")
        queries.append(f"site:news.jp {kw2b}")
        queries.append(f"site:news.nicovideo.jp {kw2b}")
        queries.append(f"site:www.chiba-tv.com {kw2b}")
        queries.append(f"site:prtimes.jp {kw2b}")
        queries.append(f"site:at-press.net {kw2b}")
        queries.append(f"site:finance.yahoo.co.jp {kw2b}")
    if len(keywords) >= 6:
        kw2c = f"{keywords[4]} {keywords[5]}"
        kw2cq = f'"{keywords[4]}" "{keywords[5]}"'
        queries.append(kw2cq)
        queries.append(f"site:news.mixi.jp {kw2c}")
        queries.append(f"site:newspicks.com {kw2c}")
        queries.append(f"site:smartnews.com {kw2c}")
        queries.append(f"site:news.jp {kw2c}")
        queries.append(f"site:news.nicovideo.jp {kw2c}")
        queries.append(f"site:www.chiba-tv.com {kw2c}")
        queries.append(f"site:prtimes.jp {kw2c}")
        queries.append(f"site:at-press.net {kw2c}")
        queries.append(f"{kw2cq} NewsPicks")
        queries.append(f"{kw2cq} SmartNews")

    if short_core:
        queries.append(f'"{short_core}" NewsPicks')
        queries.append(f'"{short_core}" SmartNews')
        queries.append(f'"{short_core}" NEWSjp')
        queries.append(f'"{short_core}" mixi')

    # ── 長尺KW単体クエリ（4文字以上の固有名詞候補は単体でも特定性が高い）──
    # 例: "住宅用火災警報器"(8字)・"東京青年会議所"(7字)・"ウェルネスチャレンジ"(10字)
    # は単体でPR配信サイトを検索するだけで記事を特定できる
    long_kws = [kw for kw in keywords if len(kw) >= 4][:4]
    for lkw in long_kws:
        queries.append(f'"{lkw}" 共同通信')
        queries.append(f"site:prtimes.jp {lkw}")
        queries.append(f"site:at-press.net {lkw}")
    if long_kws:
        # 最長KWは単体でも全サイト検索（最も固有性が高い）
        best = max(long_kws, key=len)
        queries.append(f'"{best}"')
        queries.append(f'"{best}" GIGAパーク')
        queries.append(f'"{best}" ニコニコニュース')
        queries.append(f'"{best}" NewsPicks')
        queries.append(f'"{best}" SmartNews')
        queries.append(f"site:finance.yahoo.co.jp {best}")
        queries.append(f"site:dreamnews.jp {best}")

    # ── 英数字KW補助（英数字が2個以上の記事のみ追加）──
    if len(alpha_kws) >= 2:
        ak2 = f"{alpha_kws[0]} {alpha_kws[1]}"
        ak2q = f'"{alpha_kws[0]}" "{alpha_kws[1]}"'
        queries.append(ak2q)
        queries.append(f"{ak2q} NewsPicks")
        queries.append(f"{ak2q} SmartNews")
        queries.append(f"{ak2q} mixi")
        queries.append(f"{ak2q} NEWSjp")
        queries.append(f"{ak2q} ニコニコニュース")
        queries.append(f"{ak2q} チバテレ")
        queries.append(f"site:news.mixi.jp {ak2}")
        queries.append(f"site:newspicks.com {ak2}")
        queries.append(f"site:smartnews.com {ak2}")
        queries.append(f"site:news.jp {ak2}")

    if len(alpha_kws) >= 3:
        ak3 = f"{alpha_kws[0]} {alpha_kws[1]} {alpha_kws[2]}"
        queries.append(f"site:newspicks.com {ak3}")
        queries.append(f"site:smartnews.com {ak3}")
        queries.append(f"site:news.jp {ak3}")
        queries.append(f"site:news.nicovideo.jp {ak3}")

    # source_id系
    if source_id:
        queries.append(source_id)
        queries.append(f"O{source_id}")
        queries.append(f"Ovo_{source_id}")
        queries.append(f"ovo_O{source_id}")
        queries.append(f'"O{source_id}" GIGAパーク')
        queries.append(f"site:news.mixi.jp O{source_id}")

    return dedupe_strings(queries)


def build_priority_site_queries(source_title, keywords, source_id):
    from utils import extract_alpha_keywords
    priority_media = [
        "KK KYODO NEWS SITE", "MSNニュース", "antenna", "Yahooニュース",
        "47NEWS", "GIGAパーク", "ニコニコニュース", "チバテレ",
        "NewsPicks", "NEWSjp", "SmartNews",
    ]
    queries = build_site_queries_for_media(source_title, keywords, source_id, priority_media)
    alpha_kws = extract_alpha_keywords(source_title, max_count=4)
    core = title_core(source_title)
    short_core = core if len(core) <= 25 else ""

    # 通常KW2〜3語のsite:クエリ（全記事共通）
    if len(keywords) >= 2:
        kw2 = f"{keywords[0]} {keywords[1]}"
        queries.extend([
            f"site:www.kyodo.co.jp {kw2}",
            f"site:www.msn.com {kw2}",
            f"site:news.yahoo.co.jp {kw2}",
            f"site:www.47news.jp {kw2}",
            f"site:antenna.jp {kw2}",
            f"site:news.nicovideo.jp {kw2}",
            f"site:www.chiba-tv.com {kw2}",
            f"site:newspicks.com {kw2}",
            f"site:news.jp {kw2}",
            f"site:news.mixi.jp {kw2}",
            f"site:smartnews.com {kw2}",
            f"site:www.smartnews.com {kw2}",
            f"site:www.giga.co.jp {kw2}",
        ])

    if len(keywords) >= 3:
        kw3 = f"{keywords[0]} {keywords[1]} {keywords[2]}"
        queries.extend([
            f"site:www.kyodo.co.jp {kw3}",
            f"site:www.msn.com {kw3}",
            f"site:news.yahoo.co.jp {kw3}",
            f"site:www.47news.jp {kw3}",
            f"site:antenna.jp {kw3}",
            f"site:news.nicovideo.jp {kw3}",
            f"site:www.chiba-tv.com {kw3}",
            f"site:newspicks.com {kw3}",
            f"site:news.jp {kw3}",
            f"site:news.mixi.jp {kw3}",
            f"site:smartnews.com {kw3}",
        ])

    # 中位・後位KW（タイトル後半の固有名詞カバー）
    if len(keywords) >= 4:
        kw2b = f"{keywords[2]} {keywords[3]}"
        queries.extend([
            f"site:www.msn.com {kw2b}",
            f"site:news.yahoo.co.jp {kw2b}",
            f"site:www.47news.jp {kw2b}",
            f"site:news.nicovideo.jp {kw2b}",
            f"site:www.chiba-tv.com {kw2b}",
            f"site:newspicks.com {kw2b}",
            f"site:news.jp {kw2b}",
            f"site:news.mixi.jp {kw2b}",
            f"site:smartnews.com {kw2b}",
            f"site:prtimes.jp {kw2b}",
            f"site:at-press.net {kw2b}",
            f"site:finance.yahoo.co.jp {kw2b}",
            f"site:www.giga.co.jp {kw2b}",
            f"site:www.excite.co.jp {kw2b}",
            f"site:news.infoseek.co.jp {kw2b}",
        ])
    if len(keywords) >= 6:
        kw2c = f"{keywords[4]} {keywords[5]}"
        queries.extend([
            f"site:newspicks.com {kw2c}",
            f"site:news.jp {kw2c}",
            f"site:news.mixi.jp {kw2c}",
            f"site:smartnews.com {kw2c}",
            f"site:news.nicovideo.jp {kw2c}",
            f"site:www.chiba-tv.com {kw2c}",
        ])

    # 長尺KW単体クエリ（4文字以上: 住宅用火災警報器・東京青年会議所 等は単体で記事を特定できる）
    long_kws_p = [kw for kw in keywords if len(kw) >= 4][:4]
    for lkw in long_kws_p:
        queries.extend([
            f"site:prtimes.jp {lkw}",
            f"site:at-press.net {lkw}",
            f"site:dreamnews.jp {lkw}",
        ])
    if long_kws_p:
        best_p = max(long_kws_p, key=len)
        queries.extend([
            f"site:www.msn.com {best_p}",
            f"site:news.yahoo.co.jp {best_p}",
            f"site:www.47news.jp {best_p}",
            f"site:finance.yahoo.co.jp {best_p}",
        ])

    # 英数字KW補助（英数字が2個以上の記事のみ）
    if len(alpha_kws) >= 2:
        ak2 = f"{alpha_kws[0]} {alpha_kws[1]}"
        queries.extend([
            f"site:newspicks.com {ak2}",
            f"site:news.jp {ak2}",
            f"site:news.mixi.jp {ak2}",
            f"site:smartnews.com {ak2}",
            f"site:news.nicovideo.jp {ak2}",
            f"site:www.chiba-tv.com {ak2}",
        ])
    if len(alpha_kws) >= 3:
        ak3 = f"{alpha_kws[0]} {alpha_kws[1]} {alpha_kws[2]}"
        queries.extend([
            f"site:newspicks.com {ak3}",
            f"site:news.jp {ak3}",
            f"site:smartnews.com {ak3}",
        ])

    if short_core:
        queries.extend([
            f'site:www.kyodo.co.jp "{short_core}"',
            f'site:www.msn.com "{short_core}"',
            f'site:www.47news.jp "{short_core}"',
            f'site:newspicks.com "{short_core}"',
            f'site:news.jp "{short_core}"',
        ])

    if source_id:
        queries.extend([
            f'site:www.giga.co.jp "O{source_id}"',
            f'site:news.infoseek.co.jp "ovo_O{source_id}"',
            f'site:topics.smt.docomo.ne.jp "O{source_id}"',
            f'site:www.excite.co.jp "Ovo_{source_id}"',
        ])
    return dedupe_strings(queries)


# ─────────────────────────────────────────────
# 検索バッチ
# ─────────────────────────────────────────────

def run_search_batch(queries, use_google=True, max_workers=12, per_query_results=12):
    # search_bing / search_yahoo / search_google はすべて robots.txt + 利用規約で禁止されているため
    # 呼び出しを停止（HTTPリクエストを送らない）。
    # 公式 Bing News Search API (crawl_bing_news_api) を使用してください。
    return []


def crawl_brave_news_api(source_title, api_key, override_keywords=None):
    """
    Brave Search API（ニュース検索）で転載記事を探す。
    利用規約準拠。無料枠: 2,000リクエスト/月。
    取得方法: https://api.search.brave.com/ でアカウント作成 → APIキー発行。
    """
    if not api_key:
        return []
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return []

    compare_title = _make_compare_title(source_title, override_keywords)
    compare_core = title_core(compare_title)

    clean_core = re.sub(r"^[「」『』【】（）()\[\]〔〕\s]+", "", title_core(compare_title))
    prefix18 = clean_core[:18] if clean_core else ""

    if override_keywords:
        base_terms = list(override_keywords)[:2]
    else:
        kws = extract_keywords(source_title)
        base_terms = [kw for kw in kws if len(kw) >= 3][:2]

    queries = []
    if prefix18:
        queries.append(f'"{prefix18}" 共同通信')
        queries.append(f'"{prefix18}"')
    for term in base_terms:
        queries.append(f"{term} 共同通信")
    queries = dedupe_strings(queries)

    api_headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    for query in queries[:4]:
        try:
            resp = SESSION.get(
                "https://api.search.brave.com/res/v1/news/search",
                params={"q": query, "country": "JP", "search_lang": "jp", "count": 20},
                headers=api_headers,
                timeout=12,
            )
            data = resp.json()
        except Exception:
            continue
        for item in (data.get("results") or []):
            url = safe_str(item.get("url"))
            title = safe_str(item.get("title"))
            if not url or not title:
                continue
            key = canonicalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            ts = max(sim(title_core(title), compare_core), sim(title, compare_title))
            kw_score = keyword_overlap(title, compare_title)
            if ts < 0.36 and kw_score < 0.16:
                continue
            out.append(_result_item(url, title, "", "brave_news_api", len(out) + 1))
            if len(out) >= 25:
                return out
        if len(out) >= 15:
            break

    return out


def dedupe_results(results):
    by_url = {}
    for row in sorted(results, key=_stable_result_sort_key):
        url = safe_str(row.get("canonical_url")) or canonicalize_url(row.get("url", ""))
        if not url:
            continue
        if url not in by_url:
            by_url[url] = row
            continue
        current = by_url[url]
        cur_key = (
            -int(current.get("rank", 9999) or 9999),
            len(safe_str(current.get("title"))),
            1 if safe_str(current.get("snippet")) else 0,
            safe_str(current.get("engine")),
        )
        new_key = (
            -int(row.get("rank", 9999) or 9999),
            len(safe_str(row.get("title"))),
            1 if safe_str(row.get("snippet")) else 0,
            safe_str(row.get("engine")),
        )
        if new_key > cur_key:
            by_url[url] = row
    return sorted(by_url.values(), key=_stable_result_sort_key)


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def search_all(source_ctx):
    source_title = safe_str(source_ctx.get("title"))
    source_text = safe_str(source_ctx.get("text"))
    source_id = safe_str(source_ctx.get("source_id")) or extract_source_id(source_ctx.get("url", ""), source_title, source_text)

    # 手動キーワードを先頭に置いて自動抽出より優先させる
    manual_kws = [safe_str(k) for k in (source_ctx.get("manual_keywords") or []) if safe_str(k)]
    title_keywords = extract_keywords(source_title)
    text_keywords = extract_keywords(source_text[:900])
    keywords = dedupe_strings(manual_kws + title_keywords + text_keywords)[:8]
    # クローラーに渡すoverride KW構築
    # 手動KWがあれば優先。なければタイトル前半＋2文字以上のKWを渡す。
    # （デフォルトの自動抽出は len>=3 フィルターで「氷山」「南極」等の2文字KWが落ちるため精度が下がる）
    if manual_kws:
        override_kws = manual_kws
    else:
        clean_core = re.sub(r"^[「」『』【】（）()\[\]〔〕\s]+", "", title_core(source_title))
        prefix12 = clean_core[:12] if clean_core else ""
        kw2plus = [kw for kw in keywords if len(kw) >= 2][:3]
        auto_terms = dedupe_strings(([prefix12] if prefix12 else []) + kw2plus)[:3]
        override_kws = auto_terms if auto_terms else None

    results = []

    for url in build_all_direct_candidates(source_id):
        results.append(_result_item(url, "", "", "direct", 0))

    # Brave Search API キー（app.py または環境変数 BRAVE_API_KEY から取得）
    brave_api_key = source_ctx.get("_brave_api_key", "") or ""

    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [
            # 直接URL生成クローラ（source_idベース）
            executor.submit(crawl_excite_ovo_source, source_id, 40),
            executor.submit(crawl_infoseek_direct, source_id),
            executor.submit(crawl_dmenu_all_categories, source_id),
            executor.submit(crawl_misc_direct_patterns, source_id),
            executor.submit(crawl_mixi_ovo_source, source_id, 30),
            # 各ニュースサイト自身の内部検索（タイトルベース・外部検索エンジン非依存）
            executor.submit(crawl_yahoo_news_search, source_title, override_kws),
            executor.submit(crawl_nicovideo_news_search, source_title, override_kws),
            # goo/livedoor/47news/antenna はJS描画またはエンドポイント廃止のため除外
        ]
        # Brave Search API（APIキーがある場合のみ実行・任意）
        if brave_api_key:
            futures.append(executor.submit(crawl_brave_news_api, source_title, brave_api_key, override_kws))
        # 検索インデックスされにくい媒体の専用クローラ（PR TIMES/@Press/DreamNews含む）
        try:
            from media_crawlers import crawl_all_target_medias
            futures.append(executor.submit(crawl_all_target_medias, source_title, override_kws))
        except Exception:
            pass
        for future in as_completed(futures):
            try:
                results.extend(future.result() or [])
            except Exception:
                pass

    results = dedupe_results(results)

    # run_search_batch は Bing/Yahoo/Google の一般検索を呼ぶが、
    # すべて robots.txt + 利用規約で禁止されているため呼び出しを廃止。
    # 公式 Bing News API (上記) を代替として使用してください。

    return sorted(results, key=_stable_result_sort_key)