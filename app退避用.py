from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import streamlit as st

from utils import (
    safe_str,
    title_core,
    canonicalize_url,
    detect_source_mode,
    classify_media,
    extract_source_id,
    is_probable_article_url,
    media_sort_key,
)
from scraper import fetch_article, soft_validate_candidate
from search import search_all
from judge import judge_candidate, is_false_positive
from db import init_db, save_results


st.set_page_config(
    page_title="OVO / b-dot 転載URL抽出 v2.3.1",
    page_icon="📰",
    layout="wide",
)

TRUSTED_FETCH_SKIP_MEDIA = {
    "Yahooニュース",
    "47NEWS",
    "ドコモｄメニューニュース",
    "楽天インフォシークニュース",
    "エキサイトニュース",
    "チバテレ",
    "ニフティニュース",
    "gooニュース",
    "ビッグローブニュース",
    "ライブドアニュース",
    "アメーバニュース",
    "mixiニュース",
    "ニコニコニュース",
    "めるもニュース",
    "ソネットニュース",
    "Gunosy",
    "OKMusic",
    "ビルボードジャパン",
    "ウレぴあ総研",
    "LIMIA",
    "モデルプレス",
}


def build_source_context(url: str, origin_label: str):
    url = safe_str(url)
    if not url:
        return None

    art = fetch_article(url)
    title = safe_str(art.get("title"))
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
        "source_mode": detect_source_mode(url),
        "published_at": art.get("published_at", ""),
        "raw": art,
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
    }


def quick_pre_filter(cand):
    url = safe_str(cand.get("url"))
    title = safe_str(cand.get("title"))
    media = safe_str(cand.get("media"))

    if not url.startswith("http"):
        return False

    if media in {"Twitter", "Facebook"}:
        return False

    if not title and media not in {
        "エキサイトニュース",
        "ドコモｄメニューニュース",
        "楽天インフォシークニュース",
    }:
        return False

    return True


def enrich_candidate_if_needed(cand):
    """
    fetch最小化:
    - タイトルが十分あるなら取得しない
    - trusted media + probable URL なら原則取得しない
    - タイトルがない / 極端に短い候補だけ補完
    """
    title = safe_str(cand.get("title"))
    media = safe_str(cand.get("media"))
    url = safe_str(cand.get("url"))

    if title and len(title) >= 12:
        return cand

    if media in TRUSTED_FETCH_SKIP_MEDIA and is_probable_article_url(url, media):
        if title and len(title) >= 8:
            return cand

    if not is_probable_article_url(url, media):
        return cand

    art = fetch_article(url)
    if art.get("title"):
        cand["title"] = art["title"]
    if art.get("text"):
        cand["text"] = art["text"]
    if art.get("final_url"):
        cand["url"] = art["final_url"]
        cand["canonical_url"] = canonicalize_url(art["final_url"])
    if art.get("media"):
        cand["media"] = art["media"]
    return cand


def merge_same_url(rows):
    bucket = {}

    for row in rows:
        key = row.get("canonical_url") or canonicalize_url(row.get("url", ""))
        if not key:
            continue

        if key not in bucket:
            bucket[key] = {
                **row,
                "origins": set(row.get("matched_origins", []) or []),
            }
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
            keep_ids = sorted(
                set((cur.get("matched_source_ids") or []) + (row.get("matched_source_ids") or []))
            )
            bucket[key] = {
                **row,
                "origins": keep_origins,
                "matched_source_ids": keep_ids,
            }
        else:
            merged_ids = sorted(
                set((cur.get("matched_source_ids") or []) + (row.get("matched_source_ids") or []))
            )
            cur["matched_source_ids"] = merged_ids

    out = []
    for _, v in bucket.items():
        origins = sorted(list(v.get("origins", set())))
        v["origins"] = origins
        v["origin_label"] = ",".join(origins)
        out.append(v)
    return out


def keep_best_per_media(rows):
    best = {}
    for row in rows:
        media = row.get("media", "その他")
        if media not in best:
            best[media] = row
            continue

        cur = best[media]
        cur_key = (
            float(cur.get("score", 0.0) or 0.0),
            float(cur.get("title_similarity", 0.0) or 0.0),
            float(cur.get("keyword_overlap", 0.0) or 0.0),
            -int(cur.get("rank", 9999) or 9999),
            len(safe_str(cur.get("title"))),
        )
        new_key = (
            float(row.get("score", 0.0) or 0.0),
            float(row.get("title_similarity", 0.0) or 0.0),
            float(row.get("keyword_overlap", 0.0) or 0.0),
            -int(row.get("rank", 9999) or 9999),
            len(safe_str(row.get("title"))),
        )
        if new_key > cur_key:
            best[media] = row

    result = list(best.values())
    result.sort(
        key=lambda x: (
            media_sort_key(x.get("media", "")),
            -float(x.get("score", 0.0) or 0.0),
        )
    )
    return result


def format_word_output(rows):
    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r.get('media', '')}")
        lines.append(f"URL: {r.get('url', '')}")
        lines.append(f"タイトル: {r.get('title', '')}")
        lines.append(f"判定: {r.get('level', '')} / score={r.get('score', '')}")
        lines.append(f"元記事: {r.get('origin_label', '')}")
        lines.append("")
    return "\n".join(lines)


def to_dataframe(rows):
    data = []
    for r in rows:
        data.append({
            "媒体": r.get("media", ""),
            "転載URL": r.get("url", ""),
            "タイトル": r.get("title", ""),
            "判定": r.get("level", ""),
            "スコア": r.get("score", ""),
            "元記事": r.get("origin_label", ""),
            "タイトル一致": r.get("title_similarity", ""),
            "KW一致": r.get("keyword_overlap", ""),
            "source_id一致": ",".join(r.get("matched_source_ids", []))
            if isinstance(r.get("matched_source_ids"), list)
            else r.get("matched_source_ids", ""),
            "検索エンジン": r.get("engine", ""),
            "順位": r.get("rank", ""),
            "根拠": r.get("score_reason", ""),
        })
    return pd.DataFrame(data)


def process_candidate(cand, source_contexts):
    if not quick_pre_filter(cand):
        return None, {
            **cand,
            "reject_reason": "quick_pre_filter",
            "score": 0.0,
            "level": "対象外",
        }

    cand = enrich_candidate_if_needed(cand)
    judged = judge_candidate(source_contexts, cand)

    if not soft_validate_candidate(judged):
        return None, {**judged, "reject_reason": "soft_validate_false"}

    if is_false_positive(judged):
        return None, {**judged, "reject_reason": "false_positive"}

    return judged, None


def run_pipeline(ovo_url: str, bdot_url: str, debug_mode: bool = False):
    source_contexts = []

    source_jobs = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        if safe_str(ovo_url):
            source_jobs.append(ex.submit(build_source_context, ovo_url, "OVO"))
        if safe_str(bdot_url):
            source_jobs.append(ex.submit(build_source_context, bdot_url, "b-dot"))

        for fut in as_completed(source_jobs):
            try:
                ctx = fut.result()
                if ctx:
                    source_contexts.append(ctx)
            except Exception:
                pass

    source_contexts.sort(key=lambda x: x.get("origin", ""))

    if not source_contexts:
        return [], [], []

    source_canonical_urls = {
        ctx["canonical_url"] for ctx in source_contexts if ctx.get("canonical_url")
    }

    raw_candidates = []

    with ThreadPoolExecutor(max_workers=min(2, len(source_contexts))) as ex:
        search_jobs = {ex.submit(search_all, ctx): ctx for ctx in source_contexts}
        for fut in as_completed(search_jobs):
            ctx = search_jobs[fut]
            try:
                items = fut.result() or []
            except Exception:
                items = []

            for item in items:
                cand = normalize_candidate(item, origin=ctx["origin"])
                key = cand.get("canonical_url") or canonicalize_url(cand.get("url", ""))
                if not key:
                    continue
                if key in source_canonical_urls:
                    continue
                raw_candidates.append(cand)

    # 検索段階の重複排除
    pre_dedup = {}
    for cand in raw_candidates:
        key = cand.get("canonical_url") or canonicalize_url(cand.get("url", ""))
        if not key:
            continue

        if key not in pre_dedup:
            pre_dedup[key] = cand
        else:
            cur = pre_dedup[key]
            cur_title_len = len(safe_str(cur.get("title")))
            new_title_len = len(safe_str(cand.get("title")))
            if new_title_len > cur_title_len:
                pre_dedup[key] = cand

    deduped_candidates = list(pre_dedup.values())

    accepted = []
    rejected = []

    with ThreadPoolExecutor(max_workers=6) as ex:
        jobs = [ex.submit(process_candidate, cand, source_contexts) for cand in deduped_candidates]
        for fut in as_completed(jobs):
            try:
                ok_row, ng_row = fut.result()
                if ok_row:
                    accepted.append(ok_row)
                if ng_row and debug_mode:
                    rejected.append(ng_row)
            except Exception:
                pass

    # URL単位で OVO / b-dot 統合
    merged = merge_same_url(accepted)

    # 媒体ごとベスト1
    final_rows = keep_best_per_media(merged)

    # 念のため並び替え
    final_rows.sort(
        key=lambda x: (
            media_sort_key(x.get("media", "")),
            -float(x.get("score", 0.0) or 0.0),
        )
    )

    if debug_mode:
        rejected.sort(
            key=lambda x: (
                -float(x.get("score", 0.0) or 0.0),
                x.get("media", ""),
            )
        )

    return source_contexts, final_rows, rejected


def main():
    init_db()

    st.title("OVO / b-dot 転載URL抽出ツール v2.3.1")
    st.caption("3段階探索 + 並列検索 + fetch最小化 + OVO/b-dot統合")

    with st.expander("使い方", expanded=False):
        st.markdown(
            """
- **元記事 OVO URL** と **元記事 b-dot URL** を入力できます
- どちらか片方だけでも実行可能です
- 両方から検索し、**同じ転載URLなら1件に統合**します
- 媒体ごとに **最良候補1件** を残します
- `デバッグ結果も表示` をONにすると、除外候補も確認できます
            """
        )

    col1, col2 = st.columns(2)
    with col1:
        ovo_url = st.text_input(
            "元記事 OVO URL",
            value="https://ovo.kyodo.co.jp/news/biz/a-2080953",
            placeholder="https://ovo.kyodo.co.jp/...",
        )
    with col2:
        bdot_url = st.text_input(
            "元記事 b-dot URL",
            value="",
            placeholder="https://b-dot.jp/... または https://b.kyodo.co.jp/...",
        )

    opt1, opt2, opt3 = st.columns(3)
    with opt1:
        debug_mode = st.checkbox("デバッグ結果も表示", value=False)
    with opt2:
        save_to_db = st.checkbox("結果をDB保存", value=False)
    with opt3:
        show_source_info = st.checkbox("元記事解析情報を表示", value=True)

    run = st.button("転載URLを検索", type="primary", use_container_width=True)

    if not run:
        return

    if not safe_str(ovo_url) and not safe_str(bdot_url):
        st.error("OVO URL または b-dot URL のどちらかを入力してください。")
        return

    with st.spinner("元記事解析・転載先検索中..."):
        source_contexts, final_rows, debug_rows = run_pipeline(
            ovo_url=ovo_url,
            bdot_url=bdot_url,
            debug_mode=debug_mode,
        )

    if show_source_info and source_contexts:
        st.subheader("元記事解析")
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

    st.subheader(f"抽出結果 ({len(final_rows)}件)")
    if final_rows:
        df = to_dataframe(final_rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        word_text = format_word_output(final_rows)

        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        txt_bytes = word_text.encode("utf-8")

        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                label="CSVダウンロード",
                data=csv_bytes,
                file_name="reprint_results_v231.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with dl2:
            st.download_button(
                label="Word貼り付け用TXTダウンロード",
                data=txt_bytes,
                file_name="reprint_results_v231.txt",
                mime="text/plain",
                use_container_width=True,
            )

        st.subheader("Word貼り付け用テキスト")
        st.text_area(
            "以下をコピーして使えます",
            value=word_text,
            height=320,
        )

        if save_to_db:
            save_results(final_rows)
            st.success("結果を data.db に保存しました。")

    else:
        st.warning("条件に合う転載URLが見つかりませんでした。")

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
                    "除外理由": r.get("reject_reason", ""),
                    "タイトル一致": r.get("title_similarity", ""),
                    "KW一致": r.get("keyword_overlap", ""),
                    "source_id一致": ",".join(r.get("matched_source_ids", []))
                    if isinstance(r.get("matched_source_ids"), list)
                    else r.get("matched_source_ids", ""),
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
