"""
media_crawlers.py - 検索エンジンに頼らない各媒体の直接クローラ

SSRで記事リンクが取得できる媒体の専用クローラ：
- mixi ニュース（news.mixi.jp）: 共同通信配信(id=201)一覧をクロール
- ニコニコニュース（news.nicovideo.jp）: トップ/カテゴリページをクロール
- チバテレ（www.chiba-tv.com）: RSSフィード/一覧ページをクロール

以下は直接クロール不可のため検索エンジン経由に頼る：
- NewsPicks（newspicks.com）: 認証壁（401）
- SmartNews（smartnews.com）: 記事はJS描画のみ
- NEWSjp（news.jp）: 純SPA（記事リンクなし）
"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from utils import (
    canonicalize_url,
    classify_media,
    extract_keywords,
    keyword_overlap,
    safe_str,
    sim,
    title_core,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _get(url, timeout=10, encoding=None):
    try:
        response = SESSION.get(url, timeout=timeout, allow_redirects=True)
        if encoding:
            response.encoding = encoding
        else:
            response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        if response.status_code != 200:
            return ""
        return response.text
    except Exception:
        return ""


def _item(url, title, engine, rank=9999):
    return {
        "url": url,
        "canonical_url": canonicalize_url(url),
        "title": safe_str(title),
        "snippet": safe_str(title),
        "engine": engine,
        "rank": rank,
        "media": classify_media(url, title),
    }


def _is_title_match(candidate_title, source_title, threshold_sim=0.40, threshold_kw=0.20,
                    override_keywords=None):
    """
    候補タイトルが元記事タイトルと十分似ているか判定。
    source_title が空の場合は override_keywords を代替比較テキストとして使用する。
    """
    candidate_title = safe_str(candidate_title)
    source_title = safe_str(source_title)
    # source_title が空でも override_keywords があれば比較を試みる
    if not source_title and override_keywords:
        source_title = " ".join(override_keywords)
    if not candidate_title or not source_title:
        return False
    ts = max(
        sim(title_core(candidate_title), title_core(source_title)),
        sim(candidate_title, source_title),
    )
    kw = keyword_overlap(candidate_title, source_title)
    return ts >= threshold_sim or kw >= threshold_kw


# ─────────────────────────────────────────────
# mixi ニュース（共同通信配信 id=201）
# ─────────────────────────────────────────────
# 正しいURL: https://news.mixi.jp/list_news_media.pl?id=201
# （media_id=201 ではなく id=201 が正しい！）
# EUC-JP エンコーディング
# 記事URL: /view_news.pl?id=XXX&media_id=201

def crawl_mixi_for_source(source_title, max_pages=10):
    """
    news.mixi.jp/list_news_media.pl?id=201 (OVO配信記事一覧)から
    元記事タイトルと一致する記事を探す。
    1ページ30件、最大10ページ（=300件）を検索。
    """
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title:
        return out

    for page in range(1, max_pages + 1):
        if page == 1:
            url = "https://news.mixi.jp/list_news_media.pl?id=201"
        else:
            url = f"https://news.mixi.jp/list_news_media.pl?page={page}&id=201"

        # mixi は EUC-JP エンコーディング
        html = _get(url, timeout=12, encoding="euc-jp")
        if not html:
            # フォールバック：自動検出
            html = _get(url, timeout=12)
            if not html:
                break

        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='view_news.pl']"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://news.mixi.jp" + href
            elif not href.startswith("http"):
                href = urljoin("https://news.mixi.jp/", href)
            if "view_news.pl" not in href:
                continue
            # media_id=201 が含まれるリンクのみ（OVO配信）
            if "media_id=201" not in href:
                continue

            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue

            # 'NEW' マーカー除去
            title = re.sub(r"\bNEW\b", "", title).strip()

            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)

            if not _is_title_match(title, source_title):
                continue

            out.append(_item(href, title, "mixi_list_match", len(out) + 1))

        if out:
            # 一度でも見つかったら以降のページはスキップ
            break

    return out


# ─────────────────────────────────────────────
# ニコニコニュース（news.nicovideo.jp）
# ─────────────────────────────────────────────
# 確認済み: トップページはSSRで125件以上の記事リンクあり
# URLパターン: /watch/nwDIGITS
# カテゴリはスラッグ不可・数値ID（例: /categories/10）

def crawl_nicovideo_for_source(source_title):
    """ニコニコニュースのトップ/カテゴリ一覧からOVO転載記事を探す"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title:
        return out

    list_urls = [
        "https://news.nicovideo.jp/",
        "https://news.nicovideo.jp/categories/5",   # エンタメ
        "https://news.nicovideo.jp/categories/10",  # 政治・社会
        "https://news.nicovideo.jp/categories/11",  # 経済
        "https://news.nicovideo.jp/categories/12",  # 国際
        "https://news.nicovideo.jp/categories/7",   # 生活・健康
        "https://news.nicovideo.jp/categories/8",   # スポーツ
        "https://news.nicovideo.jp/categories/9",   # 科学・テクノロジー
    ]

    for url in list_urls:
        html = _get(url, timeout=10)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/watch/nw']"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://news.nicovideo.jp" + href
            href = href.split("?")[0]
            if not re.search(r"news\.nicovideo\.jp/watch/nw\d+$", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            if not _is_title_match(title, source_title):
                continue
            out.append(_item(href, title, "nicovideo_list", len(out) + 1))
            if len(out) >= 5:
                return out
        if out:
            break

    return out


# ─────────────────────────────────────────────
# チバテレプラス（www.chiba-tv.com）
# ─────────────────────────────────────────────
# 確認済み: RSS https://www.chiba-tv.com/plus/rss.xml あり
# 一覧ページ /plus/ もSSR（CSS: a[href*='/plus/detail/']）
# URLパターン: /plus/detail/DIGITS

def crawl_chiba_tv_for_source(source_title):
    """チバテレプラスのRSS/一覧からOVO転載記事を探す"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title:
        return out

    rss_html = _get("https://www.chiba-tv.com/plus/rss.xml", timeout=10)
    if rss_html:
        try:
            soup = BeautifulSoup(rss_html, "lxml-xml")
        except Exception:
            soup = BeautifulSoup(rss_html, "lxml")
        for item in soup.find_all("item"):
            link_tag = item.find("link")
            title_tag = item.find("title")
            if not link_tag or not title_tag:
                continue
            href = safe_str(link_tag.string or link_tag.get_text())
            title = safe_str(title_tag.string or title_tag.get_text())
            if not href or not re.search(r"chiba-tv\.com/plus/detail/\d+", href):
                continue
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            if not _is_title_match(title, source_title):
                continue
            out.append(_item(href, title, "chiba_tv_rss", len(out) + 1))
            if len(out) >= 5:
                return out

    if out:
        return out

    # フォールバック：一覧ページ
    html = _get("https://www.chiba-tv.com/plus/", timeout=10)
    if not html:
        return out
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href*='/plus/detail/']"):
        href = a.get("href", "")
        if href.startswith("/"):
            href = "https://www.chiba-tv.com" + href
        if not re.search(r"chiba-tv\.com/plus/detail/\d+", href):
            continue
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 6:
            continue
        key = canonicalize_url(href)
        if key in seen:
            continue
        seen.add(key)
        if not _is_title_match(title, source_title):
            continue
        out.append(_item(href, title, "chiba_tv_list", len(out) + 1))
        if len(out) >= 5:
            break

    return out


# ─────────────────────────────────────────────
# PR TIMES（prtimes.jp）
# ─────────────────────────────────────────────
# 確認済み: https://prtimes.jp/main/html/searchrlp/key/{keyword} はSSR
# 記事URL: /main/html/rd/p/DIGITS.DIGITS.html

def crawl_prtimes_for_source(source_title, override_keywords=None):
    """PR TIMESのキーワード検索からOVO転載記事を探す（検索エンジン非依存）"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    if override_keywords:
        search_targets = list(override_keywords)[:3]
    else:
        keywords = extract_keywords(source_title)
        long_kws = [kw for kw in keywords if len(kw) >= 3][:3]
        search_targets = long_kws if long_kws else keywords[:2]
    if not search_targets:
        return out

    for kw in search_targets:
        search_url = f"https://prtimes.jp/main/html/searchrlp/key/{quote_plus(kw)}"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/main/html/rd/p/']"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://prtimes.jp" + href
            if not re.search(r"prtimes\.jp/main/html/rd/p/\d+\.\d+\.html", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            if not _is_title_match(title, source_title, override_keywords=override_keywords):
                continue
            out.append(_item(href, title, "prtimes_search", len(out) + 1))
            if len(out) >= 5:
                return out
        if out:
            break

    return out


# ─────────────────────────────────────────────
# @Press（atpress.ne.jp）
# ─────────────────────────────────────────────
# 確認済み: https://www.atpress.ne.jp/search?kw={keyword} はSSR
# 記事URL: /news/DIGITS

def crawl_atpress_for_source(source_title, override_keywords=None):
    """@Pressのキーワード検索からOVO転載記事を探す（検索エンジン非依存）"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    if override_keywords:
        search_targets = list(override_keywords)[:3]
    else:
        keywords = extract_keywords(source_title)
        long_kws = [kw for kw in keywords if len(kw) >= 3][:3]
        search_targets = long_kws if long_kws else keywords[:2]
    if not search_targets:
        return out

    for kw in search_targets:
        search_url = f"https://www.atpress.ne.jp/search?kw={quote_plus(kw)}"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.atpress.ne.jp" + href
            if not re.search(r"atpress\.ne\.jp/news/\d+", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            if not _is_title_match(title, source_title, override_keywords=override_keywords):
                continue
            out.append(_item(href, title, "atpress_search", len(out) + 1))
            if len(out) >= 5:
                return out
        if out:
            break

    return out


# ─────────────────────────────────────────────
# ドリームニュース（dreamnews.jp）
# ─────────────────────────────────────────────
# 確認済み: https://www.dreamnews.jp/?action=search_press_release&query={keyword}
# 記事URL: /press/0*DIGITS

def crawl_dreamnews_for_source(source_title, override_keywords=None):
    """ドリームニュースのキーワード検索からOVO転載記事を探す（検索エンジン非依存）"""
    out = []
    seen = set()
    source_title = safe_str(source_title)
    if not source_title and not override_keywords:
        return out

    if override_keywords:
        search_targets = list(override_keywords)[:3]
    else:
        keywords = extract_keywords(source_title)
        long_kws = [kw for kw in keywords if len(kw) >= 3][:3]
        search_targets = long_kws if long_kws else keywords[:2]
    if not search_targets:
        return out

    for kw in search_targets:
        search_url = f"https://www.dreamnews.jp/?action=search_press_release&query={quote_plus(kw)}"
        html = _get(search_url, timeout=12)
        if not html:
            continue
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.dreamnews.jp" + href
            if not re.search(r"dreamnews\.jp/press/0*\d+", href):
                continue
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 6:
                continue
            key = canonicalize_url(href)
            if key in seen:
                continue
            seen.add(key)
            if not _is_title_match(title, source_title, override_keywords=override_keywords):
                continue
            out.append(_item(href, title, "dreamnews_search", len(out) + 1))
            if len(out) >= 5:
                return out
        if out:
            break

    return out


# ─────────────────────────────────────────────
# NewsPicks（newspicks.com）
# ─────────────────────────────────────────────
# 検索は認証壁（401）のため直接クロール不可。
# 検索エンジンクエリ（utils.py build_general_queries）経由で取得。

def crawl_newspicks_for_source(source_title):
    return []


# ─────────────────────────────────────────────
# SmartNews（smartnews.com）
# ─────────────────────────────────────────────
# カテゴリページはJS描画のみで記事リンクなし。直接クロール不可。
# 検索エンジンクエリ（utils.py build_general_queries）経由で取得。

def crawl_smartnews_for_source(source_title):
    return []


# ─────────────────────────────────────────────
# NEWSjp（news.jp）
# ─────────────────────────────────────────────
# 純SPA（Vite）のためraw HTMLに記事リンクなし。直接クロール不可。
# 検索エンジンクエリ（utils.py build_general_queries）経由で取得。

def crawl_newsjp_for_source(source_title):
    return []


# ─────────────────────────────────────────────
# 統合：全ターゲット媒体を並列クロール
# ─────────────────────────────────────────────

def crawl_all_target_medias(source_title, override_keywords=None):
    """
    SSRクロール可能な媒体を並列実行。
    NewsPicks/SmartNews/NEWSjp は直接クロール不可のため検索エンジン経由。
    PR TIMES/@Press/ドリームニュースは各サイトの検索エンドポイントを直接叩く。
    override_keywords が指定された場合は検索クエリに優先使用する。
    """
    results = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            # mixi/nicovideo/chiba-tvはリスト/RSSクロールなので override_keywords 不要
            executor.submit(crawl_mixi_for_source, source_title),
            executor.submit(crawl_nicovideo_for_source, source_title),
            executor.submit(crawl_chiba_tv_for_source, source_title),
            # キーワード検索型クローラーには override_keywords を渡す
            executor.submit(crawl_prtimes_for_source, source_title, override_keywords),
            executor.submit(crawl_atpress_for_source, source_title, override_keywords),
            executor.submit(crawl_dreamnews_for_source, source_title, override_keywords),
        ]
        for future in as_completed(futures):
            try:
                results.extend(future.result() or [])
            except Exception:
                pass
    return results
