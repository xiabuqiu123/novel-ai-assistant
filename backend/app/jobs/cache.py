"""Task-domain module: cache (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import os
from ..cache import cache_key
from ..cache import input_hash
from ..database import get_cache
from ..database import put_cache
from ..database import update_analysis_job
from ..model_client import ModelHTTPError
from ..provenance import with_model_provenance
from .common import _as_int
from .common import _normalize_model_output
from typing import Any
from .. import model_client
from .. import secrets
from .. import database


def _task_cache_keys(conn, task_type: str, payload: str, model: str) -> list[str]:
    hash_value = input_hash(task_type, payload)
    rows = conn.execute(
        "SELECT cache_key FROM model_cache WHERE task_type = ? AND input_hash = ?",
        (task_type, hash_value),
    ).fetchall()
    return _unique_list(
        [_task_cache_key_from_hash(task_type, hash_value, model)]
        + [str(row["cache_key"]) for row in rows]
    )


def _task_cache_key(task_type: str, payload: str, model: str) -> str:
    return _task_cache_key_from_hash(task_type, input_hash(task_type, payload), model)


def _task_cache_key_from_hash(task_type: str, hash_value: str, model: str) -> str:
    return cache_key(model=model, task_type=task_type, input_hash_value=hash_value)


def _unique_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


_NON_CACHEABLE_STATUSES = {"invalid_model_json", "parse_error", "needs_api_key", "error"}

# Statuses the pipeline itself writes and that are safe to cache; anything else
# means the output did not come from a known success path and must not be cached.
_ALLOWED_CACHEABLE_STATUSES = {"", "ok", "local_fallback", "partial"}

# Empty extraction output is treated as a failure for these task types: real
# novels always produce characters/relationships, so an empty list is never a
# valid result here. conflict_detection is intentionally excluded ("no conflicts
# found" is a legitimate outcome); settings/events may legitimately be sparse.
_NON_EMPTY_REQUIRED_TASK_TYPES = {"character_extraction", "relationship_extraction"}


_REQUIRED_LIST_OUTPUT_FIELDS = {
    "character_extraction": "characters",
    "relationship_extraction": "relationships",
    "setting_extraction": "settings",
    "event_extraction": "events",
    "conflict_detection": "conflicts",
    "book_stage_outline": "stages",
}


def _invalid_output_reason(output: dict[str, Any], task_type: str) -> str | None:
    """Return a reason when a model output must not be cached, else None.

    Only schema-validated successful results may be written to model_cache.
    """
    status = str(output.get("status") or "")
    if status in _NON_CACHEABLE_STATUSES:
        return str(
            output.get("model_error")
            or output.get("parse_error")
            or f"model returned status '{status}'"
        )
    if status not in _ALLOWED_CACHEABLE_STATUSES:
        return f"model output has unrecognized status '{status}'"
    if status in {"local_fallback", "partial"}:
        # Pipeline-produced outputs (code-built local fallbacks and partially
        # successful batches) are schema-safe by construction and may
        # legitimately be sparse; deep validation is for model output only.
        return None
    required_list = _REQUIRED_LIST_OUTPUT_FIELDS.get(task_type)
    if required_list is not None and not isinstance(output.get(required_list), list):
        return f"model output missing required '{required_list}' list for {task_type}"
    if task_type in _NON_EMPTY_REQUIRED_TASK_TYPES and not output.get(required_list):
        return f"model output has empty '{required_list}' list for {task_type}"
    if task_type == "book_outline":
        outline = output.get("outline")
        chapters = outline.get("chapters") if isinstance(outline, dict) else None
        if not isinstance(chapters, list) or not chapters:
            return "model output missing usable outline.chapters for book_outline"
        brief_values = [str(ch.get("brief") or "").strip() for ch in chapters if isinstance(ch, dict)]
        if brief_values:
            empty_count = sum(1 for value in brief_values if not value)
            if empty_count / len(brief_values) > 0.30:
                return f"model output has too many empty briefs for book_outline ({empty_count}/{len(brief_values)} empty)"
    if task_type == "book_stage_outline":
        stages = output.get("stages")
        if not isinstance(stages, list) or not stages:
            return "model output missing usable stages for book_stage_outline"
        for index, stage in enumerate(stages):
            if not isinstance(stage, dict):
                return f"stage {index + 1} is not an object for book_stage_outline"
            filled = sum(
                1 for field in ("event", "resolution", "outcome")
                if str(stage.get(field) or "").strip()
            )
            if filled < 2:
                return f"stage {index + 1} missing two of event/resolution/outcome"
            start = _as_int(stage.get("chapter_start"))
            end = _as_int(stage.get("chapter_end"))
            if start < 1 or end < 1 or start > end:
                return f"stage {index + 1} has invalid chapter range ({start}-{end})"


    if task_type == "chapter_summary":
        short_summary = output.get("short_summary")
        if not isinstance(short_summary, str) or not short_summary.strip():
            return "model output missing non-empty short_summary for chapter_summary"
    return None


def _extraction_cache_probe(
    conn,
    task_type: str,
    payload: str,
    model: str,
    force_refresh: bool,
) -> dict[str, Any] | None:
    """Serial per-batch cache probe (no model call).

    Mirrors the cache-hit branch of _cached_model_task so the concurrent
    extraction phase only calls the model for uncached batches.
    """
    if force_refresh:
        return None
    hash_value = input_hash(task_type, payload)
    key = cache_key(model=model, task_type=task_type, input_hash_value=hash_value)
    cached = get_cache(conn, key)
    if cached is not None:
        pre_normalize = dict(cached)
        cached = _normalize_model_output(cached, task_type)
        if cached != pre_normalize:
            put_cache(conn, key=key, model=model, task_type=task_type, input_hash_value=hash_value, output=cached)
    if cached is not None and _invalid_output_reason(cached, task_type) is not None:
        with conn:
            conn.execute("DELETE FROM model_cache WHERE cache_key = ?", (key,))
        cached = None
    if cached is None:
        return None
    cache_meta = _cache_metadata(cached)
    return with_model_provenance(
        cached,
        task_type=task_type,
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


async def _call_extraction_batch_model(
    task_type: str,
    payload: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    fallback_output: dict[str, Any],
) -> dict[str, Any]:
    """Concurrent-friendly model call: no sqlite access, no caching.

    Mirrors _cached_model_task call/fallback semantics for batch extraction:
    no API key -> local fallback (not attempted); exceptions -> local fallback
    (attempted, failed, and therefore never cached). Validation and cache
    writes happen serially in the caller.
    """
    hash_value = input_hash(task_type, payload)
    key = cache_key(model=model, task_type=task_type, input_hash_value=hash_value)
    if not (api_key or os.getenv("OPENAI_API_KEY")):
        output = _with_cache_metadata(
            fallback_output,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )
        return with_model_provenance(
            output,
            task_type=task_type,
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=None,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )
    try:
        output = await model_client.call_openai_compatible(
            task_type=task_type,
            user_payload=payload,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception as exc:
        model_error = _model_error_text(exc)
        output = _with_cache_metadata(
            fallback_output | {"model_error": model_error},
            source="local_fallback",
            provider_call_attempted=True,
            provider_call_succeeded=False,
            model_error=model_error,
        )
        return with_model_provenance(
            output,
            task_type=task_type,
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=None,
            source="local_fallback",
            model_error=model_error,
            provider_call_attempted=True,
            provider_call_succeeded=False,
        )
    output = _with_cache_metadata(
        _normalize_model_output(output, task_type),
        source="remote_model",
        provider_call_attempted=True,
        provider_call_succeeded=True,
    )
    return with_model_provenance(
        output,
        task_type=task_type,
        model_used=model,
        cache_hit=False,
        input_hash_value=hash_value,
        cache_key_value=key,
        job_id=None,
        source="remote_model",
        provider_call_attempted=True,
        provider_call_succeeded=True,
    )


async def _cached_model_task(
    conn,
    task_type: str,
    payload: str,
    model: str,
    force_refresh: bool,
    job_id: int | None = None,
    fallback_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hash_value = input_hash(task_type, payload)
    key = cache_key(model=model, task_type=task_type, input_hash_value=hash_value)
    if job_id is not None:
        update_analysis_job(conn, job_id, status="running", progress=10)

    if not force_refresh:
        cached = get_cache(conn, key)
        if cached is not None:
            pre_normalize = dict(cached)
            cached = _normalize_model_output(cached, task_type)
            if cached != pre_normalize:
                put_cache(conn, key=key, model=model, task_type=task_type, input_hash_value=hash_value, output=cached)
        if cached is not None and _invalid_output_reason(cached, task_type) is not None:
            with conn:
                conn.execute("DELETE FROM model_cache WHERE cache_key = ?", (key,))
            cached = None
        if cached is not None:
            cache_meta = _cache_metadata(cached)
            source = _cached_source(cache_meta, cached)
            if job_id is not None:
                update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
            return with_model_provenance(
                cached,
                task_type=task_type,
                model_used=model,
                cache_hit=True,
                input_hash_value=hash_value,
                cache_key_value=key,
                job_id=job_id,
                source=source,
                model_error=cache_meta.get("model_error") or cached.get("model_error"),
                provider_call_attempted=False,
                provider_call_succeeded=False,
            )

    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    base_url = database.get_setting(conn, "base_url")
    if fallback_output is not None and not (api_key or os.getenv("OPENAI_API_KEY")):
        output = _with_cache_metadata(
            fallback_output,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )
        put_cache(conn, key=key, model=model, task_type=task_type, input_hash_value=hash_value, output=output)
        if job_id is not None:
            update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
        return with_model_provenance(
            output,
            task_type=task_type,
            model_used=model,
            cache_hit=False,
            input_hash_value=hash_value,
            cache_key_value=key,
            job_id=job_id,
            source="local_fallback",
            provider_call_attempted=False,
            provider_call_succeeded=False,
        )

    try:
        output = await model_client.call_openai_compatible(
            task_type=task_type,
            user_payload=payload,
            model=model,
            api_key=api_key,
            base_url=base_url,
        )
    except Exception as exc:
        model_error = _model_error_text(exc)
        if fallback_output is not None:
            output = _with_cache_metadata(
                fallback_output | {"model_error": model_error},
                source="local_fallback",
                provider_call_attempted=True,
                provider_call_succeeded=False,
                model_error=model_error,
            )
            if job_id is not None:
                update_analysis_job(conn, job_id, status="failed", progress=100, error=model_error)
            return with_model_provenance(
                output,
                task_type=task_type,
                model_used=model,
                cache_hit=False,
                input_hash_value=hash_value,
                cache_key_value=key,
                job_id=job_id,
                source="local_fallback",
                model_error=model_error,
                provider_call_attempted=True,
                provider_call_succeeded=False,
            )
        if job_id is not None:
            update_analysis_job(conn, job_id, status="failed", progress=100, error=model_error)
        raise

    output = _with_cache_metadata(
        _normalize_model_output(output, task_type),
        source="remote_model",
        provider_call_attempted=True,
        provider_call_succeeded=True,
    )
    invalid_reason = _invalid_output_reason(output, task_type)
    if invalid_reason is not None:
        if job_id is not None:
            update_analysis_job(conn, job_id, status="failed", progress=100, error=invalid_reason)
        return with_model_provenance(
            output,
            task_type=task_type,
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
    put_cache(conn, key=key, model=model, task_type=task_type, input_hash_value=hash_value, output=output)
    if job_id is not None:
        update_analysis_job(conn, job_id, status="completed", progress=100, result_cache_key=key)
    return with_model_provenance(
        output,
        task_type=task_type,
        model_used=model,
        cache_hit=False,
        input_hash_value=hash_value,
        cache_key_value=key,
        job_id=job_id,
        source="remote_model",
        provider_call_attempted=True,
        provider_call_succeeded=True,
    )


def _with_cache_metadata(
    output: dict[str, Any],
    *,
    source: str,
    provider_call_attempted: bool,
    provider_call_succeeded: bool,
    model_error: str | None = None,
) -> dict[str, Any]:
    return output | {
        "_cache_metadata": {
            "source": source,
            "provider_call_attempted": provider_call_attempted,
            "provider_call_succeeded": provider_call_succeeded,
            "model_error": model_error,
        }
    }


_PROVENANCE_TOP_KEYS = ("source", "cache_hit", "cache_key", "job_id", "provenance")


def _cacheable_output(output: dict[str, Any]) -> dict[str, Any]:
    """Canonical cache-row shape for batch-extraction results (P3-4).

    _cached_model_task rows store call metadata under _cache_metadata only.
    Batch paths wrap fresh results with with_model_provenance, which moves the
    metadata to top-level provenance keys; normalize those rows so every
    model_cache row has one consistent shape. Stats and cache-hit re-packing
    read _cache_metadata, so it is re-derived from the provenance here.
    """
    provenance = output.get("provenance") if isinstance(output.get("provenance"), dict) else {}
    row = {key: value for key, value in output.items() if key not in _PROVENANCE_TOP_KEYS}
    row["_cache_metadata"] = {
        "source": provenance.get("source") or "remote_model",
        "provider_call_attempted": bool(provenance.get("provider_call_attempted")),
        "provider_call_succeeded": bool(provenance.get("provider_call_succeeded")),
        "model_error": provenance.get("model_error"),
    }
    return row


def _cache_metadata(output: dict[str, Any]) -> dict[str, Any]:
    metadata = output.get("_cache_metadata")
    return metadata if isinstance(metadata, dict) else {}


def _cached_source(metadata: dict[str, Any], cached: dict[str, Any]) -> str:
    source = str(metadata.get("source") or cached.get("source") or "")
    if source == "remote_model":
        return "cached_remote_model"
    if source in {"local_fallback", "cached_local_fallback"}:
        return "cached_local_fallback"
    if source == "mixed":
        return "cached_partial"
    if cached.get("status") in {"local_fallback", "needs_api_key"}:
        return "cached_local_fallback"
    return "cached_remote_model"


def _model_error_text(exc: Exception) -> str:
    if isinstance(exc, ModelHTTPError):
        return f"Model API HTTP {exc.status_code}: {exc.message}"
    text = str(exc).strip()
    return text or type(exc).__name__