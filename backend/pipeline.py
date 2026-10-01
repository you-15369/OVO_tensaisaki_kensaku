"""転載URL抽出パイプライン（旧 app.py の Streamlit 非依存部分）"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from judge import is_false_positive, judge_candidate
from scraper import fetch_article, is_not_found_page, probe_url_status, soft_validate_candidate
from search import search_all
from utils import (
    canonicalize_url,
    classify_media,
    detect_source_mode,
    extract_source_id,
    is_probable_article_url,
    media_sort_key,
    safe_str,
    title_core,
)

MAX_RESULTS = 30

TRUSTED_FETCH_SKIP_MEDIA = {
    "KK KYODO NEWS SITE",
    "GIGAパーク",
    "ニコニコニュース",
    "チバテレ",
    "Yahooニュース",
    "MSNニュース",
    "47NEWS",
    "ドコモｄメニューニュース",
    "楽天インフォシークニュース",
    "エキサイトニュース",
    "ニフティニュース",
    "gooニュース",
    "ビッグローブニュース",
    "ライブドアニュース",
    "アメーバニュース",
    "mixiニュース",
    "めるもニュース",
    "Gunosy",
    "OKMusic",
    "ビルボードジャパン",
    "ウレぴあ総研",
    "LIMIA",
    "モデルプレス",
    "NewsPicks",
    "NEWSjp",
    "SmartNews",
    "PRタイムス",
    "アットプレス",
    "ドリームニュース",
    "Yahooファイナンス",
}

TITLE_OPTIONAL_MEDIA = {
    "GIGAパーク",
    "ニコニコニュース",
    "チバテレ",
    "エキサイトニュース",
    "ドコモｄメニューニュース",
    "楽天インフォシークニュース",
    "gooニュース",
    "ビッグローブニュース",
    "ニフティニュース",
    "ライブドアニュース",
    "アメーバニュース",
    "めるもニュース",
    "mixiニュース",
    "SmartNews",
    "NEWSjp",
    "NewsPicks",
    "47NEWS",
    "KK KYODO NEWS SITE",
    "MSNニュース",
    "Yahooニュース",
}


def build_source_context(url, origin_label, manual_title="", manual_keywords=None, bing_api_key=""):
    url = safe_str(url)
    if not url:
        return None

    art = fetch_article(url)
    fetched_title = safe_str(art.get("title"))
    # 手動入力タイトルが指定されている場合は常に優先使用
    # （OVOのbizページはJS描画でタイトルが取れないケースがあるため）
    title = safe_str(manual_title) if manual_title else fetched_title
    text = safe_str(art.get("text"))
    source_id = safe_str(art.get("source_id")) or extract_source_id(url, title, text)

    return {
        "origin": origin_label,
        "url": url,
        "canonical_url": canonicalize_url(url),
        "title": title,
        "title_core": title_core(title),
        "text": text,
        "media": classify_media(url, title),
        "source_id": source_id,
        "manual_keywords": list(manual_keywords) if manual_keywords else [],
        "source_mode": detect_source_mode(url),
        "published_at": art.get("published_at", ""),
        "raw": art,
        "_brave_api_key": bing_api_key or "",
    }


def normalize_candidate(item, origin=""):
    url = safe_str(item.get("url"))
    title = safe_str(item.get("title"))
    snippet = safe_str(item.get("snippet"))
    media = safe_str(item.get("media")) or classify_media(url, title)

    return {
        "url": url,
        "canonical_url": canonicalize_url(url),
        "title": title,
        "snippet": snippet,
        "text": "",
        "engine": safe_str(item.get("engine")),
        "rank": item.get("rank", 9999),
        "media": media,
        "origin": origin,
        "status_code": item.get("status_code"),
    }


def stable_row_sort_key(row):
    return (
        media_sort_key(row.get("media", "")),
        -(float(row.get("score", 0.0) or 0.0)),
        -(float(row.get("title_similarity", 0.0) or 0.0)),
        int(row.get("rank", 9999) or 9999),
        safe_str(row.get("canonical_url") or row.get("url", "")),
    )


def build_search_signature(source_contexts):
    parts = []
    for ctx in sorted(source_contexts, key=lambda x: x.get("origin", "")):
        sig = safe_str(ctx.get("source_id")) or safe_str(ctx.get("canonical_url")) or safe_str(ctx.get("url"))
        parts.append(f"{ctx.get('origin', '')}::{sig}")
    return "||".join(parts)


def quick_pre_filter(cand):
    url = safe_str(cand.get("url"))
    title = safe_str(cand.get("title"))
    media = safe_str(cand.get("media"))

    if not url.startswith("http"):
        return False
    if media in {"Twitter", "Facebook", "ソネットニュース"}:
        return False
    if "/images/" in url or "/photo/" in url or "/photos/" in url:
        return False
    if media == "antenna" and "/brands/" in url:
        return False
    if not title and media not in TITLE_OPTIONAL_MEDIA:
        return False
    if media in {"GIGAパーク", "ニコニコニュース", "チバテレ", "NewsPicks", "NEWSjp", "SmartNews"} and is_probable_article_url(url, media):
        return True
    return True


# フェッチ後にリダイレクトしても元のURLを保持するメディア
# SmartNews: 短縮URL → 各社記事にリダイレクト
# mixi: ログイン要求でリダイレクトする可能性
# NewsPicks/NEWSjp: ログイン壁でリダイレクトする可能性
_REDIRECT_ONLY_MEDIA = {"SmartNews", "Googleニュース", "LINE News", "mixiニュース", "NewsPicks", "NEWSjp"}


def enrich_candidate_if_needed(cand):
    title = safe_str(cand.get("title"))
    media = safe_str(cand.get("media"))
    url = safe_str(cand.get("url"))
    engine = safe_str(cand.get("engine", ""))
    is_direct = engine.startswith("direct")

    if not is_probable_article_url(url, media):
        return cand

    should_fetch = False
    if not title:
        should_fetch = True
    elif len(title) < 12 and not is_direct:
        should_fetch = True
    elif is_direct:
        # direct候補は常にフェッチして存在確認
        should_fetch = True

    # 検索ヒット候補でタイトルが十分ある場合はフェッチ不要（高速化）
    if not is_direct and media in TRUSTED_FETCH_SKIP_MEDIA and not should_fetch:
        return cand

    if not should_fetch:
        return cand

    art = fetch_article(url)
    if art.get("title"):
        cand["title"] = art["title"]
    if art.get("text"):
        cand["text"] = art["text"]

    # SmartNews等のリダイレクト系メディアはURLを上書きしない
    # （l.smartnews.com → OVO元記事 にリダイレクトしても、URLはSmartNewsのままにする）
    final_url = art.get("final_url", "")
    if final_url and final_url != url:
        final_media = classify_media(final_url, "")
        is_redirect_to_source = final_media in {"OVO", "ビードット", "エンタメOVO"}
        if not is_redirect_to_source and media not in _REDIRECT_ONLY_MEDIA:
            cand["url"] = final_url
            cand["canonical_url"] = canonicalize_url(final_url)
            if not cand.get("media") or cand.get("media") == "その他":
                cand["media"] = art["media"]
        elif not is_redirect_to_source:
            # リダイレクト系だがソース記事でない場合もURLは保持
            pass

    cand["status_code"] = art.get("status_code")
    cand["content_type"] = art.get("content_type")
    cand["fetched"] = True
    cand["is_not_found"] = art.get("is_not_found", False)
    return cand


def ensure_non_404(cand):
    status_code = cand.get("status_code")
    title = safe_str(cand.get("title"))
    text = safe_str(cand.get("text"))
    url = safe_str(cand.get("url"))
    media = safe_str(cand.get("media"))
    score = float(cand.get("score", 0.0) or 0.0)
    engine = safe_str(cand.get("engine", ""))
    is_direct = engine.startswith("direct")

    # タイトル・テキストから明らかに404とわかる場合
    if is_not_found_page(status_code, title, text):
        cand["is_not_found"] = True
        return cand

    # 検索エンジン（bing/google/yahoo）でヒットした候補は存在確認済みとみなす
    # direct生成候補のみHTTPチェックを実施
    if not is_direct and status_code is None:
        cand["is_not_found"] = False
        return cand

    if status_code is None and url.startswith("http"):
        probe = probe_url_status(url, timeout=5)
        cand["status_code"] = probe.get("status_code")
        if probe.get("final_url"):
            cand["url"] = probe["final_url"]
            cand["canonical_url"] = canonicalize_url(probe["final_url"])
            if not cand.get("media") or cand.get("media") == "その他":
                cand["media"] = classify_media(cand["url"], cand.get("title", ""))
        cand["content_type"] = probe.get("content_type")
        cand["is_not_found"] = is_not_found_page(cand.get("status_code"), title, text)

    return cand


def merge_same_url(rows):
    bucket = {}
    for row in sorted(rows, key=stable_row_sort_key):
        key = row.get("canonical_url") or canonicalize_url(row.get("url", ""))
        if not key:
            continue
        if key not in bucket:
            bucket[key] = {**row, "origins": set(row.get("matched_origins", []) or [])}
            if row.get("origin"):
                bucket[key]["origins"].add(row["origin"])
            continue

        cur = bucket[key]
        if row.get("origin"):
            cur["origins"].add(row["origin"])
        for x in row.get("matched_origins", []) or []:
            if x:
                cur["origins"].add(x)

        cur_score = float(cur.get("score", 0.0) or 0.0)
        new_score = float(row.get("score", 0.0) or 0.0)
        if new_score > cur_score:
            keep_origins = cur["origins"]
            keep_ids = sorted(set((cur.get("matched_source_ids") or []) + (row.get("matched_source_ids") or [])))
            bucket[key] = {**row, "origins": keep_origins, "matched_source_ids": keep_ids}
        else:
            cur["matched_source_ids"] = sorted(set((cur.get("matched_source_ids") or []) + (row.get("matched_source_ids") or [])))

    out = []
    for value in bucket.values():
        origins = sorted(list(value.get("origins", set())))
        value["origins"] = origins
        value["origin_label"] = ",".join(origins)
        out.append(value)
    return sorted(out, key=stable_row_sort_key)


def merge_same_story(rows):
    story_bucket = {}
    passthrough = []

    for row in sorted(rows, key=stable_row_sort_key):
        media = safe_str(row.get("media"))
        title_key = title_core(row.get("title", ""))
        matched_ids = row.get("matched_source_ids") or []
        story_id = matched_ids[0] if matched_ids else safe_str(extract_source_id(row.get("url", ""), row.get("title", ""), row.get("text", "")))

        if story_id and media:
            key = f"{media}::id::{story_id}"
        elif media and title_key:
            key = f"{media}::title::{title_key}"
        else:
            passthrough.append(row)
            continue

        cur = story_bucket.get(key)
        if cur is None:
            story_bucket[key] = row
            continue

        cur_key = (
            float(cur.get("score", 0.0) or 0.0),
            float(cur.get("title_similarity", 0.0) or 0.0),
            -int(cur.get("rank", 9999) or 9999),
            len(safe_str(cur.get("title"))),
        )
        new_key = (
            float(row.get("score", 0.0) or 0.0),
            float(row.get("title_similarity", 0.0) or 0.0),
            -int(row.get("rank", 9999) or 9999),
            len(safe_str(row.get("title"))),
        )
        if new_key > cur_key:
            story_bucket[key] = row

    return sorted(passthrough + list(story_bucket.values()), key=stable_row_sort_key)


def keep_top_results(rows, max_count=MAX_RESULTS):
    best_by_media = {}
    for row in sorted(rows, key=stable_row_sort_key):
        media = row.get("media", "その他")
        cur = best_by_media.get(media)
        if cur is None:
            best_by_media[media] = row
            continue
        cur_key = (
            float(cur.get("score", 0.0) or 0.0),
            float(cur.get("title_similarity", 0.0) or 0.0),
            -int(cur.get("rank", 9999) or 9999),
            -(len(safe_str(cur.get("title")))),
        )
        new_key = (
            float(row.get("score", 0.0) or 0.0),
            float(row.get("title_similarity", 0.0) or 0.0),
            -int(row.get("rank", 9999) or 9999),
            -(len(safe_str(row.get("title")))),
        )
        if new_key > cur_key:
            best_by_media[media] = row

    primary = sorted(best_by_media.values(), key=stable_row_sort_key)

    if len(primary) >= max_count:
        return primary[:max_count]

    used_urls = {r.get("canonical_url") or canonicalize_url(r.get("url", "")) for r in primary}
    extras = [
        r for r in sorted(rows, key=stable_row_sort_key)
        if (r.get("canonical_url") or canonicalize_url(r.get("url", ""))) not in used_urls
    ]

    combined = primary + extras
    seen = set()
    result = []
    for row in combined:
        key = row.get("canonical_url") or canonicalize_url(row.get("url", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
        if len(result) >= max_count:
            break
    return sorted(result, key=stable_row_sort_key)


def merge_with_previous_cache(rows, previous_rows):
    valid_previous = [
        row for row in (previous_rows or [])
        if int(row.get("status_code", 200) or 200) not in {404, 410}
    ]
    merged = merge_same_url((rows or []) + valid_previous)
    merged = merge_same_story(merged)
    return keep_top_results(merged, max_count=MAX_RESULTS)


def process_candidate(cand, source_contexts):
    if not quick_pre_filter(cand):
        return None, {**cand, "reject_reason": "quick_pre_filter", "score": 0.0, "level": "対象外"}

    cand = enrich_candidate_if_needed(cand)
    judged = judge_candidate(source_contexts, cand)
    judged["status_code"] = cand.get("status_code")
    judged["text"] = cand.get("text", "")

    if judged.get("score", 0.0) >= 0.42 or judged.get("matched_source_ids") or safe_str(judged.get("media")) in TRUSTED_FETCH_SKIP_MEDIA:
        judged = ensure_non_404(judged)
        if judged.get("is_not_found"):
            return None, {**judged, "reject_reason": "404_not_found"}

    if not soft_validate_candidate(judged):
        return None, {**judged, "reject_reason": "soft_validate_false"}

    if is_false_positive(judged):
        return None, {**judged, "reject_reason": "false_positive"}

    if judged.get("status_code") in {404, 410}:
        return None, {**judged, "reject_reason": "404_not_found"}

    return judged, None


def run_pipeline(ovo_url, bdot_url, debug_mode=False, manual_title="", manual_keywords=None, bing_api_key=""):
    source_contexts = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        if safe_str(ovo_url):
            futures.append(executor.submit(build_source_context, ovo_url, "OVO", manual_title, manual_keywords, bing_api_key))
        if safe_str(bdot_url):
            futures.append(executor.submit(build_source_context, bdot_url, "b-dot", manual_title, manual_keywords, bing_api_key))
        for future in as_completed(futures):
            try:
                ctx = future.result()
                if ctx:
                    source_contexts.append(ctx)
            except Exception:
                pass

    source_contexts.sort(key=lambda x: x.get("origin", ""))
    if not source_contexts:
        return [], [], []

    source_canonical_urls = {ctx["canonical_url"] for ctx in source_contexts if ctx.get("canonical_url")}
    raw_candidates = []

    with ThreadPoolExecutor(max_workers=min(2, len(source_contexts))) as executor:
        search_jobs = {executor.submit(search_all, ctx): ctx for ctx in source_contexts}
        for future in as_completed(search_jobs):
            ctx = search_jobs[future]
            try:
                items = future.result() or []
            except Exception:
                items = []
            for item in items:
                cand = normalize_candidate(item, origin=ctx["origin"])
                key = cand.get("canonical_url") or canonicalize_url(cand.get("url", ""))
                if not key or key in source_canonical_urls:
                    continue
                raw_candidates.append(cand)

    pre_dedup = {}
    for cand in sorted(raw_candidates, key=lambda x: (
        safe_str(x.get("canonical_url") or x.get("url", "")),
        safe_str(x.get("media", "")),
        int(x.get("rank", 9999) or 9999),
        -len(safe_str(x.get("title", ""))),
    )):
        key = cand.get("canonical_url") or canonicalize_url(cand.get("url", ""))
        if not key:
            continue
        if key not in pre_dedup:
            pre_dedup[key] = cand
        else:
            cur = pre_dedup[key]
            cur_key = (len(safe_str(cur.get("title"))), -int(cur.get("rank", 9999) or 9999))
            new_key = (len(safe_str(cand.get("title"))), -int(cand.get("rank", 9999) or 9999))
            if new_key > cur_key:
                pre_dedup[key] = cand

    deduped_candidates = sorted(
        list(pre_dedup.values()),
        key=lambda x: (
            safe_str(x.get("media", "")),
            safe_str(x.get("canonical_url") or x.get("url", "")),
            int(x.get("rank", 9999) or 9999),
        ),
    )

    accepted = []
    rejected = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(process_candidate, cand, source_contexts): cand for cand in deduped_candidates}
        for future in as_completed(future_map):
            try:
                ok_row, ng_row = future.result()
                if ok_row:
                    accepted.append(ok_row)
                if ng_row and debug_mode:
                    rejected.append(ng_row)
            except Exception:
                pass

    merged = merge_same_url(accepted)
    merged = merge_same_story(merged)
    final_rows = keep_top_results(merged, max_count=MAX_RESULTS)

    if debug_mode:
        rejected.sort(key=lambda x: (
            -float(x.get("score", 0.0) or 0.0),
            safe_str(x.get("media", "")),
            safe_str(x.get("canonical_url") or x.get("url", "")),
        ))

    return source_contexts, final_rows, rejected
