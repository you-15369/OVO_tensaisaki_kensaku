import base64
import binascii
import os
import re
import secrets
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from pipeline import MAX_RESULTS, build_search_signature, merge_with_previous_cache, run_pipeline
from utils import safe_str

APP_VERSION = "3.0.0"

app = FastAPI(title="OVO / b-dot 転載URL抽出 API", version=APP_VERSION)

# 社内向け Basic 認証（フロントと同じ共通ID/パスワード）。未設定時は認証なし（ローカル開発用）
BASIC_AUTH_USER = os.environ.get("BASIC_AUTH_USER", "")
BASIC_AUTH_PASSWORD = os.environ.get("BASIC_AUTH_PASSWORD", "")
AUTH_EXEMPT_PATHS = {"/api/health"}  # ホスティング先のヘルスチェック用


def _check_basic_auth(header: str) -> bool:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        user, sep, password = base64.b64decode(encoded).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user_ok = secrets.compare_digest(user.encode(), BASIC_AUTH_USER.encode())
    pass_ok = secrets.compare_digest(password.encode(), BASIC_AUTH_PASSWORD.encode())
    return bool(sep) and user_ok and pass_ok


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if (
        BASIC_AUTH_USER
        and BASIC_AUTH_PASSWORD
        and request.method != "OPTIONS"
        and request.url.path not in AUTH_EXEMPT_PATHS
        and not _check_basic_auth(request.headers.get("authorization", ""))
    ):
        return Response(
            "認証が必要です。",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="tensai", charset="UTF-8"'},
        )
    return await call_next(request)


# CORS は最後に追加して最外層にする（401 応答にも CORS ヘッダーが付くように）
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 同じ元記事を再検索したときに結果を安定させるためのキャッシュ（旧 st.session_state["result_cache"]）
_result_cache: dict[str, list[dict]] = {}
_cache_lock = threading.Lock()

SOURCE_FIELDS = ["origin", "url", "title", "source_id", "media", "source_mode"]
ROW_FIELDS = [
    "media", "url", "canonical_url", "title", "level", "score", "origin_label",
    "title_similarity", "keyword_overlap", "matched_source_ids", "engine", "rank",
    "status_code", "score_reason", "reject_reason",
]


class SearchRequest(BaseModel):
    ovo_url: str = ""
    bdot_url: str = ""
    manual_title: str = ""
    manual_keywords: str = ""
    debug_mode: bool = False


def _pick(d: dict, fields: list[str]) -> dict:
    out = {}
    for f in fields:
        v = d.get(f)
        if isinstance(v, set):
            v = sorted(v)
        out[f] = v
    return out


@app.get("/api/health")
def health():
    return {"status": "ok", "version": APP_VERSION}


@app.post("/api/search")
def search(req: SearchRequest):
    if not safe_str(req.ovo_url) and not safe_str(req.bdot_url):
        raise HTTPException(status_code=400, detail="OVO URL または b-dot URL のどちらかを入力してください。")

    # 補足キーワードをパース（スペース・読点・カンマで分割、2文字以上のみ）
    manual_kw_list = [k for k in re.split(r"[\s,、　]+", req.manual_keywords.strip()) if k and len(k) >= 2]
    brave_api_key = os.environ.get("BRAVE_API_KEY", "")

    source_contexts, final_rows, debug_rows = run_pipeline(
        ovo_url=req.ovo_url,
        bdot_url=req.bdot_url,
        debug_mode=req.debug_mode,
        manual_title=req.manual_title,
        manual_keywords=manual_kw_list,
        bing_api_key=brave_api_key,
    )

    signature = build_search_signature(source_contexts)
    with _cache_lock:
        previous_rows = _result_cache.get(signature, [])
        stable_rows = merge_with_previous_cache(final_rows, previous_rows)
        if signature:
            _result_cache[signature] = stable_rows

    return {
        "max_results": MAX_RESULTS,
        "source_contexts": [_pick(c, SOURCE_FIELDS) for c in source_contexts],
        "rows": [_pick(r, ROW_FIELDS) for r in stable_rows],
        "debug_rows": [_pick(r, ROW_FIELDS) for r in debug_rows],
    }
