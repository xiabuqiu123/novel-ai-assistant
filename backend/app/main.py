from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTasks
from pydantic import BaseModel

from .cache import cache_key, input_hash
from .database import (
    clear_novel_cache,
    clear_novel_cache_with_keys,
    connect,
    create_analysis_job,
    delete_novel,
    delete_novel_with_cache_keys,
    fail_stale_running_jobs,
    get_analysis_job,
    get_cache,
    get_chapter,
    get_chunks_for_chapter,
    get_novel,
    get_setting,
    import_novel,
    list_analysis_jobs,
    list_chapters,
    list_extracted_facts,
    list_review_actions,
    list_novels,
    next_queued_analysis_job,
    put_cache,
    retry_analysis_job,
    set_setting,
    supersede_previous_run_facts,
    update_analysis_job,
    usage_stats,
    update_review_status,
    upsert_extracted_fact,
)
from .model_client import ModelHTTPError, call_openai_compatible, test_openai_compatible_connection
from .provenance import with_model_provenance
from .secrets import decrypt_secret, encrypt_secret
from .text_processing import detect_and_decode, sha256_text, split_chapters

from . import model_client
from . import secrets
from . import database
from .jobs import outlines
from .jobs.common import _as_int
from .jobs.common import _chapter_lookup_for_novel
from .jobs.common import _norm_evidence
from .jobs.common import _normalize_book_outline
from .jobs.common import _normalize_model_output
from .jobs.common import _normalize_outline_chapter
from .jobs.common import _normalize_stage
from .jobs.common import _optional_int
from .jobs.common import _resolve_character_chapter_id
from .jobs.common import _source_quote
from .jobs.common import _source_quote_at
from .jobs.common import _summary_snippets
from .jobs.common import _text_from_aliases
from .jobs.common import _PARTIAL_HINT
from .jobs.cache import _cache_metadata
from .jobs.cache import _cached_model_task
from .jobs.cache import _cached_source
from .jobs.cache import _call_extraction_batch_model
from .jobs.cache import _extraction_cache_probe
from .jobs.cache import _invalid_output_reason
from .jobs.cache import _model_error_text
from .jobs.cache import _task_cache_key
from .jobs.cache import _task_cache_key_from_hash
from .jobs.cache import _task_cache_keys
from .jobs.cache import _unique_list
from .jobs.cache import _with_cache_metadata
from .jobs.cache import _NON_CACHEABLE_STATUSES
from .jobs.cache import _REQUIRED_LIST_OUTPUT_FIELDS
from .jobs.orchestration import _analysis_job_request
from .jobs.orchestration import _base_source
from .jobs.orchestration import _dedupe_request_key
from .jobs.orchestration import _effective_model
from .jobs.orchestration import _extraction_batch_size
from .jobs.orchestration import _extraction_concurrency
from .jobs.orchestration import _find_active_chapter_summary_job
from .jobs.orchestration import _find_active_job
from .jobs.orchestration import _find_active_job_with_request
from .jobs.orchestration import _get_job_model
from .jobs.orchestration import _run_batched_fact_extraction_job
from .jobs.orchestration import _run_summary_cache_key
from .jobs.orchestration import _run_summary_cache_keys_for_novel
from .jobs.orchestration import _write_run_summary
from .jobs.characters import _character_duplicate_candidates
from .jobs.characters import _character_extraction_batch_payload
from .jobs.characters import _character_extraction_combined_payload
from .jobs.characters import _character_extraction_schema
from .jobs.characters import _character_merge_target
from .jobs.characters import _character_name_candidates
from .jobs.characters import _is_name_stopword
from .jobs.characters import _known_character_names
from .jobs.characters import _local_character_extraction
from .jobs.characters import _merge_affiliation_values
from .jobs.characters import _merge_character_attributes
from .jobs.characters import _merge_character_entry
from .jobs.characters import _persist_character_facts
from .jobs.characters import _register_character_aliases
from .jobs.characters import _run_character_extraction_job
from .jobs.characters import CHARACTER_EXTRACTION_BATCH_SIZE
from .jobs.characters import _CHARACTER_ATTRIBUTE_LABELS
from .jobs.characters import _CONFIDENCE_RANK
from .jobs.characters import _IGNORED_ATTRIBUTE_VALUES
from .jobs.relationships import _canonical_relationship_name
from .jobs.relationships import _local_relationship_extraction
from .jobs.relationships import _merge_evolution_lists
from .jobs.relationships import _merge_relationship_entry
from .jobs.relationships import _normalize_relationship_attitude
from .jobs.relationships import _normalize_relationship_evolution
from .jobs.relationships import _parse_relationship_content
from .jobs.relationships import _persist_relationship_facts
from .jobs.relationships import _relationship_alias_map
from .jobs.relationships import _relationship_batch_payload
from .jobs.relationships import _relationship_combined_payload
from .jobs.relationships import _relationship_extraction_schema
from .jobs.relationships import _relationship_pair_key
from .jobs.relationships import _run_relationship_extraction_job
from .jobs.relationships import _sort_relationship_evolution
from .jobs.relationships import RELATIONSHIP_EXTRACTION_BATCH_SIZE
from .jobs.relationships import _ATTITUDE_SYNONYMS
from .jobs.relationships import _RELATIONSHIP_ATTITUDES
from .jobs.outlines import _arc_summary_payload
from .jobs.outlines import _arc_summary_schema
from .jobs.outlines import _book_outline_payload
from .jobs.outlines import _book_outline_schema
from .jobs.outlines import _book_stage_outline_arc_payload
from .jobs.outlines import _book_stage_outline_flat_payload
from .jobs.outlines import _book_stage_outline_schema
from .jobs.outlines import _chapter_brief_for_arc
from .jobs.outlines import _chapter_summary_payload
from .jobs.outlines import _enrich_outline_briefs
from .jobs.outlines import _extract_arc_summary
from .jobs.outlines import _layered_book_outline_payload
from .jobs.outlines import _layered_book_outline_schema
from .jobs.outlines import _local_arc_summary
from .jobs.outlines import _local_book_outline
from .jobs.outlines import _local_chapter_summary
from .jobs.outlines import _local_layered_book_outline
from .jobs.outlines import _local_stage_outline
from .jobs.outlines import _run_book_stage_outline_job
from .jobs.outlines import _run_layered_book_outline
from .jobs.outlines import _run_layered_book_stage_outline
from .jobs.outlines import _run_stage_outline_model_phase
from .jobs.outlines import _stage_outline_bounds_ok
from .jobs.conflicts import _conflict_judgment_payload
from .jobs.conflicts import _conflict_judgment_schema
from .jobs.conflicts import _conflict_order_key
from .jobs.conflicts import _conflict_polarity
from .jobs.conflicts import _detect_character_attribute_conflicts
from .jobs.conflicts import _detect_item_ability_conflicts
from .jobs.conflicts import _detect_plot_logic_conflicts
from .jobs.conflicts import _detect_relationship_conflicts
from .jobs.conflicts import _detect_timeline_conflicts
from .jobs.conflicts import _detect_world_setting_conflicts
from .jobs.conflicts import _ev_chapter_order
from .jobs.conflicts import _evidence_or_quote
from .jobs.conflicts import _fact_chapter_order
from .jobs.conflicts import _fact_entities
from .jobs.conflicts import _fact_evidence
from .jobs.conflicts import _find_conflict_explanation
from .jobs.conflicts import _first_temporal_number
from .jobs.conflicts import _local_conflict_detection
from .jobs.conflicts import _new_conflict_candidate
from .jobs.conflicts import _persist_conflict_facts
from .jobs.conflicts import _relationship_fact_type
from .jobs.conflicts import _run_conflict_detection_job
from .jobs.conflicts import _search_chapter_text_for_explanation
from .jobs.conflicts import _CONFLICT_CANDIDATE_TYPES
from .jobs.conflicts import _CONFLICT_SEVERITIES
from .jobs.conflicts import _IGNORE_ATTRIBUTE_VALUE
from .jobs.conflicts import _NEG_POLARITY_MARKERS
from .jobs.conflicts import _POS_POLARITY_MARKERS
from .jobs.conflicts import _RELATIONSHIP_CONTENT_PATTERN
from .jobs.conflicts import _REL_OPPOSITES
from .jobs.qa import _cached_chapter_summaries
from .jobs.qa import _candidate_chapter_scores
from .jobs.qa import _evidence_item
from .jobs.qa import _expanded_query_terms
from .jobs.qa import _facts_evidence_for_question
from .jobs.qa import _fuzzy_quote_candidates
from .jobs.qa import _local_qa_answer
from .jobs.qa import _merge_qa_evidence
from .jobs.qa import _normalized_fuzzy_text
from .jobs.qa import _qa_payload
from .jobs.qa import _question_terms
from .jobs.qa import _retrieve_evidence
from .jobs.qa import _retrieve_qa_evidence
from .jobs.qa import _strip_question_intent
from .jobs.qa import _summary_text
from .jobs.qa import QA_RETRIEVAL_VERSION
from .jobs.settings_extraction import _local_setting_extraction
from .jobs.settings_extraction import _persist_setting_facts
from .jobs.settings_extraction import _run_setting_extraction_job
from .jobs.settings_extraction import _setting_extraction_batch_payload
from .jobs.settings_extraction import _setting_extraction_schema
from .jobs.settings_extraction import SETTING_EXTRACTION_BATCH_SIZE
from .jobs.settings_extraction import _SETTING_CATEGORIES
from .jobs.events import _event_extraction_batch_payload
from .jobs.events import _event_extraction_schema
from .jobs.events import _local_event_extraction
from .jobs.events import _persist_event_facts
from .jobs.events import _run_event_extraction_job
from .jobs.events import backfill_event_chapter_order
from .jobs.events import EVENT_EXTRACTION_BATCH_SIZE
from .jobs.reports import _full_report_markdown
from .jobs.reports import _latest_cached_book_outline
from .jobs.reports import _markdown_heading_text
from .jobs.reports import _markdown_inline_text
from .jobs.reports import _novel_markdown_export
from .jobs.reports import _report_chapter_title
from .jobs.reports import _report_fact_evidence_lines
from .jobs.reports import _report_section_header
from .jobs.runner import _computed_cache_keys_for_novel
from .jobs.runner import _run_analysis_job
from .jobs.runner import _run_whole_book_analysis_job
from .jobs.runner import _whole_book_analysis_result

logger = logging.getLogger(__name__)


if getattr(sys, "frozen", False):
    # PyInstaller onefile: __file__ points into the temporary extraction dir,
    # which is wiped on exit. Keep the DB portable next to the exe instead.
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parents[1]


DB_PATH = BASE_DIR / "data" / "novel_mvp.sqlite3"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    with db() as conn:
        failed = fail_stale_running_jobs(conn)
        if failed:
            print(f"startup: marked {failed} stale running analysis job(s) as failed")
        if database.get_setting(conn, "event_chapter_order_backfill_v1") != "1":
            repaired = backfill_event_chapter_order(conn)
            if repaired:
                print(f"startup: repaired {repaired} event fact(s) with missing chapter_order")
            set_setting(conn, "event_chapter_order_backfill_v1", "1")
    yield


app = FastAPI(title="Long-Form Novel AI Analysis Assistant MVP", lifespan=_lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ModelSettings(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-4.1-mini"


class ModelTaskRequest(BaseModel):
    model: str | None = None
    force_refresh: bool = False


class ModelConnectionTestRequest(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class QuestionRequest(ModelTaskRequest):
    question: str


class ReviewUpdateRequest(BaseModel):
    status: str
    note: str = ""


def db():
    return connect(DB_PATH)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "scope": "mvp"}


@app.get("/novels")
def novels() -> list[dict[str, Any]]:
    with db() as conn:
        return list_novels(conn)


@app.delete("/novels/{novel_id}")
def remove_novel(novel_id: int) -> dict[str, Any]:
    with db() as conn:
        try:
            extra_keys = _computed_cache_keys_for_novel(conn, novel_id)
            return delete_novel_with_cache_keys(conn, novel_id, extra_keys)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/novels/{novel_id}/cache")
def clear_cache(novel_id: int, task_type: str | None = None) -> dict[str, Any]:
    with db() as conn:
        try:
            extra_keys = _computed_cache_keys_for_novel(conn, novel_id, task_type)
            return clear_novel_cache_with_keys(conn, novel_id, task_type, extra_keys)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/novels/import-txt")
async def import_txt(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    chunk_size: int = Form(default=6000),
) -> dict[str, Any]:
    if chunk_size < 1:
        raise HTTPException(status_code=400, detail="chunk_size must be >= 1")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="TXT file is empty")
    text, encoding = detect_and_decode(raw)
    chapters = split_chapters(text)
    if not chapters:
        raise HTTPException(status_code=400, detail="No readable text found")

    novel_title = title or Path(file.filename or "novel.txt").stem or "Untitled novel"
    with db() as conn:
        result = import_novel(
            conn,
            title=novel_title,
            source_filename=file.filename or "upload.txt",
            encoding=encoding,
            text_hash=sha256_text(text),
            chapters=chapters,
            chunk_size=chunk_size,
        )
    return result | {"encoding": encoding}


@app.get("/novels/{novel_id}/chapters")
def chapters(novel_id: int) -> list[dict[str, Any]]:
    with db() as conn:
        return list_chapters(conn, novel_id)


@app.get("/novels/{novel_id}/facts")
def facts(
    novel_id: int,
    fact_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    with db() as conn:
        try:
            return list_extracted_facts(conn, novel_id, fact_type=fact_type, status=status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/review/{record_type}/{record_id}")
def review_record(record_type: str, record_id: int, request: ReviewUpdateRequest) -> dict[str, Any]:
    if request.status not in {"pending_review", "confirmed", "dismissed", "explained", "watching"}:
        raise HTTPException(status_code=400, detail="unsupported review status")
    with db() as conn:
        try:
            updated = update_review_status(
                conn,
                record_type=record_type,
                record_id=record_id,
                status=request.status,
                note=request.note,
            )
            return updated | {"review_actions": list_review_actions(conn, record_type, record_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/novels/{novel_id}/export/markdown")
def export_markdown(novel_id: int) -> dict[str, Any]:
    with db() as conn:
        try:
            novel = get_novel(conn, novel_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        chapter_rows = list_chapters(conn, novel_id)
        if not chapter_rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        chapters_with_content = [get_chapter(conn, int(row["id"])) for row in chapter_rows]

    markdown = _novel_markdown_export(novel, chapters_with_content)
    return {
        "filename": f"novel-{novel_id}-export.md",
        "content_type": "text/markdown",
        "markdown": markdown,
    }


@app.get("/novels/{novel_id}/export/report")
def export_full_report(novel_id: int, include_chapters: bool = True) -> dict[str, Any]:
    """Full analysis report: outline + characters + relationships + settings + timeline + conflicts.

    PRD 9.h - export report template expansion. Sections that have no data are omitted so an
    empty novel still exports cleanly. All AI-derived sections cite chapter-level evidence.
    """
    with db() as conn:
        try:
            novel = get_novel(conn, novel_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        chapter_rows = list_chapters(conn, novel_id)
        facts = list_extracted_facts(conn, novel_id)
        outline = _latest_cached_book_outline(conn, novel_id)
        # Only fetch full chapter bodies when they are actually needed for the report.
        chapters_with_content = [get_chapter(conn, int(row["id"])) for row in chapter_rows] if include_chapters else []

    markdown = _full_report_markdown(
        novel,
        chapter_rows,
        facts,
        outline,
        chapters_with_content=chapters_with_content,
    )
    return {
        "filename": f"novel-{novel_id}-report.md",
        "content_type": "text/markdown",
        "markdown": markdown,
    }


@app.get("/chapters/{chapter_id}")
def chapter(chapter_id: int) -> dict[str, Any]:
    with db() as conn:
        try:
            return get_chapter(conn, chapter_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/analysis-jobs")
def analysis_jobs(novel_id: int | None = None) -> list[dict[str, Any]]:
    with db() as conn:
        return list_analysis_jobs(conn, novel_id)


@app.get("/usage-stats")
def usage_stats_route() -> dict[str, Any]:
    """Cumulative model-usage statistics for the settings page (PRD: 累计调用统计)."""
    with db() as conn:
        return usage_stats(conn)


@app.get("/analysis-jobs/{job_id}/result")
def analysis_job_result(job_id: int) -> dict[str, Any]:
    """Return the cached result with provenance for a completed job, or current status."""
    with db() as conn:
        try:
            job = get_analysis_job(conn, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        job_status = str(job["status"])
        cache_key_value = str(job.get("result_cache_key") or "")

        if cache_key_value:
            cached = get_cache(conn, cache_key_value)
            if cached is not None:
                task_type = str(job["task_type"])
                if task_type == "book_outline" and job.get("novel_id") is not None:
                    rows = list_chapters(conn, int(job["novel_id"]))
                    full_chapters = [get_chapter(conn, int(row["id"])) for row in rows[:40]]
                    cached = _enrich_outline_briefs(cached, full_chapters)
                if task_type in ("evidence_qa", "chapter_summary"):
                    cached = _normalize_model_output(cached, task_type)
                request = {}
                try:
                    import json as _json
                    request = _json.loads(str(job.get("request_json") or "{}"))
                except Exception:
                    pass
                effective_model = str(request.get("effective_model") or "")
                metadata = _cache_metadata(cached)
                source = _cached_source(metadata, cached)
                model_error = str(job.get("error") or metadata.get("model_error") or cached.get("model_error") or "")
                cached_provenance = cached.get("provenance") if isinstance(cached.get("provenance"), dict) else {}
                input_hash_value = str(cached_provenance.get("input_hash") or "")
                local_fallback = source in {"local_fallback", "cached_local_fallback"} or cached.get("status") in {"local_fallback", "needs_api_key"}
                provider_attempted = bool(metadata.get("provider_call_attempted") or cached_provenance.get("provider_call_attempted"))
                provider_succeeded = bool(metadata.get("provider_call_succeeded") or cached_provenance.get("provider_call_succeeded"))

                return {
                    "status": job_status,
                    "job_id": job_id,
                    "result": cached,
                    "provenance": {
                        "task_type": task_type,
                        "model_used": effective_model,
                        "cache_hit": True,
                        "source": source,
                        "local_fallback": local_fallback,
                        "input_hash": input_hash_value,
                        "cache_key": cache_key_value,
                        "job_id": job_id,
                        "model_error": model_error,
                        "provider_call_attempted": provider_attempted,
                        "provider_call_succeeded": provider_succeeded,
                    },
                }

        return {
            "status": job_status,
            "job_id": job_id,
            "result": None,
            "progress": int(job.get("progress") or 0),
            "error": str(job.get("error") or ""),
            "effective_model": str(_get_job_model(job)),
        }


@app.get("/analysis-jobs/{job_id}")
def analysis_job(job_id: int) -> dict[str, Any]:
    with db() as conn:
        try:
            return get_analysis_job(conn, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/analysis-jobs/{job_id}/retry")
def retry_job(job_id: int) -> dict[str, Any]:
    with db() as conn:
        try:
            return retry_analysis_job(conn, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/analysis-jobs/{job_id}/run")
async def run_analysis_job(job_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Start a queued job in the background and return immediately.

    同步 await 整个 job 会让请求挂到任务结束（章节/批次任务可能数十分钟），
    前端 20s 超时后误以为未启动。改为后台执行, 前端轮询 job 状态。
    """
    with db() as conn:
        try:
            job = get_analysis_job(conn, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = str(job["status"])
        if status == "cancelled":
            return {"job_id": job_id, "status": "cancelled", "skipped": True}
        if status == "running":
            return {"job_id": job_id, "status": "running", "skipped": True}
        if status in ("completed", "failed"):
            return {"job_id": job_id, "status": status, "skipped": True}
        background_tasks.add_task(_run_job_in_background, job_id)
        return {"job_id": job_id, "status": "queued", "skipped": False}


@app.post("/analysis-jobs/run-next")
async def run_next_analysis_job(background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Start the next queued job in the background and return immediately."""
    with db() as conn:
        job = next_queued_analysis_job(conn)
        if job is None:
            return {"status": "idle", "job": None}
        job_id = int(job["id"])
        background_tasks.add_task(_run_job_in_background, job_id)
        return {"status": "started", "job_id": job_id, "job": {"id": job_id, "status": "queued"}}


@app.post("/analysis-jobs/{job_id}/cancel")
def cancel_analysis_job(job_id: int) -> dict[str, Any]:
    """Cancel a queued or running analysis job.

    Running orchestration jobs (e.g. whole_book_analysis) observe the cancelled
    status between units of work and stop; completed work stays cached so a
    later run resumes without duplicate model calls.
    """
    with db() as conn:
        try:
            job = get_analysis_job(conn, job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        status = str(job["status"])
        if status not in ("queued", "running"):
            raise HTTPException(
                status_code=400,
                detail=f"job status '{status}' cannot be cancelled",
            )
        return update_analysis_job(conn, job_id, status="cancelled")


@app.post("/novels/{novel_id}/analyze-all/start")
async def start_whole_book_analysis(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """One-click whole-book analysis: batch chapter summaries with progress, cancel and resume."""
    with db() as conn:
        chapter_rows = list_chapters(conn, novel_id)
        if not chapter_rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)

        # Dedup: reuse existing queued/running job
        existing = _find_active_job(conn, novel_id, "whole_book_analysis")
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }

        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="whole_book_analysis", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])

    # Run in background after releasing the DB connection
    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


@app.post("/settings/model")
def save_model_settings(settings: ModelSettings) -> dict[str, str]:
    with db() as conn:
        set_setting(conn, "api_key", encrypt_secret(settings.api_key))
        set_setting(conn, "base_url", settings.base_url)
        set_setting(conn, "model", settings.model)
    return {"status": "saved"}


@app.get("/settings/model")
def read_model_settings() -> dict[str, str]:
    with db() as conn:
        api_key = secrets.decrypt_secret(database.get_setting(conn, "api_key"))
        return {
            "api_key_set": "yes" if api_key else "no",
            "base_url": database.get_setting(conn, "base_url"),
            "model": database.get_setting(conn, "model", "gpt-4.1-mini"),
        }


@app.post("/settings/model/test")
async def test_model_settings(request: ModelConnectionTestRequest) -> dict[str, Any]:
    with db() as conn:
        api_key = request.api_key if request.api_key is not None else secrets.decrypt_secret(database.get_setting(conn, "api_key"))
        base_url = request.base_url if request.base_url is not None else database.get_setting(conn, "base_url")
        model = request.model if request.model is not None else database.get_setting(conn, "model", "gpt-4.1-mini")
    try:
        return await model_client.test_openai_compatible_connection(api_key=api_key, base_url=base_url, model=model)
    except ModelHTTPError as exc:
        return {
            "ok": False,
            "status": "provider_http_error",
            "message": exc.message,
            "http_status": exc.status_code,
            "model": model,
            "base_url": base_url,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "network_error",
            "message": str(exc),
            "model": model,
            "base_url": base_url,
        }


@app.post("/chapters/{chapter_id}/summary")
async def summarize_chapter(chapter_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        try:
            chapter_row = get_chapter(conn, chapter_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        chunks = get_chunks_for_chapter(conn, chapter_id)
        payload = _chapter_summary_payload(chapter_row, chunks)
        fallback_output = _local_chapter_summary(chapter_row, chunks)
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(
            conn,
            task_type="chapter_summary",
            novel_id=chapter_row["novel_id"],
            chapter_id=chapter_id,
            request=request_data,
        )
        return await _cached_model_task(
            conn,
            "chapter_summary",
            payload,
            effective_model,
            request.force_refresh,
            job_id=job["id"],
            fallback_output=fallback_output,
        )


@app.post("/chapters/{chapter_id}/summary/start")
async def start_chapter_summary(chapter_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Non-blocking start: creates a chapter_summary analysis job and runs it in background."""
    with db() as conn:
        try:
            chapter_row = get_chapter(conn, chapter_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        effective_model = _effective_model(conn, request.model)

        # Dedup: reuse existing queued/running job for the same chapter
        existing = _find_active_chapter_summary_job(conn, chapter_id)
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }

        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(
            conn,
            task_type="chapter_summary",
            novel_id=chapter_row["novel_id"],
            chapter_id=chapter_id,
            request=request_data,
        )
        job_id = int(job["id"])

    # Run in background after releasing the DB connection
    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


@app.post("/novels/{novel_id}/outline")
async def outline(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        chapter_rows = list_chapters(conn, novel_id)
        if not chapter_rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        if len(chapter_rows) > outlines.BOOK_OUTLINE_ARC_SIZE:
            effective_model = _effective_model(conn, request.model)
            request_data = request.model_dump() | {"effective_model": effective_model}
            job = create_analysis_job(conn, task_type="book_outline", novel_id=novel_id, request=request_data)
            return await _run_layered_book_outline(conn, chapter_rows, effective_model, request.force_refresh, int(job["id"]))
        effective_model = _effective_model(conn, request.model)
        payload = _book_outline_payload(conn, chapter_rows, effective_model)
        full_chapters = [get_chapter(conn, int(row["id"])) for row in chapter_rows[:40]]
        fallback_output = _local_book_outline(full_chapters)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="book_outline", novel_id=novel_id, request=request_data)
        result = await _cached_model_task(
            conn,
            "book_outline",
            payload,
            effective_model,
            request.force_refresh,
            job_id=job["id"],
            fallback_output=fallback_output,
        )
        return _enrich_outline_briefs(result, full_chapters)


@app.post("/novels/{novel_id}/outline/start")
async def start_outline(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Non-blocking start: creates an analysis job and runs it in background."""
    with db() as conn:
        chapter_rows = list_chapters(conn, novel_id)
        if not chapter_rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)

        # Dedup: reuse existing queued/running job
        existing = _find_active_job(conn, novel_id, "book_outline")
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }

        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="book_outline", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])

    # Run in background after releasing the DB connection
    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


@app.post("/novels/{novel_id}/stage-outline")
async def stage_outline(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    """Generate a whole-book stage outline (剧情阶段分块); large books go
    through the layered arc_summary pipeline."""
    with db() as conn:
        chapter_rows = list_chapters(conn, novel_id)
        if not chapter_rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="book_stage_outline", novel_id=novel_id, request=request_data)
        return await _run_book_stage_outline_job(conn, novel_id, effective_model, request.force_refresh, int(job["id"]))


@app.post("/novels/{novel_id}/stage-outline/start")
async def start_stage_outline(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Non-blocking start: creates a book_stage_outline job and runs it in background."""
    with db() as conn:
        chapter_rows = list_chapters(conn, novel_id)
        if not chapter_rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)

        # Dedup: reuse an existing queued/running stage-outline job
        existing = _find_active_job(conn, novel_id, "book_stage_outline")
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }

        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="book_stage_outline", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])

    # Run in background after releasing the DB connection
    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


@app.post("/novels/{novel_id}/characters/start")
async def start_characters(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Non-blocking start: creates an analysis job and runs it in background."""
    with db() as conn:
        rows = list_chapters(conn, novel_id)
        if not rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)

        # Dedup: reuse existing queued/running job
        existing = _find_active_job(conn, novel_id, "character_extraction")
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }

        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="character_extraction", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])

    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


@app.post("/novels/{novel_id}/characters")
async def characters(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        rows = list_chapters(conn, novel_id)
        if not rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="character_extraction", novel_id=novel_id, request=request_data)
        return await _run_character_extraction_job(conn, novel_id, effective_model, request.force_refresh, int(job["id"]))


@app.post("/novels/{novel_id}/relationships")
async def relationships(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        rows = list_chapters(conn, novel_id)
        if not rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="relationship_extraction", novel_id=novel_id, request=request_data)
        return await _run_relationship_extraction_job(conn, novel_id, effective_model, request.force_refresh, int(job["id"]))


@app.post("/novels/{novel_id}/relationships/start")
async def start_relationships(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Non-blocking start: creates a relationship extraction job and runs it in background."""
    with db() as conn:
        rows = list_chapters(conn, novel_id)
        if not rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)

        existing = _find_active_job(conn, novel_id, "relationship_extraction")
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }

        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="relationship_extraction", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])

    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


@app.get("/novels/{novel_id}/relationships/graph")
def relationship_graph(novel_id: int) -> dict[str, Any]:
    with db() as conn:
        get_novel(conn, novel_id)
        chapter_meta = {int(row["id"]): row for row in list_chapters(conn, novel_id)}
        nodes: dict[str, dict[str, Any]] = {}
        for fact in list_extracted_facts(conn, novel_id, fact_type="character_profile"):
            entities = fact.get("entities")
            name = str(entities[0]).strip() if isinstance(entities, list) and entities else ""
            if name:
                nodes[name] = {
                    "id": name,
                    "name": name,
                    "fact_id": fact["id"],
                    "confidence": fact["confidence"],
                    "status": fact["status"],
                    "kind": "character",
                }
        edges: list[dict[str, Any]] = []
        for fact in list_extracted_facts(conn, novel_id, fact_type="character_relationship"):
            entities = fact.get("entities")
            if not isinstance(entities, list) or len(entities) < 2:
                continue
            source, target = str(entities[0]), str(entities[1])
            parsed = _parse_relationship_content(str(fact.get("content") or ""))
            for name in (source, target):
                nodes.setdefault(
                    name,
                    {
                        "id": name,
                        "name": name,
                        "fact_id": None,
                        "confidence": "low",
                        "status": "pending_review",
                        "kind": "character",
                    },
                )
            chapter_id = fact.get("chapter_id")
            chapter = chapter_meta.get(int(chapter_id)) if chapter_id is not None else None
            extra = fact.get("extra") if isinstance(fact.get("extra"), dict) else {}
            edges.append(
                {
                    "id": fact["id"],
                    "source": source,
                    "target": target,
                    "relation_type": str(extra.get("relation_type") or parsed["relation_type"]),
                    "relation_label": str(extra.get("relation_label") or ""),
                    "attitude": str(extra.get("attitude") or ""),
                    "evolution": extra.get("evolution") if isinstance(extra.get("evolution"), list) else [],
                    "description": parsed["description"],
                    "confidence": fact["confidence"],
                    "status": fact["status"],
                    "chapter_id": chapter_id,
                    "chapter_order": chapter["chapter_order"] if chapter else None,
                    "chapter_title": chapter["title"] if chapter else "",
                    "source_quote": fact.get("source_quote") or "",
                }
            )
        return {"novel_id": novel_id, "nodes": list(nodes.values()), "edges": edges}


@app.post("/novels/{novel_id}/qa")
async def qa(novel_id: int, request: QuestionRequest) -> dict[str, Any]:
    with db() as conn:
        rows = list_chapters(conn, novel_id)
        if not rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        evidence = _retrieve_qa_evidence(conn, novel_id, request.question)
        payload = _qa_payload(request.question, evidence)
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="evidence_qa", novel_id=novel_id, request=request_data)
        fallback_output = _local_qa_answer(request.question, evidence)
        return await _cached_model_task(
            conn,
            "evidence_qa",
            payload,
            effective_model,
            request.force_refresh,
            job_id=job["id"],
            fallback_output=fallback_output,
        )


@app.post("/novels/{novel_id}/qa/start")
async def start_qa(novel_id: int, request: QuestionRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Non-blocking evidence Q&A start endpoint."""
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    with db() as conn:
        rows = list_chapters(conn, novel_id)
        if not rows:
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"question": question, "effective_model": effective_model}
        existing = _find_active_job_with_request(conn, novel_id, "evidence_qa", request_data)
        if existing is not None:
            return {
                "job_id": int(existing["id"]),
                "status": str(existing["status"]),
                "duplicated": True,
                "effective_model": effective_model,
            }
        job = create_analysis_job(conn, task_type="evidence_qa", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])
    background_tasks.add_task(_run_job_in_background, job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "duplicated": False,
        "effective_model": effective_model,
    }


async def _run_job_in_background(job_id: int) -> None:
    """Run an analysis job in a new DB connection (for BackgroundTasks)."""
    try:
        with db() as conn:
            job = get_analysis_job(conn, job_id)
            await _run_analysis_job(conn, job)
    except Exception as exc:
        # 兜底: 后台任务异常必须落日志; 标记 failed 再次失败时也不能吞掉原始错误。
        logger.exception("analysis job %s failed in background", job_id)
        try:
            with db() as conn:
                update_analysis_job(conn, job_id, status="failed", progress=100, error=str(exc))
        except Exception:
            logger.exception(
                "failed to mark analysis job %s as failed (original error: %s)", job_id, exc
            )


async def _run_analysis_job_by_id(job_id: int) -> dict[str, Any]:
    with db() as conn:
        try:
            job = get_analysis_job(conn, job_id)
            return await _run_analysis_job(conn, job)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            update_analysis_job(conn, job_id, status="failed", progress=100, error=str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            # 兜底: 未预期的执行异常不得让 job 永久卡在 queued/running。
            update_analysis_job(conn, job_id, status="failed", progress=100, error=str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc
# ---- Facts type extension: settings / timeline / conflict detection (PRD 9 d+e) ----


@app.post("/novels/{novel_id}/settings/start")
async def start_settings(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    with db() as conn:
        if not list_chapters(conn, novel_id):
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        existing = _find_active_job(conn, novel_id, "setting_extraction")
        if existing is not None:
            return {"job_id": int(existing["id"]), "status": str(existing["status"]), "duplicated": True, "effective_model": effective_model}
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="setting_extraction", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])
    background_tasks.add_task(_run_job_in_background, job_id)
    return {"job_id": job_id, "status": "queued", "duplicated": False, "effective_model": effective_model}


@app.post("/novels/{novel_id}/settings")
async def settings(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        if not list_chapters(conn, novel_id):
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="setting_extraction", novel_id=novel_id, request=request_data)
        return await _run_setting_extraction_job(conn, novel_id, effective_model, request.force_refresh, int(job["id"]))


@app.post("/novels/{novel_id}/events/start")
async def start_events(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    with db() as conn:
        if not list_chapters(conn, novel_id):
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        existing = _find_active_job(conn, novel_id, "event_extraction")
        if existing is not None:
            return {"job_id": int(existing["id"]), "status": str(existing["status"]), "duplicated": True, "effective_model": effective_model}
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="event_extraction", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])
    background_tasks.add_task(_run_job_in_background, job_id)
    return {"job_id": job_id, "status": "queued", "duplicated": False, "effective_model": effective_model}


@app.post("/novels/{novel_id}/events")
async def events(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        if not list_chapters(conn, novel_id):
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="event_extraction", novel_id=novel_id, request=request_data)
        return await _run_event_extraction_job(conn, novel_id, effective_model, request.force_refresh, int(job["id"]))


@app.post("/novels/{novel_id}/conflicts/start")
async def start_conflicts(novel_id: int, request: ModelTaskRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    with db() as conn:
        if not list_chapters(conn, novel_id):
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        existing = _find_active_job(conn, novel_id, "conflict_detection")
        if existing is not None:
            return {"job_id": int(existing["id"]), "status": str(existing["status"]), "duplicated": True, "effective_model": effective_model}
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="conflict_detection", novel_id=novel_id, request=request_data)
        job_id = int(job["id"])
    background_tasks.add_task(_run_job_in_background, job_id)
    return {"job_id": job_id, "status": "queued", "duplicated": False, "effective_model": effective_model}


@app.post("/novels/{novel_id}/conflicts")
async def conflicts(novel_id: int, request: ModelTaskRequest) -> dict[str, Any]:
    with db() as conn:
        if not list_chapters(conn, novel_id):
            raise HTTPException(status_code=404, detail="novel not found or has no chapters")
        effective_model = _effective_model(conn, request.model)
        request_data = request.model_dump() | {"effective_model": effective_model}
        job = create_analysis_job(conn, task_type="conflict_detection", novel_id=novel_id, request=request_data)
        return await _run_conflict_detection_job(conn, novel_id, effective_model, request.force_refresh, int(job["id"]))


@app.get("/novels/{novel_id}/conflicts")
def list_conflicts(novel_id: int) -> list[dict[str, Any]]:
    with db() as conn:
        try:
            return list_extracted_facts(conn, novel_id, fact_type="setting_conflict")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/dev/import-sample")
def import_sample() -> dict[str, Any]:
    sample = "First chapter\nLi Qing arrives at Qingshi Town. Wang warns him not to approach North Mountain."
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        tmp.write(sample.encode("utf-8"))
        tmp_path = Path(tmp.name)
    raw = tmp_path.read_bytes()
    text, encoding = detect_and_decode(raw)
    tmp_path.unlink(missing_ok=True)
    with db() as conn:
        return import_novel(
            conn,
            title="Sample novel",
            source_filename="sample.txt",
            encoding=encoding,
            text_hash=sha256_text(text),
            chapters=split_chapters(text),
        )