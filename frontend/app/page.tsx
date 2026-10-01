"use client";

import { useMemo, useState } from "react";

// 既定は同一オリジン（next.config.mjs の rewrites で FastAPI に中継）
const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

type SourceContext = {
  origin: string;
  url: string;
  title: string;
  source_id: string;
  media: string;
  source_mode: string;
};

type Row = {
  media: string;
  url: string;
  canonical_url?: string;
  title?: string;
  level?: string;
  score?: number | string;
  origin_label?: string;
  title_similarity?: number | string;
  keyword_overlap?: number | string;
  matched_source_ids?: string[] | string;
  engine?: string;
  rank?: number | string;
  status_code?: number | null;
  score_reason?: string;
  reject_reason?: string;
};

type SearchResponse = {
  max_results: number;
  source_contexts: SourceContext[];
  rows: Row[];
  debug_rows: Row[];
};

const MEDIA_DISPLAY_NAMES: Record<string, string> = {
  "KK KYODO NEWS SITE": "KK KYODO NEWS SITE（共同通信社）",
};

const str = (v: unknown) => (v === null || v === undefined ? "" : String(v));
const ids = (v: Row["matched_source_ids"]) => (Array.isArray(v) ? v.join(",") : str(v));
const safeHref = (url: string) => (/^https?:\/\//i.test(url) ? url : undefined);

function formatWordOutput(rows: Row[]) {
  return rows.map((r) => `${str(r.media)}\n${str(r.url)}\n`).join("\n").trim();
}

const CSV_COLUMNS: [string, (r: Row) => unknown][] = [
  ["媒体", (r) => r.media],
  ["転載URL", (r) => r.url],
  ["タイトル", (r) => r.title],
  ["判定", (r) => r.level],
  ["スコア", (r) => r.score],
  ["元記事", (r) => r.origin_label],
  ["タイトル一致", (r) => r.title_similarity],
  ["KW一致", (r) => r.keyword_overlap],
  ["source_id一致", (r) => ids(r.matched_source_ids)],
  ["検索エンジン", (r) => r.engine],
  ["順位", (r) => r.rank],
  ["HTTP", (r) => r.status_code],
  ["根拠", (r) => r.score_reason],
];

function toCsv(rows: Row[]) {
  const esc = (v: unknown) => {
    const s = str(v);
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [CSV_COLUMNS.map(([h]) => esc(h)).join(",")];
  for (const r of rows) lines.push(CSV_COLUMNS.map(([, f]) => esc(f(r))).join(","));
  return "﻿" + lines.join("\r\n");
}

function download(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Home() {
  const [ovoUrl, setOvoUrl] = useState("");
  const [bdotUrl, setBdotUrl] = useState("");
  const [manualTitle, setManualTitle] = useState("");
  const [manualKeywords, setManualKeywords] = useState("");
  const [debugMode, setDebugMode] = useState(false);
  const [showSourceInfo, setShowSourceInfo] = useState(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [copied, setCopied] = useState(false);

  async function runSearch() {
    if (!ovoUrl.trim() && !bdotUrl.trim()) {
      setError("OVO URL または b-dot URL のどちらかを入力してください。");
      return;
    }
    setError("");
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ovo_url: ovoUrl,
          bdot_url: bdotUrl,
          manual_title: manualTitle,
          manual_keywords: manualKeywords,
          debug_mode: debugMode,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail ?? `サーバーエラー (HTTP ${res.status})`);
      }
      const data: SearchResponse = await res.json();
      setResult(data);
      setExcluded(new Set()); // 新しい検索で除外チェックをクリア
    } catch (e) {
      setError(
        e instanceof TypeError
          ? `バックエンド (${API_BASE || "/api"}) に接続できません。FastAPI が起動しているか確認してください。`
          : e instanceof Error
            ? e.message
            : String(e),
      );
    } finally {
      setLoading(false);
    }
  }

  function toggleExclude(i: number) {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  const rows = result?.rows ?? [];
  const includedRows = useMemo(() => rows.filter((_, i) => !excluded.has(i)), [rows, excluded]);
  const wordText = useMemo(() => formatWordOutput(includedRows), [includedRows]);

  async function copyWordText() {
    await navigator.clipboard.writeText(wordText);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <main className="container">
      <h1>OVO / b-dot 転載URL抽出ツール v3.0.0</h1>
      <p className="caption">
        Next.js + FastAPI 版 / GIGAパーク対応 / NewsPicks・NEWSjp・SmartNews収集強化
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!loading) runSearch();
        }}
      >
        <div className="grid2">
          <div className="field">
            <label htmlFor="ovo">元記事 OVO URL</label>
            <input id="ovo" type="text" value={ovoUrl} onChange={(e) => setOvoUrl(e.target.value)} placeholder="https://ovo.kyodo.co.jp/..." />
          </div>
          <div className="field">
            <label htmlFor="bdot">元記事 b-dot URL</label>
            <input id="bdot" type="text" value={bdotUrl} onChange={(e) => setBdotUrl(e.target.value)} placeholder="https://b-dot.jp/... または https://b.kyodo.co.jp/..." />
          </div>
        </div>

        <div className="field">
          <label htmlFor="title">記事タイトル（入力すると検索精度が上がります）</label>
          <input id="title" type="text" value={manualTitle} onChange={(e) => setManualTitle(e.target.value)} placeholder="例：○○が△△を発表、新たな展開へ" />
          <span className="help">
            入力すると自動取得タイトルより優先されます。OVO bizページはJSレンダリングでタイトルが取れない場合があるため、タイトルを入力すると確実に検索に使用されます。
          </span>
        </div>

        <div className="field">
          <label htmlFor="kw">補足キーワード（スペース区切り・入力すると全クローラーで優先使用）</label>
          <input id="kw" type="text" value={manualKeywords} onChange={(e) => setManualKeywords(e.target.value)} placeholder="例: 住宅用火災警報器　または　MIXI MEETUP　または　ウェルネスチャレンジ" />
          <span className="help">
            自動抽出では汎用語（設置・交換・AI等）が上位になりがちです。記事を一意に特定できる固有名詞をスペース区切りで入力すると、goo/livedoor/Yahoo/PR TIMESでの検索精度が大幅に向上します。
          </span>
        </div>

        <div className="grid3">
          <label className="check">
            <input type="checkbox" checked={debugMode} onChange={(e) => setDebugMode(e.target.checked)} />
            除外候補も表示（デバッグ用）
          </label>
          <label className="check">
            <input type="checkbox" checked={showSourceInfo} onChange={(e) => setShowSourceInfo(e.target.checked)} />
            元記事解析情報を表示
          </label>
        </div>

        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading && <span className="spinner" />}
          {loading ? "元記事解析・転載先検索中..." : "転載URLを検索"}
        </button>
      </form>

      {error && <div className="alert alert-error">{error}</div>}

      {result && (
        <>
          {showSourceInfo && result.source_contexts.length > 0 && (
            <section>
              <h2>元記事解析</h2>
              {result.source_contexts
                .filter((c) => !c.title)
                .map((c) => (
                  <div key={c.origin} className="alert alert-warn">
                    ⚠️ {c.origin} のタイトルが取得できませんでした。「記事タイトル」欄に正確なタイトルを入力してから再検索してください。
                  </div>
                ))}
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>元記事</th><th>URL</th><th>タイトル</th><th>source_id</th><th>媒体判定</th><th>判定モード</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.source_contexts.map((c) => (
                      <tr key={c.origin}>
                        <td>{c.origin}</td>
                        <td className="wrap">{c.url}</td>
                        <td className="wrap">{c.title}</td>
                        <td>{c.source_id}</td>
                        <td>{c.media}</td>
                        <td>{c.source_mode}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <section>
            <h2>抽出結果 ({rows.length}件 / 上限{result.max_results}件)</h2>
            <p className="caption">除外したい行だけチェックしてください。新しい検索を実行するとチェックは自動でクリアされます。</p>

            {rows.length === 0 ? (
              <div className="alert alert-warn">条件に合う転載URLが見つかりませんでした。</div>
            ) : (
              <>
                {rows.map((r, i) => {
                  const isExcluded = excluded.has(i);
                  return (
                    <div key={`${r.canonical_url ?? r.url}-${i}`} className={`result${isExcluded ? " excluded" : ""}`}>
                      <input type="checkbox" aria-label="除外" checked={isExcluded} onChange={() => toggleExclude(i)} />
                      <div>
                        <div className="result-head">
                          <strong>{i + 1}. {MEDIA_DISPLAY_NAMES[r.media] ?? r.media}</strong>
                          <span className="badge" style={{ background: isExcluded ? "var(--danger)" : "var(--primary)" }}>
                            {isExcluded ? "除外" : str(r.level)}
                          </span>
                          <span className="score">score={str(r.score)}</span>
                        </div>
                        {r.title && <div className="result-title">{r.title}</div>}
                        <div className="result-url">
                          <a href={safeHref(r.url)} target="_blank" rel="noopener noreferrer">{r.url}</a>
                        </div>
                      </div>
                    </div>
                  );
                })}

                <h2>
                  Word貼り付け用テキスト（{includedRows.length}件
                  {excluded.size > 0 && ` / 除外 ${excluded.size}件`}）
                </h2>
                <textarea
                  readOnly
                  value={wordText}
                  style={{ height: Math.max(220, Math.max(1, includedRows.length) * 48) }}
                />
                <div className="row-actions">
                  <button type="button" className="btn" onClick={copyWordText}>
                    {copied ? "コピーしました" : "クリップボードにコピー"}
                  </button>
                  <button type="button" className="btn" onClick={() => download(toCsv(rows), "reprint_results_v300.csv", "text/csv")}>
                    CSVダウンロード（全件）
                  </button>
                  <button type="button" className="btn" onClick={() => download(wordText, "reprint_results_v300.txt", "text/plain")}>
                    Word貼り付け用TXTダウンロード
                  </button>
                </div>
              </>
            )}
          </section>

          {debugMode && (
            <section>
              <h2>除外候補 ({result.debug_rows.length}件)</h2>
              {result.debug_rows.length === 0 ? (
                <div className="alert alert-info">除外候補はありません。（デバッグ表示をONにしてから検索すると表示されます）</div>
              ) : (
                <div className="table-wrap" style={{ maxHeight: 480 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>媒体</th><th>URL</th><th>タイトル</th><th>判定</th><th>スコア</th><th>HTTP</th><th>除外理由</th>
                        <th>タイトル一致</th><th>KW一致</th><th>source_id一致</th><th>検索エンジン</th><th>順位</th><th>根拠</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.debug_rows.map((r, i) => (
                        <tr key={i}>
                          <td>{r.media}</td>
                          <td className="wrap">{r.url}</td>
                          <td className="wrap">{str(r.title)}</td>
                          <td>{str(r.level)}</td>
                          <td>{str(r.score)}</td>
                          <td>{str(r.status_code)}</td>
                          <td>{str(r.reject_reason)}</td>
                          <td>{str(r.title_similarity)}</td>
                          <td>{str(r.keyword_overlap)}</td>
                          <td>{ids(r.matched_source_ids)}</td>
                          <td>{str(r.engine)}</td>
                          <td>{str(r.rank)}</td>
                          <td className="wrap">{str(r.score_reason)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}
