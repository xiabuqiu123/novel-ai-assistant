"""Task-domain module: outlines (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import os
from ..cache import cache_key
from ..cache import input_hash
from ..database import get_cache
from ..database import get_chapter
from ..database import get_chunks_for_chapter
from ..database import list_chapters
from ..database import put_cache
from ..database import update_analysis_job
from ..provenance import with_model_provenance
from .cache import _cache_metadata
from .cache import _cached_model_task
from .cache import _cached_source
from .cache import _invalid_output_reason
from .cache import _model_error_text
from .cache import _task_cache_key
from .cache import _task_cache_keys
from .cache import _with_cache_metadata
from .common import _as_int
from .common import _normalize_model_output
from .common import _summary_snippets
from typing import Any
from .. import model_client
from .. import secrets
from .. import database


BOOK_OUTLINE_ARC_SIZE = 200


def _arc_summary_schema() -> str:
    return (
        "Summarize one story arc as JSON only. Return this shape: "
        '{"status":"ok","task_type":"arc_summary","arc":{"arc_index":0,"title":"string",'
        '"summary":"string","key_events":["string"],"characters":["string"]}}. '
        "Base everything only on the provided chapter briefs. Do not invent plot details."
    )


def _chapter_brief_for_arc(conn, chapter_row: dict[str, Any], model: str) -> str:
    chunks = get_chunks_for_chapter(conn, int(chapter_row["id"]))
    payload = _chapter_summary_payload(chapter_row, chunks)
    for key in _task_cache_keys(conn, "chapter_summary", payload, model):
        cached = get_cache(conn, key)
        if cached is None:
            continue
        parsed = cached.get("parsed_json") if isinstance(cached.get("parsed_json"), dict) else {}
        summary = str(parsed.get("short_summary") or cached.get("short_summary") or "").strip()
        if summary:
            return summary[:400]
    snippets = _summary_snippets(str(chapter_row.get("content") or ""), limit=2)
    return " ".join(snippets)[:300]


def _arc_summary_payload(arc_index: int, briefs: list[dict[str, Any]]) -> str:
    lines = "\n".join(
        f"chapter_order:{item['chapter_order']} title:{item['title']}\nbrief:{item['brief']}" for item in briefs
    )
    first = briefs[0]["chapter_order"]
    last = briefs[-1]["chapter_order"]
    return f"{_arc_summary_schema()}\n\narc_index:{arc_index} arc_chapter_range:{first}-{last}\n\nchapter_briefs:\n{lines}"


def _extract_arc_summary(
    result: dict[str, Any],
    arc_index: int,
    briefs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    parsed = result.get("parsed_json") if isinstance(result.get("parsed_json"), dict) else {}
    arc = result.get("arc") if isinstance(result.get("arc"), dict) else None
    if arc is None and isinstance(parsed.get("arc"), dict):
        arc = parsed["arc"]
    if arc is None and str(parsed.get("summary") or "").strip():
        arc = parsed
    if arc is None:
        return None
    summary = str(arc.get("summary") or "").strip()
    title = str(arc.get("title") or "").strip()
    if not summary and not title:
        return None
    return {
        "arc_index": arc_index,
        "title": title or f"Arc {arc_index + 1}",
        "summary": summary,
        "chapter_start": briefs[0]["chapter_order"],
        "chapter_end": briefs[-1]["chapter_order"],
        "key_events": arc.get("key_events") if isinstance(arc.get("key_events"), list) else [],
        "characters": arc.get("characters") if isinstance(arc.get("characters"), list) else [],
    }


def _local_arc_summary(arc_index: int, briefs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "local_fallback",
        "task_type": "arc_summary",
        "arc": {
            "arc_index": arc_index,
            "title": f"Arc {arc_index + 1} (chapters {briefs[0]['chapter_order']}-{briefs[-1]['chapter_order']})",
            "summary": " ".join(item["brief"] for item in briefs[:3])[:600],
            "key_events": [],
            "characters": [],
        },
    }


def _layered_book_outline_schema() -> str:
    return (
        "Generate a whole-book outline as JSON only. Return this shape: "
        '{"status":"ok","task_type":"book_outline","outline":{"title":"string",'
        '"chapters":[{"chapter_order":1,"chapter_title":"string","brief":"string"}]},'
        '"evidence":[{"chapter_order":1,"chapter_title":"string","source_quote":"string"}]}. '
        "Each chapters entry represents one story arc: use the arc start chapter_order as chapter_order, "
        "the arc title as chapter_title, and a multi-sentence arc brief. "
        "Use only the provided arc summaries. Do not invent arcs or chapters."
    )


def _layered_book_outline_payload(arcs: list[dict[str, Any]]) -> str:
    lines = "\n\n".join(
        f"arc_index:{arc['arc_index']} chapter_range:{arc['chapter_start']}-{arc['chapter_end']}\n"
        f"title:{arc['title']}\nsummary:{arc['summary']}"
        for arc in arcs
    )
    return f"{_layered_book_outline_schema()}\n\narc_summaries:\n{lines}"


def _local_layered_book_outline(arcs: list[dict[str, Any]]) -> dict[str, Any]:
    chapters = [
        {
            "chapter_order": arc["chapter_start"],
            "chapter_title": arc["title"],
            "brief": str(arc.get("summary") or "")[:360],
            "arc_start": arc["chapter_start"],
            "arc_end": arc["chapter_end"],
        }
        for arc in arcs
    ]
    return {
        "status": "local_fallback",
        "task_type": "book_outline",
        "outline": {"title": "Local arc outline", "chapters": chapters},
        "arcs": arcs,
        "evidence": [],
        "suggestions": ["Set an API key for model-generated whole-book outlines."],
        "evidence_required": True,
    }


async def _run_layered_book_outline(
    conn,
    chapter_rows: list[dict[str, Any]],
    model: str,
    force_refresh: bool,
    job_id: int,
) -> dict[str, Any]:
    arc_groups = [
        chapter_rows[start:start + BOOK_OUTLINE_ARC_SIZE]
        for start in range(0, len(chapter_rows), BOOK_OUTLINE_ARC_SIZE)
    ]
    arcs: list[dict[str, Any]] = []
    update_analysis_job(conn, job_id, status="running", progress=5)
    for arc_index, group in enumerate(arc_groups):
        briefs = []
        for row in group:
            full = get_chapter(conn, int(row["id"]))
            briefs.append(
                {
                    "chapter_order": int(row["chapter_order"]),
                    "title": str(row["title"] or ""),
                    "brief": _chapter_brief_for_arc(conn, full, model),
                }
            )
        payload = _arc_summary_payload(arc_index, briefs)
        result = await _cached_model_task(
            conn,
            "arc_summary",
            payload,
            model,
            force_refresh,
            job_id=None,
            fallback_output=_local_arc_summary(arc_index, briefs),
        )
        arc = _extract_arc_summary(result, arc_index, briefs)
        if arc is None:
            error = f"arc_summary {arc_index + 1}/{len(arc_groups)} failed: model output missing usable arc summary"
            update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
            return with_model_provenance(
                result,
                task_type="arc_summary",
                model_used=model,
                cache_hit=False,
                input_hash_value=input_hash("arc_summary", payload),
                cache_key_value=_task_cache_key("arc_summary", payload, model),
                job_id=job_id,
                model_error=error,
            )
        arcs.append(arc)
        update_analysis_job(conn, job_id, status="running", progress=5 + 80 * (arc_index + 1) // len(arc_groups))
    payload = _layered_book_outline_payload(arcs)
    result = await _cached_model_task(
        conn,
        "book_outline",
        payload,
        model,
        force_refresh,
        job_id=job_id,
        fallback_output=_local_layered_book_outline(arcs),
    )
    return result | {"arcs": arcs}


async def _run_stage_outline_model_phase(
    conn,
    payload: str,
    model: str,
    job_id: int,
    max_order: int,
    base_progress: int = 20,
) -> tuple[dict[str, Any], str | None]:
    """Call the stage-outline model up to twice (invalid output is retried once).
    Returns (output, invalid_reason); on transport error the output carries a
    model_error and the reason is that error. Invalid outputs are never cached."""
    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    base_url = database.get_setting(conn, "base_url")
    if not (api_key or os.getenv("OPENAI_API_KEY")):
        return {"status": "needs_api_key", "task_type": "book_stage_outline"}, "needs_api_key"
    last_output: dict[str, Any] = {}
    last_invalid = ""
    for attempt in range(2):
        update_analysis_job(conn, job_id, status="running", progress=base_progress + 25 * attempt)
        try:
            raw = await model_client.call_openai_compatible(
                task_type="book_stage_outline",
                user_payload=payload,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as exc:
            error = _model_error_text(exc)
            last_output = {"status": "error", "task_type": "book_stage_outline", "model_error": error}
            last_invalid = error
            break
        output = _with_cache_metadata(
            _normalize_model_output(raw, "book_stage_outline"),
            source="remote_model",
            provider_call_attempted=True,
            provider_call_succeeded=True,
        )
        invalid = _invalid_output_reason(output, "book_stage_outline")
        if invalid is None and not _stage_outline_bounds_ok(output, max_order):
            invalid = f"stage outline chapters out of bounds for book with {max_order} chapters"
        last_output = output
        last_invalid = invalid or ""
        if invalid is None:
            break
    return last_output, (last_invalid or None)


async def _run_book_stage_outline_job(
    conn,
    novel_id: int,
    model: str,
    force_refresh: bool,
    job_id: int,
) -> dict[str, Any]:
    chapter_rows = list_chapters(conn, novel_id)
    if not chapter_rows:
        raise ValueError("novel not found or has no chapters")
    max_order = max(int(row["chapter_order"] or 0) for row in chapter_rows)
    update_analysis_job(conn, job_id, status="running", progress=5)

    if len(chapter_rows) > BOOK_OUTLINE_ARC_SIZE:
        return await _run_layered_book_stage_outline(conn, chapter_rows, model, force_refresh, job_id, max_order)

    payload = _book_stage_outline_flat_payload(conn, chapter_rows, model)
    hash_value = input_hash("book_stage_outline", payload)
    key = cache_key(model=model, task_type="book_stage_outline", input_hash_value=hash_value)

    if not force_refresh:
        cached = get_cache(conn, key)
        if cached is not None:
            cached = _normalize_model_output(cached, "book_stage_outline")
            if _invalid_output_reason(cached, "book_stage_outline") is None:
                update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
                cache_meta = _cache_metadata(cached)
                return with_model_provenance(
                    cached,
                    task_type="book_stage_outline",
                    model_used=model,
                    cache_hit=True,
                    input_hash_value=hash_value,
                    cache_key_value=key,
                    job_id=job_id,
                    source=_cached_source(cache_meta, cached),
                    model_error=cache_meta.get("model_error") or cached.get("model_error"),
                    provider_call_attempted=False,
                    provider_call_succeeded=False,
                )

    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    if not (api_key or os.getenv("OPENAI_API_KEY")):
        fallback_output = _local_stage_outline(chapter_rows)
        output = _with_cache_metadata(
            fallback_output,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )
        put_cache(conn, key=key, model=model, task_type="book_stage_outline", input_hash_value=hash_value, output=output)
        update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
        return with_model_provenance(
            output,
            task_type="book_stage_outline",
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=job_id,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )

    output, invalid_reason = await _run_stage_outline_model_phase(conn, payload, model, job_id, max_order)
    if invalid_reason is None:
        put_cache(conn, key=key, model=model, task_type="book_stage_outline", input_hash_value=hash_value, output=output)
        update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
        return with_model_provenance(
            output,
            task_type="book_stage_outline",
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=job_id,
            source="remote_model",
            provider_call_attempted=True,
            provider_call_succeeded=True,
        )
    update_analysis_job(conn, job_id, status="failed", progress=100, error=invalid_reason)
    return with_model_provenance(
        output,
        task_type="book_stage_outline",
        model_used=model,
        cache_hit=False,
        input_hash_value=hash_value,
        cache_key_value=key,
        job_id=job_id,
        source="remote_model",
        model_error=invalid_reason,
        provider_call_attempted=True,
        provider_call_succeeded=False,
    )


async def _run_layered_book_stage_outline(
    conn,
    chapter_rows: list[dict[str, Any]],
    model: str,
    force_refresh: bool,
    job_id: int,
    max_order: int,
) -> dict[str, Any]:
    """>BOOK_OUTLINE_ARC_SIZE chapters: first summarize arcs, then ask the model
    for the stage outline from arc summaries only (layered long-text strategy)."""
    arc_groups = [
        chapter_rows[start:start + BOOK_OUTLINE_ARC_SIZE]
        for start in range(0, len(chapter_rows), BOOK_OUTLINE_ARC_SIZE)
    ]
    arcs: list[dict[str, Any]] = []
    update_analysis_job(conn, job_id, status="running", progress=5)
    for arc_index, group in enumerate(arc_groups):
        briefs = []
        for row in group:
            full = get_chapter(conn, int(row["id"]))
            briefs.append(
                {
                    "chapter_order": int(row["chapter_order"]),
                    "title": str(row["title"] or ""),
                    "brief": _chapter_brief_for_arc(conn, full, model),
                }
            )
        payload = _arc_summary_payload(arc_index, briefs)
        result = await _cached_model_task(
            conn,
            "arc_summary",
            payload,
            model,
            force_refresh,
            job_id=None,
            fallback_output=_local_arc_summary(arc_index, briefs),
        )
        arc = _extract_arc_summary(result, arc_index, briefs)
        if arc is None:
            error = f"arc_summary {arc_index + 1}/{len(arc_groups)} failed: model output missing usable arc summary"
            update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
            return with_model_provenance(
                result,
                task_type="arc_summary",
                model_used=model,
                cache_hit=False,
                input_hash_value=input_hash("arc_summary", payload),
                cache_key_value=_task_cache_key("arc_summary", payload, model),
                job_id=job_id,
                model_error=error,
            )
        arcs.append(arc)
        update_analysis_job(conn, job_id, status="running", progress=5 + 40 * (arc_index + 1) // len(arc_groups))

    stage_payload = _book_stage_outline_arc_payload(arcs)
    hash_value = input_hash("book_stage_outline", stage_payload)
    key = cache_key(model=model, task_type="book_stage_outline", input_hash_value=hash_value)

    if not force_refresh:
        cached = get_cache(conn, key)
        if cached is not None:
            cached = _normalize_model_output(cached, "book_stage_outline")
            if _invalid_output_reason(cached, "book_stage_outline") is None:
                update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
                cache_meta = _cache_metadata(cached)
                return with_model_provenance(
                    cached,
                    task_type="book_stage_outline",
                    model_used=model,
                    cache_hit=True,
                    input_hash_value=hash_value,
                    cache_key_value=key,
                    job_id=job_id,
                    source=_cached_source(cache_meta, cached),
                    model_error=cache_meta.get("model_error") or cached.get("model_error"),
                    provider_call_attempted=False,
                    provider_call_succeeded=False,
                ) | {"arcs": arcs}

    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    if not (api_key or os.getenv("OPENAI_API_KEY")):
        fallback_output = _local_stage_outline(chapter_rows)
        output = _with_cache_metadata(
            fallback_output,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )
        put_cache(conn, key=key, model=model, task_type="book_stage_outline", input_hash_value=hash_value, output=output)
        update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
        return with_model_provenance(
            output,
            task_type="book_stage_outline",
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=job_id,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        ) | {"arcs": arcs}

    output, invalid_reason = await _run_stage_outline_model_phase(
        conn, stage_payload, model, job_id, max_order, base_progress=50
    )
    if invalid_reason is None:
        put_cache(conn, key=key, model=model, task_type="book_stage_outline", input_hash_value=hash_value, output=output)
        update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
        return with_model_provenance(
            output,
            task_type="book_stage_outline",
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=job_id,
            source="remote_model",
            provider_call_attempted=True,
            provider_call_succeeded=True,
        ) | {"arcs": arcs}
    update_analysis_job(conn, job_id, status="failed", progress=100, error=invalid_reason)
    return with_model_provenance(
        output,
        task_type="book_stage_outline",
        model_used=model,
        cache_hit=False,
        input_hash_value=hash_value,
        cache_key_value=key,
        job_id=job_id,
        source="remote_model",
        model_error=invalid_reason,
        provider_call_attempted=True,
        provider_call_succeeded=False,
    ) | {"arcs": arcs}


def _book_outline_schema() -> str:
    return (
        'Generate a whole-book outline as JSON only. Return this shape: ' 
        '{"status":"ok","task_type":"book_outline","outline":{"title":"string",'
        '"chapters":[{"chapter_order":1,"chapter_title":"string","brief":"string"}]},'
        '"evidence":[{"chapter_order":1,"chapter_title":"string","source_quote":"string"}]}. ' 
        'Use chapter_order from the input. Do not invent chapters. If a field is unknown, use an empty string, ' 
        'but still keep the exact JSON field names.'
    )


def _book_outline_payload(conn, chapter_rows: list[dict[str, Any]], model: str) -> str:
    """Feed per-chapter summaries (cached chapter_summary preferred, local
    snippet fallback otherwise) instead of only title+char_count so the model
    can draft meaningful per-arc briefs. B1 restoration."""
    blocks = []
    for row in chapter_rows:
        chapter_row = get_chapter(conn, int(row["id"]))
        chunks = get_chunks_for_chapter(conn, int(row["id"]))
        cs_payload = _chapter_summary_payload(chapter_row, chunks)
        brief = ""
        key_events = ""
        source = "local_snippet_fallback"
        for key in _task_cache_keys(conn, "chapter_summary", cs_payload, model):
            cached = get_cache(conn, key)
            if cached is None:
                continue
            parsed = cached.get("parsed_json") if isinstance(cached.get("parsed_json"), dict) else {}
            summary = str(parsed.get("short_summary") or cached.get("short_summary") or "").strip()
            if summary:
                brief = summary[:400]
                events = parsed.get("key_events") if isinstance(parsed.get("key_events"), list) else cached.get("key_events")
                if isinstance(events, list):
                    key_events = "; ".join(str(e) for e in events if str(e).strip())
                source = "chapter_summary_cache"
                break
        if not brief:
            snippets = _summary_snippets(str(chapter_row.get("content") or ""), limit=2)
            brief = " ".join(snippets)[:300]
            source = "local_snippet_fallback"
        events_line = f"\nkey_events:{key_events}" if key_events else ""
        blocks.append(
            f"chapter_order:{row['chapter_order']}\ntitle:{row['title']}\nsummary:{brief}{events_line}\nsource:{source}"
        )
    chapters = "\n\n".join(blocks)
    return f"{_book_outline_schema()}\n\ninput_chapters:\n{chapters}"


def _book_stage_outline_schema() -> str:
    return (
        "Generate a whole-book stage outline as JSON only. Return this shape: "
        '{"status":"ok","task_type":"book_stage_outline","stages":['
        '{"stage_index":1,"title":"string","chapter_start":1,"chapter_end":5,'
        '"location":"string","characters":["string"],"event":"string","resolution":"string","outcome":"string"}],'
        '"evidence":[{"chapter_order":1,"source_quote":"string"}]}. '
        "Group the whole book into coarse plot stages (剧情阶段分块): each stage covers a continuous "
        "chapter range and answers location, characters, event, resolution and outcome. "
        "Use chapter_order numbers from the input. Do not invent chapters or characters. "
        "If a field is unknown, use an empty string, but still keep the exact JSON field names."
    )


def _book_stage_outline_flat_payload(conn, chapter_rows: list[dict[str, Any]], model: str) -> str:
    """Feed per-chapter summaries (cached chapter_summary preferred, local
    snippet fallback otherwise) so the model can group the book into stages."""
    blocks = []
    for row in chapter_rows:
        chapter_row = get_chapter(conn, int(row["id"]))
        chunks = get_chunks_for_chapter(conn, int(row["id"]))
        cs_payload = _chapter_summary_payload(chapter_row, chunks)
        brief = ""
        key_events = ""
        source = "local_snippet_fallback"
        for key in _task_cache_keys(conn, "chapter_summary", cs_payload, model):
            cached = get_cache(conn, key)
            if cached is None:
                continue
            parsed = cached.get("parsed_json") if isinstance(cached.get("parsed_json"), dict) else {}
            summary = str(parsed.get("short_summary") or cached.get("short_summary") or "").strip()
            if summary:
                brief = summary[:400]
                events = parsed.get("key_events") if isinstance(parsed.get("key_events"), list) else cached.get("key_events")
                if isinstance(events, list):
                    key_events = "; ".join(str(e) for e in events if str(e).strip())
                source = "chapter_summary_cache"
                break
        if not brief:
            snippets = _summary_snippets(str(chapter_row.get("content") or ""), limit=2)
            brief = " ".join(snippets)[:300]
            source = "local_snippet_fallback"
        events_line = f"\nkey_events:{key_events}" if key_events else ""
        blocks.append(
            f"chapter_order:{row['chapter_order']}\ntitle:{row['title']}\nsummary:{brief}{events_line}\nsource:{source}"
        )
    chapters = "\n\n".join(blocks)
    return f"{_book_stage_outline_schema()}\n\ninput_chapters:\n{chapters}"


def _book_stage_outline_arc_payload(arcs: list[dict[str, Any]]) -> str:
    lines = "\n\n".join(
        f"arc_index:{arc['arc_index']} chapter_range:{arc['chapter_start']}-{arc['chapter_end']}\n"
        f"title:{arc['title']}\nsummary:{arc['summary']}"
        for arc in arcs
    )
    return f"{_book_stage_outline_schema()}\n\narc_summaries:\n{lines}"


def _local_stage_outline(chapter_rows: list[dict[str, Any]], max_stages: int = 8) -> dict[str, Any]:
    total = len(chapter_rows)
    if total <= 0:
        return {
            "status": "local_fallback",
            "task_type": "book_stage_outline",
            "stages": [],
            "evidence": [],
            "suggestions": ["Set an API key for model-generated stage outlines."],
            "evidence_required": True,
        }
    n_stages = min(max_stages, max(1, (total + 4) // 5))
    stage_size = max(1, (total + n_stages - 1) // n_stages)
    stages: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for start in range(0, total, stage_size):
        group = chapter_rows[start:start + stage_size]
        stage_start = int(group[0]["chapter_order"])
        stage_end = int(group[-1]["chapter_order"])
        snippets = []
        for row in group:
            snippets.extend(_summary_snippets(str(row.get("content") or ""), limit=1))
        event = " ".join(snippets)[:240]
        stages.append(
            {
                "stage_index": len(stages) + 1,
                "title": f"阶段 {len(stages) + 1}（第 {stage_start}-{stage_end} 章）",
                "chapter_start": stage_start,
                "chapter_end": stage_end,
                "location": "",
                "characters": [],
                "event": event,
                "resolution": "",
                "outcome": "",
            }
        )
        evidence.append(
            {
                "chapter_id": int(group[0]["id"]),
                "chapter_order": stage_start,
                "chapter_title": str(group[0].get("title") or ""),
                "source_quote": str(group[0].get("content") or "")[:600],
            }
        )
    return {
        "status": "local_fallback",
        "task_type": "book_stage_outline",
        "stages": stages,
        "evidence": evidence,
        "suggestions": ["Set an API key for model-generated stage outlines."],
        "evidence_required": True,
    }


def _stage_outline_bounds_ok(output: dict[str, Any], max_order: int) -> bool:
    stages = output.get("stages")
    if not isinstance(stages, list) or not stages:
        return False
    for stage in stages:
        if not isinstance(stage, dict):
            return False
        start = _as_int(stage.get("chapter_start"))
        end = _as_int(stage.get("chapter_end"))
        if start < 1 or end < 1 or start > end:
            return False
        if end > max_order:
            return False
    return True


def _chapter_summary_payload(chapter_row: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
    chunk_text = "\n\n".join(
        f"chunk_id:{chunk['id']} order:{chunk['chunk_order']}\n{chunk['content']}" for chunk in chunks
    )
    return (
        "Summarize this chapter as JSON. Include short_summary, key_events, characters, and evidence.\n"
        f"chapter_id:{chapter_row['id']}\nchapter_title:{chapter_row['title']}\n\n{chunk_text}"
    )


def _local_chapter_summary(chapter_row: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    source_text = "\n".join(chunk["content"] for chunk in chunks).strip() or chapter_row["content"]
    snippets = _summary_snippets(source_text)
    short_summary = " ".join(snippets[:2]) if snippets else source_text[:240]
    source_quote = source_text[:900]
    return {
        "status": "local_fallback",
        "task_type": "chapter_summary",
        "short_summary": short_summary[:500],
        "key_events": snippets[:5],
        "characters": [],
        "evidence": [
            {
                "chapter_id": chapter_row["id"],
                "chapter_title": chapter_row["title"],
                "source_quote": source_quote,
            }
        ],
        "suggestions": ["Set an API key for model-generated summaries."],
        "evidence_required": True,
    }


def _local_book_outline(chapter_rows: list[dict[str, Any]], limit: int = 40) -> dict[str, Any]:
    chapters = []
    evidence = []
    for chapter_row in chapter_rows[:limit]:
        snippets = _summary_snippets(chapter_row["content"], limit=2)
        source_quote = chapter_row["content"][:600]
        chapters.append(
            {
                "chapter_id": chapter_row["id"],
                "chapter_order": chapter_row["chapter_order"],
                "chapter_title": chapter_row["title"],
                "brief": " ".join(snippets)[:360],
            }
        )
        evidence.append(
            {
                "chapter_id": chapter_row["id"],
                "chapter_order": chapter_row["chapter_order"],
                "chapter_title": chapter_row["title"],
                "source_quote": source_quote,
            }
        )
    return {
        "status": "local_fallback",
        "task_type": "book_outline",
        "outline": {
            "title": "Local chapter-order outline",
            "chapters": chapters,
        },
        "evidence": evidence,
        "suggestions": ["Set an API key for model-generated whole-book outlines."],
        "evidence_required": True,
    }


def _enrich_outline_briefs(output: dict[str, Any], chapter_rows: list[dict[str, Any]]) -> dict[str, Any]:
    outline = output.get("outline")
    if not isinstance(outline, dict):
        return output
    chapters = outline.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        return output
    if any(isinstance(item, dict) and str(item.get("brief") or "").strip() for item in chapters):
        return output
    by_order = {int(row.get("chapter_order") or 0): row for row in chapter_rows}
    enriched_chapters: list[dict[str, Any]] = []
    evidence = output.get("evidence") if isinstance(output.get("evidence"), list) else []
    for item in chapters:
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        order = _as_int(enriched.get("chapter_order") or enriched.get("order"))
        row = by_order.get(order)
        if row is not None:
            snippets = _summary_snippets(str(row.get("content") or ""), limit=2)
            enriched["brief"] = " ".join(snippets)[:360]
            if not enriched.get("chapter_title"):
                enriched["chapter_title"] = row.get("title") or enriched.get("title") or ""
            evidence.append(
                {
                    "chapter_id": row.get("id"),
                    "chapter_order": row.get("chapter_order"),
                    "chapter_title": row.get("title"),
                    "source_quote": str(row.get("content") or "")[:600],
                }
            )
        enriched_chapters.append(enriched)
    enriched_outline = dict(outline) | {"chapters": enriched_chapters}
    suggestions = output.get("suggestions") if isinstance(output.get("suggestions"), list) else []
    suggestions = suggestions + ["Remote outline returned chapter titles without summaries; chapter briefs were filled from local source snippets."]
    return output | {"outline": enriched_outline, "evidence": evidence, "suggestions": suggestions}