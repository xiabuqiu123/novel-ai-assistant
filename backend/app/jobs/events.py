"""Task-domain module: events (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import json
import re
from ..database import supersede_previous_run_facts
from ..database import upsert_extracted_fact
from .common import _chapter_lookup_for_novel
from .common import _norm_evidence
from .common import _optional_int
from .common import _resolve_character_chapter_id
from .orchestration import _extraction_batch_size
from .orchestration import _run_batched_fact_extraction_job
from typing import Any


EVENT_EXTRACTION_BATCH_SIZE = 10


def _event_extraction_schema() -> str:
    return (
        'Output a JSON object with an "events" list. Each event must be an object with:\n'
        '"title" (string, short event title),\n'
        '"description" (string, one-sentence description from provided evidence only),\n'
        '"time_context" (string, relative time/sequencing hint, e.g. "chapter 3" or "early morning"),\n'
        '"era" (string, story era label such as "五百年前" or "取经路上"; empty when undeterminable),\n'
        '"story_time_order" (integer, relative order within the story timeline; 0 when undeterminable),\n'
        '"entities" (list of strings, characters/objects involved),\n'
        '"source_chapters" (list of chapter numbers/ids where this event happens),\n'
        '"evidence" (list of objects, each with chapter_title and source_quote),\n'
        '"confidence" (string: one of "high", "medium", "low"),\n'
        '"status" ("pending_review").\n\n'
        "IMPORTANT RULES:\n"
        "- Only include events clearly described in the provided excerpts; do NOT invent plot events.\n"
        "- Order events by narrative chronology when possible.\n"
        "- Assign each event an era and a relative story_time_order (1, 2, 3, ...) when the excerpts allow; leave era empty and omit story_time_order when the story-time position cannot be determined (flashbacks, unclear multi-thread narration). Story-time ordering is an AI inference.\n"
        "- Every event must carry at least one source_quote from the provided excerpts.\n"
    )


def _event_extraction_batch_payload(
    batch_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
) -> str:
    first = batch_rows[0]
    last = batch_rows[-1]
    marker = (
        f"batch_chapter_range:{first['chapter_order']}-{last['chapter_order']} "
        f"batch_chapter_ids:{first['id']}-{last['id']}"
    )
    excerpts = "\n\n".join(
        f"chapter:{row['title']}\nsource_excerpt:{str(row['content'])[:2000]}" for row in full_rows
    )
    return _event_extraction_schema() + "\n\n" + marker + "\n\n" + excerpts


def _local_event_extraction(chapter_rows: list[dict[str, Any]]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for row in chapter_rows:
        order = int(row["chapter_order"])
        first_sentence = ""
        for piece in re.split(r"[。.!?！？\n]", row["content"].strip()):
            if piece.strip():
                first_sentence = piece.strip()[:120]
                break
        title = str(row["title"] or "").strip() or f"第{order}章"
        events.append({
            "title": title,
            "description": first_sentence or title,
            "time_context": f"第{order}章",
            "era": "",
            "story_time_order": None,
            "entities": [],
            "source_chapters": [order],
            "evidence": [{
                "chapter_id": int(row["id"]),
                "chapter_order": order,
                "chapter_title": row["title"],
                "source_quote": first_sentence or row["content"][:120],
            }],
            "confidence": "low",
            "status": "pending_review",
        })
    return {
        "status": "local_fallback",
        "task_type": "event_extraction",
        "events": events,
        "evidence": [e["evidence"][0] for e in events[:5]],
        "evidence_required": True,
    }


def _persist_event_facts(
    conn, novel_id: int, result: dict[str, Any], job_id: int,
    seen_keys: set[tuple[str, int]] | None = None,
) -> int:
    events = result.get("events")
    if not isinstance(events, list):
        return 0
    chapter_lookup = _chapter_lookup_for_novel(conn, novel_id)
    if seen_keys is None:
        seen_keys = set()
    superseded = False
    persisted = 0
    for item in events:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        desc = str(item.get("description") or "").strip()
        content = f"{title}: {desc}" if title and desc else (title or desc)
        if not content.strip():
            continue
        raw_entities = item.get("entities") if isinstance(item.get("entities"), list) else []
        entities = [str(e).strip() for e in raw_entities if str(e).strip()]
        ev = _norm_evidence(item.get("evidence"))
        first = ev[0] if ev else {}
        chapter_id = _resolve_character_chapter_id(item, first, chapter_lookup)
        source_quote = str(first.get("source_quote") or "")
        chapter_order = _optional_int(item.get("chapter_order"))
        if chapter_order is None:
            chapter_order = _optional_int(first.get("chapter_order"))
        chapter_title = str(item.get("chapter_title") or first.get("chapter_title") or "").strip()
        if chapter_id is not None:
            chapter_row = conn.execute(
                "SELECT chapter_order, title FROM chapters WHERE id = ?", (chapter_id,)
            ).fetchone()
            if chapter_row is not None:
                if not chapter_order or chapter_order == 0:
                    chapter_order = int(chapter_row["chapter_order"])
                if not chapter_title:
                    chapter_title = str(chapter_row["title"] or "")
        dedup_key = (title.strip().lower(), chapter_order or 0)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        if not superseded:
            supersede_previous_run_facts(
                conn, novel_id=novel_id, fact_type="event", current_run_id=job_id
            )
            superseded = True
        extra = {
            "time_context": str(item.get("time_context") or ""),
            "era": str(item.get("era") or "").strip(),
            "story_time_order": _optional_int(item.get("story_time_order")) or 0,
            "event_order": persisted + 1,
            "chapter_order": chapter_order or 0,
            "chapter_title": chapter_title,
        }
        upsert_extracted_fact(
            conn,
            novel_id=novel_id,
            fact_type="event",
            content=content,
            entities=entities,
            chapter_id=chapter_id,
            source_quote=source_quote,
            confidence=str(item.get("confidence") or "low"),
            status="pending_review",
            model_run_id=job_id,
            evidence=ev,
            extra=extra,
        )
        persisted += 1
    return persisted


def backfill_event_chapter_order(conn) -> int:
    """Repair legacy event facts whose extra.chapter_order is missing/0 by
    resolving chapter_order (and title) from the persisted chapter_id."""
    rows = conn.execute(
        "SELECT id, chapter_id, extra_json FROM extracted_facts WHERE fact_type = 'event'",
    ).fetchall()
    updated = 0
    for row in rows:
        chapter_id = row["chapter_id"]
        if chapter_id is None:
            continue
        try:
            extra = json.loads(row["extra_json"]) if row["extra_json"] else {}
        except json.JSONDecodeError:
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        current_order = extra.get("chapter_order")
        if isinstance(current_order, (int, float)) and current_order and current_order != 0:
            continue
        chapter_row = conn.execute(
            "SELECT chapter_order, title FROM chapters WHERE id = ?", (int(chapter_id),)
        ).fetchone()
        if chapter_row is None:
            continue
        extra["chapter_order"] = int(chapter_row["chapter_order"])
        if not str(extra.get("chapter_title") or "").strip():
            extra["chapter_title"] = str(chapter_row["title"] or "")
        with conn:
            conn.execute(
                "UPDATE extracted_facts SET extra_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (json.dumps(extra, ensure_ascii=False), int(row["id"])),
            )
        updated += 1
    return updated


async def _run_event_extraction_job(conn, novel_id: int, model: str, force_refresh: bool, job_id: int) -> dict[str, Any]:
    return await _run_batched_fact_extraction_job(
        conn, novel_id, model, force_refresh, job_id,
        task_type="event_extraction",
        batch_size=_extraction_batch_size(
            conn, "event_extraction_batch_size", EVENT_EXTRACTION_BATCH_SIZE
        ),
        payload_builder=_event_extraction_batch_payload,
        persist_fn=_persist_event_facts,
        local_fn=_local_event_extraction,
        job_label="event_extraction",
    )