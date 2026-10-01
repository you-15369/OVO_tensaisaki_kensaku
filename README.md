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
バックエンドのURLを変える場合は `frontend/.env.local` に `NEXT_PUBLIC_API_BASE=http://...` を設定してください。

## Brave Search APIキーの取得

1. https://api.search.brave.com/ でアカウント作成（無料枠: 2,000リクエスト/月）
2. APIキーを発行
3. v3 は `backend/.env`、v2 は `.streamlit/secrets.toml` に `BRAVE_API_KEY` を記載

## v2: Streamlit 版（旧）

```bash
pip install -r requirements.txt
streamlit run app.py
```
