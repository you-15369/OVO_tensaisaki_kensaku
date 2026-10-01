# tensai

OVO / b-dot 転載URL抽出アプリ

- **v3（推奨）**: Next.js（`frontend/`） + FastAPI（`backend/`）
- v2（旧）: Streamlit（ルート直下の `app.py`）

## v3: Next.js + FastAPI 版

```
backend/   FastAPI（検索・判定ロジック）  → http://localhost:8000
frontend/  Next.js（画面）                          → http://localhost:3000
```

### 1. バックエンド（FastAPI）

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # BRAVE_API_KEY を記入
uvicorn main:app --reload --port 8000
```

API ドキュメント: http://localhost:8000/docs

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/api/health` | 死活確認 |
| POST | `/api/search` | 転載URL検索（`ovo_url`, `bdot_url`, `manual_title`, `manual_keywords`, `debug_mode`） |

環境変数（`backend/.env`）:

- `BRAVE_API_KEY` … Brave Search API キー
- `CORS_ORIGINS` … フロントエンドのURL（既定 `http://localhost:3000`）

DBは使用しません（検索結果はブラウザ上でCSV/TXTとしてダウンロードできます）。

### 2. フロントエンド（Next.js）

```bash
cd frontend
npm install
npm run dev
```

ブラウザで http://localhost:3000 を開きます。
ブラウザは同一オリジンの `/api/*` を呼び、Next.js が `API_PROXY_TARGET`（既定 `http://localhost:8000`）の FastAPI に中継します。

### 3. Basic認証（社内限定公開）

共通ID/パスワードで画面とAPIを保護します。**フロントとバックエンドに同じ値**を設定してください。

| 設定先 | 変数 |
| --- | --- |
| `frontend/.env.local`（またはホスティング先の環境変数） | `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD`, `API_PROXY_TARGET` |
| `backend/.env`（同上） | `BASIC_AUTH_USER`, `BASIC_AUTH_PASSWORD` |

- フロント: 本番（`next start`）で未設定だと 503 を返します（設定漏れで公開されるのを防止）。`npm run dev` では未設定なら認証なし。
- バックエンド: 未設定なら認証なし。`/api/health` のみ認証不要（ヘルスチェック用）。
- ブラウザで入力したID/パスワードは Next.js 経由でそのままバックエンドに転送されます。
- 必ず HTTPS で公開してください（Basic認証は平文同等のため）。

## Brave Search APIキーの取得

1. https://api.search.brave.com/ でアカウント作成（無料枠: 2,000リクエスト/月）
2. APIキーを発行
3. v3 は `backend/.env`、v2 は `.streamlit/secrets.toml` に `BRAVE_API_KEY` を記載

## v2: Streamlit 版（旧）

```bash
pip install -r requirements.txt
streamlit run app.py
```
