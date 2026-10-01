from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from utils import classify_media, extract_source_id, is_trusted_media, safe_str, title_core

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

NOT_FOUND_PATTERNS = [
    "404 not found",
    "not found",
    "ページが見つかりません",
    "お探しのページは見つかりませんでした",
    "ご指定のページが見つかりません",
]


def resolve_google_wrapper(url):
    url = safe_str(url)
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if "google." in parsed.netloc and parsed.path.startswith("/url"):
            query = parse_qs(parsed.query)
            for key in ["q", "url"]:
                if key in query and query[key]:
                    return unquote(query[key][0])
    except Exception:
        pass
    return url


def fetch_html(url, timeout=8):
    url = resolve_google_wrapper(url)
    try:
        response = SESSION.get(url, timeout=timeout, allow_redirects=True)
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "final_url": response.url,
            "html": response.text or "",
            "content_type": response.headers.get("Content-Type", ""),
        }
    except Exception as e:
        return {
            "ok": False,
            "status_code": None,
            "final_url": url,
            "html": "",
            "content_type": "",
            "error": str(e),
        }


def probe_url_status(url, timeout=5):
    url = resolve_google_wrapper(url)
    try:
        response = SESSION.get(url, timeout=timeout, allow_redirects=True, stream=True)
        status_code = response.status_code
        final_url = response.url
        content_type = response.headers.get("Content-Type", "")
        response.close()
        return {
            "status_code": status_code,
            "final_url": final_url,
            "content_type": content_type,
        }
    except Exception:
        return {
            "status_code": None,
            "final_url": url,
            "content_type": "",
        }


def extract_title(soup):
    for tag_name, attrs, attr_key in [
        ("meta", {"property": "og:title"}, "content"),
        ("meta", {"name": "twitter:title"}, "content"),
        ("meta", {"name": "title"}, "content"),
    ]:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get(attr_key):
            return tag.get(attr_key).strip()

    if soup.title and soup.title.text:
        return soup.title.text.strip()

    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)

    return ""


def extract_published_at(soup):
    time_tag = soup.find("time")
    if time_tag:
        if time_tag.get("datetime"):
            return time_tag.get("datetime")
        return time_tag.get_text(" ", strip=True)

    for tag_name, attrs, attr_key in [
        ("meta", {"property": "article:published_time"}, "content"),
        ("meta", {"name": "pubdate"}, "content"),
        ("meta", {"name": "date"}, "content"),
    ]:
        tag = soup.find(tag_name, attrs=attrs)
        if tag and tag.get(attr_key):
            return tag.get(attr_key).strip()
    return ""


def extract_main_text(soup):
    for bad in soup.select("script, style, noscript, header, footer, nav, aside, form"):
        bad.decompose()

    article = soup.find("article")
    if article:
        ps = article.find_all(["p", "h2", "h3", "li"])
        txt = "\n".join([p.get_text(" ", strip=True) for p in ps if p.get_text(" ", strip=True)])
        if len(txt) >= 120:
            return txt

    candidates = []
    for selector in [
        "main",
        ".article",
        ".article-body",
        ".articleBody",
        ".content",
        ".entry-content",
        "#articleBody",
        "#news_textbody",
        ".detailText",
    ]:
        node = soup.select_one(selector)
        if node:
            ps = node.find_all(["p", "h2", "h3", "li"])
            txt = "\n".join([p.get_text(" ", strip=True) for p in ps if p.get_text(" ", strip=True)])
            if txt:
                candidates.append(txt)

    if candidates:
        candidates.sort(key=len, reverse=True)
        return candidates[0]

    ps = soup.find_all("p")
    return "\n".join([p.get_text(" ", strip=True) for p in ps if p.get_text(" ", strip=True)])


def is_not_found_page(status_code=None, title="", text=""):
    if status_code in {404, 410}:
        return True
    hay = f"{safe_str(title)}\n{safe_str(text)[:300]}".lower()
    return any(pattern in hay for pattern in NOT_FOUND_PATTERNS)


def fetch_article(url):
    res = fetch_html(url, timeout=8)
    html = res.get("html", "")
    soup = BeautifulSoup(html, "lxml") if html else BeautifulSoup("", "lxml")

    title = extract_title(soup)
    text = extract_main_text(soup)
    published_at = extract_published_at(soup)
    final_url = res.get("final_url", url)

    media = classify_media(final_url, title)
    source_id = extract_source_id(final_url, title, text)

    return {
        "url": url,
        "final_url": final_url,
        "status_code": res.get("status_code"),
        "content_type": res.get("content_type"),
        "title": title_core(title),
        "raw_title": title,
        "text": text,
        "published_at": published_at,
        "media": media,
        "source_id": source_id,
        "error": res.get("error", ""),
        "is_not_found": is_not_found_page(res.get("status_code"), title, text),
    }


def soft_validate_candidate(candidate):
    url = safe_str(candidate.get("url"))
    media = safe_str(candidate.get("media"))
    score = float(candidate.get("score", 0.0) or 0.0)
    status_code = candidate.get("status_code")
    title = safe_str(candidate.get("title"))
    text = safe_str(candidate.get("text"))

    # 明示的に404/410が確認された場合のみ弾く（status_code=Noneは通過させる）
    if status_code in {404, 410}:
        return False

    # タイトル・テキストから404ページと判定できる場合のみ弾く
    if title and text and is_not_found_page(None, title, text):
        return False

    if media == "antenna" and "antenna.jp/articles/" in url:
        return True

    if is_trusted_media(media) and score >= 0.54:
        return True

    return True