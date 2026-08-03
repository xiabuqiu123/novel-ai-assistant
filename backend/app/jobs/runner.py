"""Task-domain module: runner (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import asyncio

from ..cache import cache_key
from ..cache import input_hash
from ..database import get_analysis_job
from ..database import get_cache
from ..database import put_cache
from ..database import get_chapter
from ..database import get_chunks_for_chapter
from ..database import get_novel
from ..database import list_chapters
from ..database import list_extracted_facts
from ..database import update_analysis_job
from .cache import _cached_model_task
from .cache import _cache_metadata
from .cache import _cached_source
from .cache import _call_extraction_batch_model
from .cache import _cacheable_output
from .cache import _invalid_output_reason
from .cache import _task_cache_keys
from .cache import _unique_list
from .cache import _with_cache_metadata
from ..provenance import with_model_provenance
from .characters import CHARACTER_EXTRACTION_BATCH_SIZE
from .characters import _character_extraction_batch_payload
from .characters import _character_extraction_combined_payload
from .characters import _known_character_names
from .characters import _run_character_extraction_job
from .common import _PARTIAL_HINT
from .common import _normalize_model_output
from .conflicts import _conflict_judgment_payload
from .conflicts import _local_conflict_detection
from .conflicts import _run_conflict_detection_job
from .events import EVENT_EXTRACTION_BATCH_SIZE
from .events import _event_extraction_batch_payload
from .events import _run_event_extraction_job
from .orchestration import _analysis_job_request
from .orchestration import _extraction_concurrency
from .orchestration import _effective_model
from .orchestration import _extraction_batch_size
from .orchestration import _run_summary_cache_keys_for_novel
from .orchestration import _write_run_summary
from .outlines import _arc_summary_payload
from .outlines import _book_outline_payload
from .outlines import _book_stage_outline_flat_payload
from .outlines import _chapter_brief_for_arc
from .outlines import _chapter_summary_payload
from .outlines import _enrich_outline_briefs
from .outlines import _local_book_outline
from .outlines import _local_chapter_summary
from .outlines import _run_book_stage_outline_job
from .outlines import _run_layered_book_outline
from .qa import _local_qa_answer
from .qa import _qa_payload
from .qa import _retrieve_evidence
from .qa import _retrieve_qa_evidence
from .relationships import RELATIONSHIP_EXTRACTION_BATCH_SIZE
from .relationships import _relationship_batch_payload
from .relationships import _relationship_combined_payload
from .relationships import _run_relationship_extraction_job
from .settings_extraction import SETTING_EXTRACTION_BATCH_SIZE
from .settings_extraction import _run_setting_extraction_job
from .settings_extraction import _setting_extraction_batch_payload
from typing import Any
from .. import database
from .. import secrets
from . import outlines


async def _run_analysis_job(conn, job: dict[str, Any]) -> dict[str, Any]:
    # 取消守卫: 已取消的 job 不得再执行——background 任务可能晚于取消请求启动,
    # 照常执行会覆盖 cancelled 状态并产生不必要的模型调用。
    if str(job.get("status") or "") == "cancelled":
        return {
            "status": "cancelled",
            "task_type": str(job.get("task_type") or ""),
            "job_id": int(job["id"]),
            "skipped": True,
        }
    request = _analysis_job_request(job)
    model = str(request.get("effective_model") or "").strip() or _effective_model(conn, str(request.get("model") or "") or None)
    force_refresh = bool(request.get("force_refresh") or int(job.get("retry_count") or 0) > 0)
    task_type = str(job["task_type"])

    if task_type == "chapter_summary":
        chapter_id = job.get("chapter_id")
        if chapter_id is None:
            raise ValueError("chapter_summary job is missing chapter_id")
        chapter_row = get_chapter(conn, int(chapter_id))
        chunks = get_chunks_for_chapter(conn, int(chapter_id))
        return await _cached_model_task(
            conn,
            "chapter_summary",
            _chapter_summary_payload(chapter_row, chunks),
            model,
            force_refresh,
            job_id=int(job["id"]),
            fallback_output=_local_chapter_summary(chapter_row, chunks),
        )

    if task_type == "book_outline":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("book_outline job is missing novel_id")
        chapter_rows = list_chapters(conn, int(novel_id))
        if not chapter_rows:
            raise ValueError("novel not found or has no chapters")
        if len(chapter_rows) > outlines.BOOK_OUTLINE_ARC_SIZE:
            return await _run_layered_book_outline(conn, chapter_rows, model, force_refresh, int(job["id"]))
        payload = _book_outline_payload(conn, chapter_rows, model)
        full_chapters = [get_chapter(conn, int(row["id"])) for row in chapter_rows[:40]]
        result = await _cached_model_task(
            conn,
            "book_outline",
            payload,
            model,
            force_refresh,
            job_id=int(job["id"]),
            fallback_output=_local_book_outline(full_chapters),
        )
        return _enrich_outline_briefs(result, full_chapters)

    if task_type == "book_stage_outline":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("book_stage_outline job is missing novel_id")
        return await _run_book_stage_outline_job(conn, int(novel_id), model, force_refresh, int(job["id"]))

    if task_type == "whole_book_analysis":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("whole_book_analysis job is missing novel_id")
        # Retry of this orchestration job resumes from per-chapter caches: each
        # chapter summary is an independent cached task and failed chapters are
        # never cached, so only missing/failed chapters trigger new model calls.
        request_force_refresh = bool(request.get("force_refresh"))
        return await _run_whole_book_analysis_job(conn, int(novel_id), model, request_force_refresh, int(job["id"]))

    if task_type == "character_extraction":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("character_extraction job is missing novel_id")
        return await _run_character_extraction_job(conn, int(novel_id), model, force_refresh, int(job["id"]))

    if task_type == "relationship_extraction":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("relationship_extraction job is missing novel_id")
        return await _run_relationship_extraction_job(conn, int(novel_id), model, force_refresh, int(job["id"]))
    if task_type == "evidence_qa":
        novel_id = job.get("novel_id")
        question = str(request.get("question") or "").strip()
        if novel_id is None:
            raise ValueError("evidence_qa job is missing novel_id")
        if not question:
            raise ValueError("evidence_qa job is missing question")
        rows = list_chapters(conn, int(novel_id))
        if not rows:
            raise ValueError("novel not found or has no chapters")
        evidence = _retrieve_qa_evidence(conn, int(novel_id), question)
        payload = _qa_payload(question, evidence)
        return await _cached_model_task(
            conn,
            "evidence_qa",
            payload,
            model,
            force_refresh,
            job_id=int(job["id"]),
            fallback_output=_local_qa_answer(question, evidence),
        )

    if task_type == "setting_extraction":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("setting_extraction job is missing novel_id")
        return await _run_setting_extraction_job(conn, int(novel_id), model, force_refresh, int(job["id"]))

    if task_type == "event_extraction":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("event_extraction job is missing novel_id")
        return await _run_event_extraction_job(conn, int(novel_id), model, force_refresh, int(job["id"]))

    if task_type == "conflict_detection":
        novel_id = job.get("novel_id")
        if novel_id is None:
            raise ValueError("conflict_detection job is missing novel_id")
        return await _run_conflict_detection_job(conn, int(novel_id), model, force_refresh, int(job["id"]))

    raise ValueError(f"unsupported analysis job task_type: {task_type}")


async def _run_whole_book_analysis_job(
    conn,
    novel_id: int,
    model: str,
    force_refresh: bool,
    job_id: int,
) -> dict[str, Any]:
    """F4-style concurrent batch chapter-summary orchestration for one-click
    whole-book analysis.

    Three phases per wave: (1) serial payload preparation and cache probes,
    (2) concurrent model calls only (no sqlite access), (3) serial validation,
    cache writes, stats and progress. Every chapter summary is cached
    independently with a deterministic key, so a rerun after cancel or failure
    resumes from the first uncached chapter without duplicate calls.
    Cancellation is observed between waves (50 chapters per wave).
    """
    rows = list_chapters(conn, novel_id)
    if not rows:
        raise ValueError("novel not found or has no chapters")

    total = len(rows)
    update_analysis_job(conn, job_id, status="running", progress=1)
    processed = 0
    cache_hits = 0
    attempted = False
    succeeded = 0
    fallback_count = 0
    failed: list[dict[str, Any]] = []
    model_errors: list[str] = []
    fallback_chapters: list[dict[str, Any]] = []

    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    base_url = database.get_setting(conn, "base_url")
    concurrency = _extraction_concurrency(conn, "chapter_summary_concurrency", default=4, maximum=8)
    wave_size = 50

    def _summary_spec(row: dict[str, Any], index: int) -> dict[str, Any]:
        chapter_row = get_chapter(conn, int(row["id"]))
        chunks = get_chunks_for_chapter(conn, int(row["id"]))
        payload = _chapter_summary_payload(chapter_row, chunks)
        hash_value = input_hash("chapter_summary", payload)
        key = cache_key(model=model, task_type="chapter_summary", input_hash_value=hash_value)
        spec: dict[str, Any] = {
            "index": index,
            "row": row,
            "payload": payload,
            "hash": hash_value,
            "key": key,
            "fallback": _local_chapter_summary(chapter_row, chunks),
            "cached": None,
            "output": None,
        }
        if not force_refresh:
            cached = get_cache(conn, key)
            if cached is not None:
                cached = _normalize_model_output(cached, "chapter_summary")
                if _invalid_output_reason(cached, "chapter_summary") is None:
                    # 命中缓存时重包 provenance（对齐 _extraction_cache_probe）：
                    # 统计基于本轮语义，避免沿用首次运行的陈旧 provenance。
                    cache_meta = _cache_metadata(cached)
                    spec["cached"] = with_model_provenance(
                        cached,
                        task_type="chapter_summary",
                        model_used=model,
                        cache_hit=True,
                        input_hash_value=hash_value,
                        cache_key_value=key,
                        job_id=None,
                        source=_cached_source(cache_meta, cached),
                        model_error=cache_meta.get("model_error") or cached.get("model_error"),
                        provider_call_attempted=False,
                        provider_call_succeeded=False,
                    )
                else:
                    with conn:
                        conn.execute("DELETE FROM model_cache WHERE cache_key = ?", (key,))
        return spec

    def _finalize(cancelled: bool) -> dict[str, Any]:
        return _whole_book_analysis_result(
            job_id=job_id,
            model=model,
            total=total,
            processed=processed,
            failed=failed,
            cache_hits=cache_hits,
            attempted=attempted,
            succeeded=succeeded,
            fallback_count=fallback_count,
            cancelled=cancelled,
        )

    for wave_start in range(0, total, wave_size):
        if str(get_analysis_job(conn, job_id)["status"]) == "cancelled":
            update_analysis_job(conn, job_id, progress=1 + 98 * processed // total)
            return _finalize(cancelled=True)

        prepared = [
            _summary_spec(row, wave_start + index)
            for index, row in enumerate(rows[wave_start : wave_start + wave_size])
        ]
        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_summary_call(spec: dict[str, Any]) -> None:
            if spec["cached"] is not None:
                return
            async with semaphore:
                spec["output"] = await _call_extraction_batch_model(
                    "chapter_summary",
                    spec["payload"],
                    model,
                    api_key,
                    base_url,
                    spec["fallback"],
                )

        # Phase 2: concurrent model calls only; no sqlite access.
        await asyncio.gather(*(_bounded_summary_call(spec) for spec in prepared))

        # Phase 3: serial validation, cache writes, stats and progress.
        for index, spec in enumerate(prepared):
            row = spec["row"]
            result = spec["cached"] if spec["cached"] is not None else spec["output"]
            provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
            chapter_error = str(provenance.get("model_error") or result.get("model_error") or "").strip()
            if chapter_error:
                model_errors.append(chapter_error)
            if provenance.get("cache_hit"):
                cache_hits += 1
            if provenance.get("provider_call_attempted"):
                attempted = True
                if provenance.get("provider_call_succeeded"):
                    succeeded += 1
            if result.get("source") == "local_fallback" or result.get("status") == "local_fallback":
                fallback_count += 1
                fallback_chapters.append(
                    {
                        "chapter_id": int(row["id"]),
                        "chapter_order": int(row.get("chapter_order") or wave_start + index + 1),
                        "title": str(row.get("title") or ""),
                        "error": chapter_error or "local_fallback",
                    }
                )
            invalid_reason = _invalid_output_reason(result, "chapter_summary")
            if invalid_reason is not None:
                failed.append(
                    {
                        "chapter_id": int(row["id"]),
                        "chapter_order": int(row.get("chapter_order") or wave_start + index + 1),
                        "title": str(row.get("title") or ""),
                        "error": invalid_reason,
                    }
                )
            elif spec["cached"] is None:
                # Cache semantics match _cached_model_task: successful model
                # output and un-attempted local fallback are cacheable; a
                # fallback produced by a failed model call is never cached.
                attempted_call = bool(provenance.get("provider_call_attempted"))
                call_succeeded = bool(provenance.get("provider_call_succeeded"))
                if not attempted_call or call_succeeded:
                    put_cache(
                        conn,
                        key=spec["key"],
                        model=model,
                        task_type="chapter_summary",
                        input_hash_value=spec["hash"],
                        output=_cacheable_output(result),
                    )
            processed += 1
            if str(get_analysis_job(conn, job_id)["status"]) != "cancelled":
                update_analysis_job(conn, job_id, status="running", progress=1 + 98 * processed // total)

    result = _finalize(cancelled=False)
    last_error = model_errors[-1] if model_errors else ""
    _write_run_summary(
        conn,
        model=model,
        task_type="whole_book_analysis",
        novel_id=novel_id,
        job_id=job_id,
        summary=_with_cache_metadata(
            result,
            source=result["source"],
            provider_call_attempted=result["provider_call_attempted"],
            provider_call_succeeded=result["provider_call_succeeded"],
            model_error=last_error or None,
        ),
        failed_batches=failed + fallback_chapters,
    )
    if failed:
        failed_orders = ", ".join(str(item["chapter_order"]) for item in failed[:10])
        update_analysis_job(
            conn,
            job_id,
            status="failed",
            progress=100,
            error=(
                f"{len(failed)}/{total} chapter summaries failed "
                f"(chapter_order: {failed_orders}); retry resumes from cached chapters"
            ),
        )
    else:
        update_analysis_job(
            conn,
            job_id,
            status="completed",
            progress=100,
            error=last_error or _PARTIAL_HINT if fallback_count else None,
        )
    return result


def _whole_book_analysis_result(
    *,
    job_id: int,
    model: str,
    total: int,
    processed: int,
    failed: list[dict[str, Any]],
    cache_hits: int,
    attempted: bool,
    succeeded: int,
    fallback_count: int,
    cancelled: bool,
) -> dict[str, Any]:
    if cancelled:
        status = "cancelled"
    elif failed:
        status = "failed"
    elif fallback_count == 0:
        # 无失败批次即视为成功: 数据全部来自模型产物（本轮调用或缓存命中）。
        status = "ok"
    elif not attempted:
        status = "local_fallback"
    else:
        status = "partial"
    if fallback_count == 0:
        source = "remote_model"
    elif not attempted:
        source = "local_fallback"
    else:
        source = "mixed"
    return {
        "status": status,
        "task_type": "whole_book_analysis",
        "model_used": model,
        "job_id": job_id,
        "chapters_total": total,
        "chapters_processed": processed,
        "chapters_failed": len(failed),
        "cache_hits": cache_hits,
        "failed_chapters": failed,
        "source": source,
        "provider_call_attempted": attempted,
        "provider_call_succeeded": attempted and succeeded > 0 and not failed,
    }


def _computed_cache_keys_for_novel(conn, novel_id: int, task_type: str | None = None) -> list[str]:
    get_novel(conn, novel_id)
    task_filter = {task_type} if task_type else {"chapter_summary", "book_outline", "book_stage_outline", "character_extraction", "relationship_extraction", "evidence_qa", "whole_book_analysis", "setting_extraction", "event_extraction", "conflict_detection"}
    keys: list[str] = []
    model = database.get_setting(conn, "model", "gpt-4.1-mini")
    chapter_rows = list_chapters(conn, novel_id)

    if "chapter_summary" in task_filter:
        for row in chapter_rows:
            chapter_row = get_chapter(conn, int(row["id"]))
            chunks = get_chunks_for_chapter(conn, int(row["id"]))
            keys.extend(_task_cache_keys(conn, "chapter_summary", _chapter_summary_payload(chapter_row, chunks), model))

    if "book_outline" in task_filter and chapter_rows:
        payload = _book_outline_payload(conn, chapter_rows, model)
        keys.extend(_task_cache_keys(conn, "book_outline", payload, model))
        if len(chapter_rows) > outlines.BOOK_OUTLINE_ARC_SIZE:
            arc_groups = [
                chapter_rows[start:start + outlines.BOOK_OUTLINE_ARC_SIZE]
                for start in range(0, len(chapter_rows), outlines.BOOK_OUTLINE_ARC_SIZE)
            ]
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
                keys.extend(_task_cache_keys(conn, "arc_summary", _arc_summary_payload(arc_index, briefs), model))

    if "book_stage_outline" in task_filter and chapter_rows:
        if len(chapter_rows) > outlines.BOOK_OUTLINE_ARC_SIZE:
            arc_groups = [
                chapter_rows[start:start + outlines.BOOK_OUTLINE_ARC_SIZE]
                for start in range(0, len(chapter_rows), outlines.BOOK_OUTLINE_ARC_SIZE)
            ]
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
                keys.extend(_task_cache_keys(conn, "arc_summary", _arc_summary_payload(arc_index, briefs), model))
        else:
            payload = _book_stage_outline_flat_payload(conn, chapter_rows, model)
            keys.extend(_task_cache_keys(conn, "book_stage_outline", payload, model))

    if "character_extraction" in task_filter and chapter_rows:
        char_names = _known_character_names(conn, novel_id, [get_chapter(conn, int(row["id"])) for row in chapter_rows], model)
        batch_size = _extraction_batch_size(conn, "character_extraction_batch_size", CHARACTER_EXTRACTION_BATCH_SIZE)
        batches = [
            chapter_rows[start:start + batch_size]
            for start in range(0, len(chapter_rows), batch_size)
        ]
        for batch_rows in batches:
            full_rows = [get_chapter(conn, int(row["id"])) for row in batch_rows]
            keys.extend(
                _task_cache_keys(
                    conn,
                    "character_extraction",
                    _character_extraction_batch_payload(batch_rows, full_rows, char_names),
                    model,
                )
            )
        combined_hash = input_hash("character_extraction_combined", _character_extraction_combined_payload(chapter_rows))
        keys.append(cache_key(model=model, task_type="character_extraction_combined", input_hash_value=combined_hash))

    if "relationship_extraction" in task_filter and chapter_rows:
        rel_names = _known_character_names(conn, novel_id, [get_chapter(conn, int(row["id"])) for row in chapter_rows])
        batch_size = _extraction_batch_size(conn, "relationship_extraction_batch_size", RELATIONSHIP_EXTRACTION_BATCH_SIZE)
        batches = [
            chapter_rows[start:start + batch_size]
            for start in range(0, len(chapter_rows), batch_size)
        ]
        for batch_rows in batches:
            full_rows = [get_chapter(conn, int(row["id"])) for row in batch_rows]
            keys.extend(
                _task_cache_keys(
                    conn,
                    "relationship_extraction",
                    _relationship_batch_payload(batch_rows, full_rows, rel_names),
                    model,
                )
            )
        combined_hash = input_hash("relationship_extraction_combined", _relationship_combined_payload(chapter_rows))
        keys.append(cache_key(model=model, task_type="relationship_extraction_combined", input_hash_value=combined_hash))

    if "setting_extraction" in task_filter and chapter_rows:
        batch_size = _extraction_batch_size(conn, "setting_extraction_batch_size", SETTING_EXTRACTION_BATCH_SIZE)
        batches = [
            chapter_rows[start:start + batch_size]
            for start in range(0, len(chapter_rows), batch_size)
        ]
        for batch_rows in batches:
            full_rows = [get_chapter(conn, int(row["id"])) for row in batch_rows]
            keys.extend(
                _task_cache_keys(conn, "setting_extraction", _setting_extraction_batch_payload(batch_rows, full_rows), model)
            )

    if "event_extraction" in task_filter and chapter_rows:
        batch_size = _extraction_batch_size(conn, "event_extraction_batch_size", EVENT_EXTRACTION_BATCH_SIZE)
        batches = [
            chapter_rows[start:start + batch_size]
            for start in range(0, len(chapter_rows), batch_size)
        ]
        for batch_rows in batches:
            full_rows = [get_chapter(conn, int(row["id"])) for row in batch_rows]
            keys.extend(
                _task_cache_keys(conn, "event_extraction", _event_extraction_batch_payload(batch_rows, full_rows), model)
            )

    if "conflict_detection" in task_filter and chapter_rows:
        facts: list[dict[str, Any]] = []
        for fact_type in ("character_profile", "world_rule", "setting_fact", "event", "character_relationship"):
            facts.extend(list_extracted_facts(conn, novel_id, fact_type=fact_type))
        local = _local_conflict_detection(conn, novel_id, facts)
        keys.extend(
            _task_cache_keys(conn, "conflict_detection", _conflict_judgment_payload(local.get("conflicts", [])), model)
        )

    for summary_task_type in ("whole_book_analysis", "setting_extraction", "event_extraction", "conflict_detection"):
        if summary_task_type in task_filter:
            keys.extend(_run_summary_cache_keys_for_novel(conn, novel_id, summary_task_type))

    # evidence_qa: QA 缓存键已由 analysis_jobs.result_cache_key 精确覆盖
    # （_cached_model_task 命中/兜底路径都写 result_cache_key），无需重算检索。

    return _unique_list(keys)