from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .text_processing import ChapterDraft, ChunkDraft, sha256_text, split_chunks

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS novels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    encoding TEXT NOT NULL,
    text_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id INTEGER NOT NULL,
    chapter_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    UNIQUE(novel_id, chapter_order),
    FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL,
    chunk_order INTEGER NOT NULL,
    content TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    UNIQUE(chapter_id, chunk_order),
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS model_cache (
    cache_key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    task_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id INTEGER,
    chapter_id INTEGER,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    result_cache_key TEXT NOT NULL DEFAULT '',
    request_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS extracted_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    novel_id INTEGER NOT NULL,
    fact_type TEXT NOT NULL,
    content TEXT NOT NULL,
    entities_json TEXT NOT NULL DEFAULT '[]',
    chapter_id INTEGER,
    chunk_id INTEGER,
    source_quote TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    extra_json TEXT NOT NULL DEFAULT '{}',
    confidence TEXT NOT NULL DEFAULT 'low',
    status TEXT NOT NULL DEFAULT 'pending_review',
    model_run_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(novel_id) REFERENCES novels(id) ON DELETE CASCADE,
    FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE SET NULL,
    FOREIGN KEY(model_run_id) REFERENCES analysis_jobs(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_extracted_facts_novel_type_status
ON extracted_facts(novel_id, fact_type, status);
CREATE TABLE IF NOT EXISTS review_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_type TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate_schema(conn)
    return conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(analysis_jobs)").fetchall()
    }
    if "request_json" not in columns:
        conn.execute("ALTER TABLE analysis_jobs ADD COLUMN request_json TEXT NOT NULL DEFAULT '{}'")
    fact_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(extracted_facts)").fetchall()
    }
    if "evidence_json" not in fact_columns:
        conn.execute("ALTER TABLE extracted_facts ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]'")
    if "extra_json" not in fact_columns:
        conn.execute("ALTER TABLE extracted_facts ADD COLUMN extra_json TEXT NOT NULL DEFAULT '{}'")


def import_novel(
    conn: sqlite3.Connection,
    *,
    title: str,
    source_filename: str,
    encoding: str,
    text_hash: str,
    chapters: list[ChapterDraft],
    chunk_size: int = 6000,
) -> dict[str, Any]:
    existing = conn.execute("SELECT id FROM novels WHERE text_hash = ?", (text_hash,)).fetchone()
    if existing:
        existing_novel = get_novel(conn, int(existing["id"]))
        return existing_novel | {
            "imported": False,
            "duplicate_of": existing_novel,
            "requested_title": title,
            "requested_source_filename": source_filename,
        }

    with conn:
        cur = conn.execute(
            "INSERT INTO novels(title, source_filename, encoding, text_hash) VALUES (?, ?, ?, ?)",
            (title, source_filename, encoding, text_hash),
        )
        novel_id = int(cur.lastrowid)
        chapter_count = 0
        chunk_count = 0
        for chapter in chapters:
            chapter_hash = sha256_text(chapter.content)
            chapter_cur = conn.execute(
                """
                INSERT INTO chapters(novel_id, chapter_order, title, content, text_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (novel_id, chapter.order, chapter.title, chapter.content, chapter_hash),
            )
            chapter_id = int(chapter_cur.lastrowid)
            chapter_count += 1
            overlap = min(300, max(0, chunk_size // 10))
            for chunk in split_chunks(chapter.content, max_chars=chunk_size, overlap=overlap):
                _insert_chunk(conn, chapter_id, chunk)
                chunk_count += 1
    return get_novel(conn, novel_id) | {"imported": True, "chapter_count": chapter_count, "chunk_count": chunk_count}


def _insert_chunk(conn: sqlite3.Connection, chapter_id: int, chunk: ChunkDraft) -> None:
    conn.execute(
        "INSERT INTO chunks(chapter_id, chunk_order, content, text_hash) VALUES (?, ?, ?, ?)",
        (chapter_id, chunk.order, chunk.content, chunk.text_hash),
    )


def get_novel(conn: sqlite3.Connection, novel_id: int) -> dict[str, Any]:
    novel = conn.execute("SELECT * FROM novels WHERE id = ?", (novel_id,)).fetchone()
    if novel is None:
        raise KeyError(f"novel {novel_id} not found")
    counts = conn.execute(
        """
        SELECT COUNT(DISTINCT chapters.id) AS chapter_count, COUNT(chunks.id) AS chunk_count
        FROM chapters LEFT JOIN chunks ON chunks.chapter_id = chapters.id
        WHERE chapters.novel_id = ?
        """,
        (novel_id,),
    ).fetchone()
    return dict(novel) | {"chapter_count": counts["chapter_count"], "chunk_count": counts["chunk_count"]}


def list_novels(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id FROM novels ORDER BY created_at DESC, id DESC").fetchall()
    return [get_novel(conn, int(row["id"])) for row in rows]


def delete_novel(conn: sqlite3.Connection, novel_id: int) -> dict[str, Any]:
    return delete_novel_with_cache_keys(conn, novel_id, [])


def delete_novel_with_cache_keys(
    conn: sqlite3.Connection,
    novel_id: int,
    extra_cache_keys: list[str],
) -> dict[str, Any]:
    novel = get_novel(conn, novel_id)
    cache_rows = conn.execute(
        """
        SELECT DISTINCT result_cache_key FROM analysis_jobs
        WHERE novel_id = ? AND result_cache_key != ''
        """,
        (novel_id,),
    ).fetchall()
    cache_keys = _unique_cache_keys([str(row["result_cache_key"]) for row in cache_rows] + extra_cache_keys)
    existing_cache_keys = _existing_cache_keys(conn, cache_keys)
    with conn:
        if existing_cache_keys:
            conn.executemany("DELETE FROM model_cache WHERE cache_key = ?", [(key,) for key in existing_cache_keys])
        conn.execute("DELETE FROM novels WHERE id = ?", (novel_id,))
    return {
        "deleted": True,
        "novel_id": novel_id,
        "title": novel["title"],
        "deleted_cache_entries": len(existing_cache_keys),
    }


def clear_novel_cache(conn: sqlite3.Connection, novel_id: int, task_type: str | None = None) -> dict[str, Any]:
    return clear_novel_cache_with_keys(conn, novel_id, task_type, [])


def clear_novel_cache_with_keys(
    conn: sqlite3.Connection,
    novel_id: int,
    task_type: str | None = None,
    extra_cache_keys: list[str] | None = None,
) -> dict[str, Any]:
    novel = get_novel(conn, novel_id)
    params: list[Any] = [novel_id]
    task_filter = ""
    if task_type:
        task_filter = " AND task_type = ?"
        params.append(task_type)
    cache_rows = conn.execute(
        f"""
        SELECT DISTINCT result_cache_key FROM analysis_jobs
        WHERE novel_id = ? AND result_cache_key != ''{task_filter}
        """,
        params,
    ).fetchall()
    cache_keys = _unique_cache_keys([str(row["result_cache_key"]) for row in cache_rows] + list(extra_cache_keys or []))
    existing_cache_keys = _existing_cache_keys(conn, cache_keys)
    with conn:
        if existing_cache_keys:
            conn.executemany("DELETE FROM model_cache WHERE cache_key = ?", [(key,) for key in existing_cache_keys])
        conn.execute(
            f"""
            UPDATE analysis_jobs SET result_cache_key = '', updated_at = CURRENT_TIMESTAMP
            WHERE novel_id = ? AND result_cache_key != ''{task_filter}
            """,
            params,
        )
    return {
        "cleared": True,
        "novel_id": novel_id,
        "title": novel["title"],
        "task_type": task_type or "all",
        "deleted_cache_entries": len(existing_cache_keys),
    }


def _unique_cache_keys(cache_keys: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for key in cache_keys:
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def _existing_cache_keys(conn: sqlite3.Connection, cache_keys: list[str]) -> list[str]:
    if not cache_keys:
        return []
    placeholders = ",".join("?" for _ in cache_keys)
    rows = conn.execute(
        f"SELECT cache_key FROM model_cache WHERE cache_key IN ({placeholders})",
        cache_keys,
    ).fetchall()
    existing = {str(row["cache_key"]) for row in rows}
    return [key for key in cache_keys if key in existing]


def list_chapters(conn: sqlite3.Connection, novel_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, chapter_order, title, length(content) AS char_count FROM chapters WHERE novel_id = ? ORDER BY chapter_order",
        (novel_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_chapter(conn: sqlite3.Connection, chapter_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
    if row is None:
        raise KeyError(f"chapter {chapter_id} not found")
    return dict(row)


def get_chunks_for_chapter(conn: sqlite3.Connection, chapter_id: int) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM chunks WHERE chapter_id = ? ORDER BY chunk_order", (chapter_id,)).fetchall()
    return [dict(row) for row in rows]


def get_cache(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT output_json FROM model_cache WHERE cache_key = ?", (key,)).fetchone()
    if row is None:
        return None
    return json.loads(row["output_json"])


def put_cache(
    conn: sqlite3.Connection,
    *,
    key: str,
    model: str,
    task_type: str,
    input_hash_value: str,
    output: dict[str, Any],
) -> None:
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO model_cache(cache_key, model, task_type, input_hash, output_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, model, task_type, input_hash_value, json.dumps(output, ensure_ascii=False)),
        )


def create_analysis_job(
    conn: sqlite3.Connection,
    *,
    task_type: str,
    novel_id: int | None = None,
    chapter_id: int | None = None,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with conn:
        cur = conn.execute(
            """
            INSERT INTO analysis_jobs(novel_id, chapter_id, task_type, status, progress, request_json)
            VALUES (?, ?, ?, 'queued', 0, ?)
            """,
            (novel_id, chapter_id, task_type, json.dumps(request or {}, ensure_ascii=False)),
        )
    return get_analysis_job(conn, int(cur.lastrowid))


def update_analysis_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str | None = None,
    progress: int | None = None,
    error: str | None = None,
    result_cache_key: str | None = None,
) -> dict[str, Any]:
    fields: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("status", status),
        ("progress", progress),
        ("error", error),
        ("result_cache_key", result_cache_key),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    if not fields:
        return get_analysis_job(conn, job_id)
    fields.append("updated_at = CURRENT_TIMESTAMP")
    values.append(job_id)
    with conn:
        conn.execute(f"UPDATE analysis_jobs SET {', '.join(fields)} WHERE id = ?", values)
    return get_analysis_job(conn, job_id)


def get_analysis_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"analysis job {job_id} not found")
    return dict(row)


def list_analysis_jobs(conn: sqlite3.Connection, novel_id: int | None = None) -> list[dict[str, Any]]:
    if novel_id is None:
        rows = conn.execute("SELECT * FROM analysis_jobs ORDER BY id DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM analysis_jobs WHERE novel_id = ? ORDER BY id DESC",
            (novel_id,),
        ).fetchall()
    return [_job_from_row(conn, row) for row in rows]


def usage_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate cumulative model-usage statistics from existing data.

    No usage/token telemetry is stored today (model responses are not parsed
    for token counts and no usage table exists), so token statistics are
    reported as unavailable. Call counts come from model_cache provenance
    metadata: every row is one cached result, and provider_call_attempted /
    provider_call_succeeded mark real API calls. Failed calls that were never
    cached are not visible here, so failed_jobs (analysis_jobs) is the closest
    available approximation of the failure side.
    """
    total = 0
    attempted = 0
    succeeded = 0
    local_fallback = 0
    for row in conn.execute("SELECT output_json FROM model_cache").fetchall():
        total += 1
        try:
            output = json.loads(str(row["output_json"]))
        except (TypeError, ValueError):
            continue
        meta = output.get("_cache_metadata")
        if not isinstance(meta, dict):
            meta = {}
        if bool(meta.get("provider_call_attempted")):
            attempted += 1
        if bool(meta.get("provider_call_succeeded")):
            succeeded += 1
        if str(meta.get("source") or "") == "local_fallback":
            local_fallback += 1
    failed_row = conn.execute(
        "SELECT COUNT(*) AS n FROM analysis_jobs WHERE status = 'failed'"
    ).fetchone()
    return {
        "cache_entries": total,
        "model_calls_attempted": attempted,
        "model_calls_succeeded": succeeded,
        "local_fallback_results": local_fallback,
        "failed_jobs": int(failed_row["n"] or 0) if failed_row is not None else 0,
        "token_stats_available": False,
    }


def _job_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    request = _safe_json_object(str(job.get("request_json") or "{}"))
    job["requested_model"] = str(request.get("model") or "")
    job["effective_model"] = str(request.get("effective_model") or request.get("model") or "")
    result_cache_key = str(job.get("result_cache_key") or "")
    if result_cache_key:
        cached = get_cache(conn, result_cache_key)
        if cached is not None:
            raw_metadata = cached.get("_cache_metadata")
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            source = str(metadata.get("source") or cached.get("source") or "")
            if source == "remote_model":
                job["cache_source"] = "cached_remote_model"
            elif source in {"local_fallback", "cached_local_fallback"} or cached.get("status") in {
                "local_fallback",
                "needs_api_key",
            }:
                job["cache_source"] = "cached_local_fallback"
            elif source == "mixed":
                job["cache_source"] = "cached_partial"
            else:
                job["cache_source"] = "cached_remote_model"
            job["model_error"] = str(metadata.get("model_error") or cached.get("model_error") or job.get("error") or "")
            job["provider_call_attempted"] = bool(metadata.get("provider_call_attempted"))
            job["provider_call_succeeded"] = bool(metadata.get("provider_call_succeeded"))
            job["local_fallback"] = job["cache_source"] == "cached_local_fallback"
    job.setdefault("cache_source", "")
    job.setdefault("model_error", str(job.get("error") or ""))
    job.setdefault("provider_call_attempted", False)
    job.setdefault("provider_call_succeeded", False)
    job.setdefault("local_fallback", False)
    return job


def _safe_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def upsert_extracted_fact(
    conn: sqlite3.Connection,
    *,
    novel_id: int,
    fact_type: str,
    content: str,
    entities: list[str] | None = None,
    chapter_id: int | None = None,
    chunk_id: int | None = None,
    source_quote: str = "",
    confidence: str = "low",
    status: str = "pending_review",
    model_run_id: int | None = None,
    evidence: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_content = content.strip()
    normalized_quote = source_quote.strip()
    if not normalized_content:
        raise ValueError("extracted fact content is required")
    existing = conn.execute(
        """
        SELECT * FROM extracted_facts
        WHERE novel_id = ? AND fact_type = ? AND content = ?
          AND IFNULL(chapter_id, 0) = IFNULL(?, 0)
          AND source_quote = ?
          AND status != 'superseded'
        ORDER BY id LIMIT 1
        """,
        (novel_id, fact_type, normalized_content, chapter_id, normalized_quote),
    ).fetchone()
    entities_json = json.dumps(entities or [], ensure_ascii=False)
    evidence_json = json.dumps(evidence or [], ensure_ascii=False)
    extra_json = json.dumps(extra or {}, ensure_ascii=False)
    with conn:
        if existing is not None:
            conn.execute(
                """
                UPDATE extracted_facts
                SET entities_json = ?, chunk_id = ?, confidence = ?, model_run_id = ?,
                    evidence_json = ?, extra_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    entities_json,
                    chunk_id,
                    confidence,
                    model_run_id,
                    evidence_json,
                    extra_json,
                    int(existing["id"]),
                ),
            )
            fact_id = int(existing["id"])
        else:
            cur = conn.execute(
                """
                INSERT INTO extracted_facts(
                    novel_id, fact_type, content, entities_json, chapter_id, chunk_id,
                    source_quote, evidence_json, extra_json, confidence, status, model_run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    novel_id,
                    fact_type,
                    normalized_content,
                    entities_json,
                    chapter_id,
                    chunk_id,
                    normalized_quote,
                    evidence_json,
                    extra_json,
                    confidence,
                    status,
                    model_run_id,
                ),
            )
            fact_id = int(cur.lastrowid)
    return get_extracted_fact(conn, fact_id)


def supersede_previous_run_facts(
    conn: sqlite3.Connection,
    *,
    novel_id: int,
    fact_type: str,
    current_run_id: int,
) -> int:
    """E4: flip active/pending_review facts from earlier runs to superseded.

    Superseded rows stay in the table as an audit trail (never physically
    deleted). Rows persisted by the current run, and rows already in a
    terminal review state (confirmed / dismissed / explained / watching), are
    left untouched.
    """
    with conn:
        cur = conn.execute(
            """
            UPDATE extracted_facts
            SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
            WHERE novel_id = ? AND fact_type = ?
              AND status IN ('active', 'pending_review')
              AND (model_run_id IS NULL OR model_run_id != ?)
            """,
            (novel_id, fact_type, current_run_id),
        )
        return int(cur.rowcount)


def get_extracted_fact(conn: sqlite3.Connection, fact_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM extracted_facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        raise KeyError(f"extracted fact {fact_id} not found")
    return _fact_from_row(row)


def list_extracted_facts(
    conn: sqlite3.Connection,
    novel_id: int,
    fact_type: str | None = None,
    status: str | None = None,
    *,
    include_superseded: bool = False,
) -> list[dict[str, Any]]:
    get_novel(conn, novel_id)
    filters = ["novel_id = ?"]
    params: list[Any] = [novel_id]
    if fact_type:
        filters.append("fact_type = ?")
        params.append(fact_type)
    if status:
        filters.append("status = ?")
        params.append(status)
    if not include_superseded:
        filters.append("status != 'superseded'")
    rows = conn.execute(
        f"SELECT * FROM extracted_facts WHERE {' AND '.join(filters)} ORDER BY id DESC",
        params,
    ).fetchall()
    return [_fact_from_row(row) for row in rows]


def update_review_status(
    conn: sqlite3.Connection,
    *,
    record_type: str,
    record_id: int,
    status: str,
    note: str = "",
) -> dict[str, Any]:
    if record_type != "extracted_fact":
        raise ValueError(f"unsupported review record_type: {record_type}")
    row = conn.execute("SELECT * FROM extracted_facts WHERE id = ?", (record_id,)).fetchone()
    if row is None:
        raise KeyError(f"extracted fact {record_id} not found")
    previous = str(row["status"])
    with conn:
        conn.execute(
            "UPDATE extracted_facts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, record_id),
        )
        conn.execute(
            """
            INSERT INTO review_actions(record_type, record_id, from_status, to_status, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record_type, record_id, previous, status, note.strip()),
        )
    return get_extracted_fact(conn, record_id)


def list_review_actions(conn: sqlite3.Connection, record_type: str, record_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM review_actions
        WHERE record_type = ? AND record_id = ?
        ORDER BY id
        """,
        (record_type, record_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _fact_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        entities = json.loads(row["entities_json"])
    except json.JSONDecodeError:
        entities = []
    if not isinstance(entities, list):
        entities = []
    try:
        evidence = json.loads(row["evidence_json"]) if "evidence_json" in row.keys() else []
    except json.JSONDecodeError:
        evidence = []
    if not isinstance(evidence, list):
        evidence = []
    try:
        extra = json.loads(row["extra_json"]) if "extra_json" in row.keys() else {}
    except json.JSONDecodeError:
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    data = dict(row)
    data["entities"] = entities
    data["evidence"] = evidence
    data["extra"] = extra
    data.pop("entities_json", None)
    data.pop("evidence_json", None)
    data.pop("extra_json", None)
    return data


def next_queued_analysis_job(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM analysis_jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
    ).fetchone()
    return None if row is None else dict(row)


def retry_analysis_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    with conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'queued', progress = 0, error = '', result_cache_key = '', retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )
    return get_analysis_job(conn, job_id)


def fail_stale_running_jobs(
    conn: sqlite3.Connection,
    *,
    error: str = "Backend restarted while this job was running; marked as failed so it can be retried.",
) -> int:
    """Mark zombie 'running' jobs as failed on backend startup (single-process backend)."""
    with conn:
        cur = conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """,
            (error,),
        )
    return int(cur.rowcount)


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return default if row is None else str(row["value"])
