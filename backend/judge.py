from utils import (
    ORIGINAL_MEDIA,
    SOCIAL_MEDIA,
    classify_media,
    is_noise_url,
    is_probable_article_url,
    is_trusted_media,
    keyword_overlap,
    media_url_pattern_score,
    safe_str,
    sim,
    title_core,
    to_level,
)

TARGET_RESCUE_MEDIA = {
    "GIGAパーク",
    "ニコニコニュース",
    "チバテレ",
    "NewsPicks",
    "NEWSjp",
    "SmartNews",
    "PRタイムス",
    "アットプレス",
    "ドリームニュース",
    "Yahooファイナンス",
}


def judge_candidate(source_contexts, candidate):
    url = safe_str(candidate.get("url"))
    title = safe_str(candidate.get("title"))
    snippet = safe_str(candidate.get("snippet"))
    text = safe_str(candidate.get("text"))
    media = safe_str(candidate.get("media")) or classify_media(url, title)

    best_title_sim = 0.0
    best_kw = 0.0
    matched_origins = []
    matched_source_ids = []

    for ctx in source_contexts:
        src_title = safe_str(ctx.get("title"))
        src_core = safe_str(ctx.get("title_core"))
        src_id = safe_str(ctx.get("source_id"))

        # 元記事タイトルが空の場合、手動キーワードを代替比較テキストとして使用する。
        # これにより bizページJS未レンダリング＋補足KW入力の組み合わせでもスコアが計算される。
        if not src_title:
            manual_kws = ctx.get("manual_keywords") or []
            if manual_kws:
                src_title = " ".join(manual_kws)
                src_core = src_title

        ts = max(
            sim(title_core(title), src_core),
            sim(title, src_title),
            sim(snippet, src_title) * 0.82,
            sim(title_core(snippet), src_core) * 0.82,
        )
        kw = max(
            keyword_overlap(title, src_title),
            keyword_overlap(snippet, src_title) * 0.82,
            keyword_overlap(text[:700], src_title) * 0.68,
        )

        if ts > best_title_sim:
            best_title_sim = ts
        if kw > best_kw:
            best_kw = kw

        hay = f"{url} {title} {snippet} {text}"
        if src_id and src_id in hay:
            matched_origins.append(ctx.get("origin"))
            matched_source_ids.append(src_id)

    primary_source_id = matched_source_ids[0] if matched_source_ids else ""
    url_score = media_url_pattern_score(url, media, primary_source_id)
    trusted_bonus = 0.10 if is_trusted_media(media) else 0.0
    probable_bonus = 0.10 if is_probable_article_url(url, media) else -0.18
    source_id_bonus = 0.26 if matched_source_ids else 0.0

    penalty = 0.0
    reasons = []

    if media in ORIGINAL_MEDIA:
        penalty -= 0.72
        reasons.append("元記事媒体")
    if media in SOCIAL_MEDIA:
        penalty -= 0.24
        reasons.append("SNS")
    if is_noise_url(url):
        penalty -= 0.62
        reasons.append("一覧/検索URL")

    title_score = best_title_sim * 0.52
    kw_score = best_kw * 0.22
    total = title_score + kw_score + url_score + trusted_bonus + probable_bonus + source_id_bonus + penalty

    # trusted_rescue: ts>=0.50 OR kw>=0.22 で発動（汎用的条件）
    rescue_active = False
    if media and is_trusted_media(media) and is_probable_article_url(url, media):
        if best_title_sim >= 0.50 or best_kw >= 0.22 or matched_source_ids:
            total = max(total, 0.64)
            reasons.append("trusted_rescue")
            rescue_active = True

    if media in TARGET_RESCUE_MEDIA and is_probable_article_url(url, media):
        if best_title_sim >= 0.55 or best_kw >= 0.25 or matched_source_ids:
            total = max(total, 0.72)
            reasons.append("target_media_rescue")
            rescue_active = True

    if matched_source_ids and is_probable_article_url(url, media):
        total = max(total, 0.62)
        reasons.append("source_id_rescue")
        rescue_active = True

    total = max(0.0, min(1.0, total))
    level = to_level(total)

    if best_title_sim >= 0.78:
        reasons.append("タイトル高一致")
    elif best_title_sim >= 0.56:
        reasons.append("タイトル中一致")

    if best_kw >= 0.30:
        reasons.append("キーワード一致")

    if matched_source_ids:
        reasons.append(f"source_id一致:{'/'.join(sorted(set(matched_source_ids)))}")

    if url_score >= 0.18:
        reasons.append("URLパターン一致")

    return {
        **candidate,
        "media": media or "その他",
        "title_similarity": round(best_title_sim, 4),
        "keyword_overlap": round(best_kw, 4),
        "matched_source_ids": sorted(set(matched_source_ids)),
        "matched_origins": sorted(set([x for x in matched_origins if x])),
        "score": round(total, 4),
        "level": level,
        "score_reason": " / ".join(reasons),
        "_rescue_active": rescue_active,
    }


def is_false_positive(row):
    media = safe_str(row.get("media"))
    url = safe_str(row.get("url"))
    score = float(row.get("score", 0.0) or 0.0)
    title_sim = float(row.get("title_similarity", 0.0) or 0.0)
    kw = float(row.get("keyword_overlap", 0.0) or 0.0)
    has_source_id = bool(row.get("matched_source_ids"))
    engine = safe_str(row.get("engine", ""))
    is_direct = engine.startswith("direct")
    title = safe_str(row.get("title"))
    rescue_active = bool(row.get("_rescue_active", False))

    # ── 基本除外 ──────────────────────────────
    if not url.startswith("http"):
        return True
    if media in ORIGINAL_MEDIA:
        return True
    if is_noise_url(url):
        return True
    if media in SOCIAL_MEDIA:
        return True

    # ── source_id一致は最優先で通過（direct+fetched+titleなしでも通過）──
    if has_source_id and is_probable_article_url(url, media):
        return False

    # ── direct候補：フェッチ後にtitleが取れなければ404 ──
    # source_id一致チェックの後に評価する
    if is_direct and row.get("fetched") and not title:
        return True

    # ── rescue発動済み → 通過（関連性の証拠あり）──────
    if rescue_active and is_probable_article_url(url, media):
        return False

    # ── タイトル類似もKW重複も両方低い = 明らかに無関係 ──
    # 汎用的条件：記事ジャンルに関わらず ts<0.42 AND kw<0.18 は不要記事
    # （正規転載スニペットは必ずどちらかが0.42/0.18を超える）
    if title_sim < 0.42 and kw < 0.18 and not has_source_id:
        return True

    # ── その他 ────────────────────────────────
    if media == "その他" and score < 0.68:
        return True

    return False
