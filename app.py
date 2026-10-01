from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from db import init_db, save_results
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

st.set_page_config(
    page_title="OVO / b-dot 転載URL抽出 v2.6.0",
    page_icon="📰",
    layout="centered",
)

st.markdown(
    """
    <style>
      .block-container {max-width: 920px; padding-top: 1.2rem; padding-bottom: 2rem;}
      .stTextArea textarea {font-size: 13px;}
      .result-card {padding: 0.25rem 0 0.15rem 0;}
      .result-url {font-size: 0.82rem; word-break: break-all;}
      .result-title {font-size: 0.87rem; color: #555;}
    </style>
    """,
    unsafe_allow_html=True,
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


def format_word_output(rows):
    lines = []
    for row in rows:
        lines.append(row.get("media", ""))
        lines.append(row.get("url", ""))
        lines.append("")
    return "\n".join(lines).strip()


def to_dataframe(rows):
    return pd.DataFrame([
        {
            "媒体": r.get("media", ""),
            "転載URL": r.get("url", ""),
            "タイトル": r.get("title", ""),
            "判定": r.get("level", ""),
            "スコア": r.get("score", ""),
            "元記事": r.get("origin_label", ""),
            "タイトル一致": r.get("title_similarity", ""),
            "KW一致": r.get("keyword_overlap", ""),
            "source_id一致": ",".join(r.get("matched_source_ids", [])) if isinstance(r.get("matched_source_ids"), list) else r.get("matched_source_ids", ""),
            "検索エンジン": r.get("engine", ""),
            "順位": r.get("rank", ""),
            "HTTP": r.get("status_code", ""),
            "根拠": r.get("score_reason", ""),
        }
        for r in rows
    ])


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


def clear_exclusion_keys():
    for key in list(st.session_state.keys()):
        if str(key).startswith("exclude_"):
            del st.session_state[key]


MEDIA_DISPLAY_NAMES = {
    "KK KYODO NEWS SITE": "KK KYODO NEWS SITE（共同通信社）",
}


def render_result_row(index, row, search_seq):
    checkbox_key = f"exclude_{search_seq}_{index}"
    media = row.get("media", "")
    display_media = MEDIA_DISPLAY_NAMES.get(media, media)
    url = row.get("url", "")
    title = row.get("title", "")
    score = row.get("score", "")
    level = row.get("level", "")

    col_chk, col_info = st.columns([0.8, 11.2])
    with col_chk:
        st.checkbox("除外", key=checkbox_key, value=False, label_visibility="collapsed")
    with col_info:
        checked = st.session_state.get(checkbox_key, False)
        badge_color = "#d32f2f" if checked else "#1976d2"
        badge_label = "除外" if checked else level
        st.markdown(
            f"<div class='result-card'><strong>{index + 1}. {display_media}</strong> "
            f"<span style='background:{badge_color};color:#fff;padding:2px 7px;border-radius:4px;font-size:0.8em;'>{badge_label}</span> "
            f"<span style='font-size:0.85em;color:#555;'>score={score}</span></div>",
            unsafe_allow_html=True,
        )
        if title:
            st.markdown(f"<div class='result-title'>{title}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='result-url'><a href='{url}' target='_blank'>{url}</a></div>", unsafe_allow_html=True)
    st.divider()


def main():
    init_db()

    if "search_seq" not in st.session_state:
        st.session_state["search_seq"] = 0
    if "result_cache" not in st.session_state:
        st.session_state["result_cache"] = {}

    st.title("OVO / b-dot 転載URL抽出ツール v2.7.0")
    st.caption("GIGAパーク対応 / NewsPicks・NEWSjp・SmartNews収集強化 / Faith WonderWorks→GIGAパーク移行")

    col1, col2 = st.columns(2)
    with col1:
        ovo_url = st.text_input(
            "元記事 OVO URL",
            value="",
            placeholder="https://ovo.kyodo.co.jp/...",
        )
    with col2:
        bdot_url = st.text_input(
            "元記事 b-dot URL",
            value="",
            placeholder="https://b-dot.jp/... または https://b.kyodo.co.jp/...",
        )

    manual_title = st.text_input(
        "記事タイトル（入力すると検索精度が上がります）",
        value="",
        placeholder="例：○○が△△を発表、新たな展開へ",
        help="入力すると自動取得タイトルより優先されます。OVO bizページはJSレンダリングでタイトルが取れない場合があるため、タイトルを入力すると確実に検索に使用されます。",
    )

    manual_keywords_raw = st.text_input(
        "補足キーワード（スペース区切り・入力すると全クローラーで優先使用）",
        value="",
        placeholder="例: 住宅用火災警報器　または　MIXI MEETUP　または　ウェルネスチャレンジ",
        help="自動抽出では汎用語（設置・交換・AI等）が上位になりがちです。記事を一意に特定できる固有名詞をスペース区切りで入力すると、goo/livedoor/Yahoo/PR TIMESでの検索精度が大幅に向上します。",
    )

    opt1, opt2, opt3 = st.columns(3)
    with opt1:
        debug_mode = st.checkbox("除外候補も表示（デバッグ用）", value=False)
    with opt2:
        save_to_db = st.checkbox("結果をDB保存", value=False)
    with opt3:
        show_source_info = st.checkbox("元記事解析情報を表示", value=True)

    run = st.button("転載URLを検索", type="primary", use_container_width=True)

    # Brave API キーはUIではなくサーバー設定（.streamlit/secrets.toml）から読み込む
    import os as _os
    bing_api_key = st.secrets.get("BRAVE_API_KEY", "") or _os.environ.get("BRAVE_API_KEY", "")

    if run:
        if not safe_str(ovo_url) and not safe_str(bdot_url):
            st.error("OVO URL または b-dot URL のどちらかを入力してください。")
            return

        st.session_state["search_seq"] += 1
        clear_exclusion_keys()

        # 補足キーワードをパース（スペース・読点・カンマで分割、2文字以上のみ）
        import re as _re
        manual_kw_list = [k for k in _re.split(r"[\s,、　]+", manual_keywords_raw.strip()) if k and len(k) >= 2]

        with st.spinner("元記事解析・転載先検索中..."):
            source_contexts, final_rows, debug_rows = run_pipeline(
                ovo_url=ovo_url,
                bdot_url=bdot_url,
                debug_mode=debug_mode,
                manual_title=manual_title,
                manual_keywords=manual_kw_list,
                bing_api_key=bing_api_key,
            )

        signature = build_search_signature(source_contexts)
        previous_rows = st.session_state["result_cache"].get(signature, [])
        stable_rows = merge_with_previous_cache(final_rows, previous_rows)
        st.session_state["result_cache"][signature] = stable_rows

        st.session_state["source_contexts"] = source_contexts
        st.session_state["final_rows"] = stable_rows
        st.session_state["debug_rows"] = debug_rows
        st.session_state["active_signature"] = signature

    if "final_rows" not in st.session_state:
        return

    source_contexts = st.session_state.get("source_contexts", [])
    final_rows = st.session_state.get("final_rows", [])
    debug_rows = st.session_state.get("debug_rows", [])
    search_seq = st.session_state.get("search_seq", 0)

    if show_source_info and source_contexts:
        st.subheader("元記事解析")
        for ctx in source_contexts:
            if not ctx.get("title"):
                st.warning(
                    f"⚠️ {ctx.get('origin', '')} のタイトルが取得できませんでした。"
                    "「記事タイトル」欄に正確なタイトルを入力してから再検索してください。"
                )
        source_df = pd.DataFrame([
            {
                "元記事": ctx.get("origin", ""),
                "URL": ctx.get("url", ""),
                "タイトル": ctx.get("title", ""),
                "source_id": ctx.get("source_id", ""),
                "媒体判定": ctx.get("media", ""),
                "判定モード": ctx.get("source_mode", ""),
            }
            for ctx in source_contexts
        ])
        st.dataframe(source_df, use_container_width=True, hide_index=True)

    st.subheader(f"抽出結果 ({len(final_rows)}件 / 上限{MAX_RESULTS}件)")
    st.caption("除外したい行だけチェックしてください。新しい検索を実行するとチェックは自動でクリアされます。")

    if not final_rows:
        st.warning("条件に合う転載URLが見つかりませんでした。")
    else:
        for i, row in enumerate(final_rows):
            render_result_row(i, row, search_seq)

        included_rows = [
            row for i, row in enumerate(final_rows)
            if not st.session_state.get(f"exclude_{search_seq}_{i}", False)
        ]
        excluded_count = len(final_rows) - len(included_rows)

        st.subheader(
            f"Word貼り付け用テキスト（{len(included_rows)}件"
            + (f" / 除外 {excluded_count}件" if excluded_count else "")
            + "）"
        )
        word_text = format_word_output(included_rows)
        st.text_area(
            "以下をコピーして使えます",
            value=word_text,
            height=max(220, max(1, len(included_rows)) * 48),
        )

        df_all = to_dataframe(final_rows)
        csv_bytes = df_all.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        txt_bytes = word_text.encode("utf-8")

        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                label="CSVダウンロード（全件）",
                data=csv_bytes,
                file_name="reprint_results_v260.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with dl2:
            st.download_button(
                label="Word貼り付け用TXTダウンロード",
                data=txt_bytes,
                file_name="reprint_results_v260.txt",
                mime="text/plain",
                use_container_width=True,
            )

        if save_to_db:
            save_results(final_rows)
            st.success("結果を data.db に保存しました。")

    if debug_mode:
        st.subheader(f"除外候補 ({len(debug_rows)}件)")
        if debug_rows:
            debug_df = pd.DataFrame([
                {
                    "媒体": r.get("media", ""),
                    "URL": r.get("url", ""),
                    "タイトル": r.get("title", ""),
                    "判定": r.get("level", ""),
                    "スコア": r.get("score", ""),
                    "HTTP": r.get("status_code", ""),
                    "除外理由": r.get("reject_reason", ""),
                    "タイトル一致": r.get("title_similarity", ""),
                    "KW一致": r.get("keyword_overlap", ""),
                    "source_id一致": ",".join(r.get("matched_source_ids", [])) if isinstance(r.get("matched_source_ids"), list) else r.get("matched_source_ids", ""),
                    "検索エンジン": r.get("engine", ""),
                    "順位": r.get("rank", ""),
                    "根拠": r.get("score_reason", ""),
                }
                for r in debug_rows
            ])
            st.dataframe(debug_df, use_container_width=True, hide_index=True)
        else:
            st.info("除外候補はありません。")


if __name__ == "__main__":
    main()