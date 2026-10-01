import sqlite3
from datetime import datetime

DB_PATH = "data.db"

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS search_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        media TEXT,
        url TEXT,
        canonical_url TEXT,
        title TEXT,
        level TEXT,
        score REAL,
        title_similarity REAL,
        keyword_overlap REAL,
        origin_label TEXT,
        engine TEXT,
        rank INTEGER,
        matched_source_ids TEXT,
        matched_origins TEXT,
        score_reason TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def save_results(rows):
    conn = get_conn()
    cur = conn.cursor()

    for r in rows:
        cur.execute("""
        INSERT INTO search_results (
            media, url, canonical_url, title, level, score,
            title_similarity, keyword_overlap, origin_label,
            engine, rank, matched_source_ids, matched_origins,
            score_reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r.get("media", ""),
            r.get("url", ""),
            r.get("canonical_url", ""),
            r.get("title", ""),
            r.get("level", ""),
            float(r.get("score", 0.0) or 0.0),
            float(r.get("title_similarity", 0.0) or 0.0),
            float(r.get("keyword_overlap", 0.0) or 0.0),
            r.get("origin_label", ""),
            r.get("engine", ""),
            int(r.get("rank", 9999) or 9999),
            ",".join(r.get("matched_source_ids", [])) if isinstance(r.get("matched_source_ids"), list) else str(r.get("matched_source_ids", "")),
            ",".join(r.get("matched_origins", [])) if isinstance(r.get("matched_origins"), list) else str(r.get("matched_origins", "")),
            r.get("score_reason", ""),
            datetime.utcnow().isoformat(timespec="seconds"),
        ))

    conn.commit()
    conn.close()