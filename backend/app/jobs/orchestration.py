"""Task-domain module: orchestration (moved verbatim from main.py by main-split refactor)."""

from __future__ import annotations

import asyncio
import json
from ..cache import cache_key
from ..cache import input_hash
from ..database import get_analysis_job
from ..database import get_chapter
from ..database import list_chapters
from ..database import put_cache
from ..database import update_analysis_job
from ..provenance import with_model_provenance
from .cache import _call_extraction_batch_model
from .cache import _cacheable_output
from .cache import _extraction_cache_probe
from .cache import _invalid_output_reason
from .cache import _task_cache_key
from .cache import _unique_list
from .cache import _with_cache_metadata
from .common import _PARTIAL_HINT
from .common import _as_int
from typing import Any
from .. import database
from .. import secrets


def _get_job_model(job: dict) -> str:
    try:
        import json as _json
        req = _json.loads(str(job.get("request_json") or "{}"))
        return str(req.get("effective_model") or "")
    except Exception:
        return ""


def _effective_model(conn, requested_model: str | None) -> str:
    if requested_model and requested_model.strip():
        return requested_model.strip()
    saved_model = database.get_setting(conn, "model", "gpt-4.1-mini").strip()
    return saved_model or "gpt-4.1-mini"


def _find_active_chapter_summary_job(conn, chapter_id: int) -> dict | None:
    """Return an existing queued/running chapter_summary job for the same chapter, or None."""
    row = conn.execute(
        "SELECT * FROM analysis_jobs WHERE chapter_id = ? AND task_type = 'chapter_summary' AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
        (chapter_id,),
    ).fetchone()
    return None if row is None else dict(row)


def _find_active_job(conn, novel_id: int, task_type: str) -> dict | None:
    """Return an existing queued/running job for the same novel + task, or None."""
    row = conn.execute(
        "SELECT * FROM analysis_jobs WHERE novel_id = ? AND task_type = ? AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
        (novel_id, task_type),
    ).fetchone()
    return None if row is None else dict(row)


def _find_active_job_with_request(conn, novel_id: int, task_type: str, request_data: dict[str, Any]) -> dict | None:
    """Return an active job with the same normalized request fields."""
    rows = conn.execute(
        "SELECT * FROM analysis_jobs WHERE novel_id = ? AND task_type = ? AND status IN ('queued', 'running') ORDER BY id DESC",
        (novel_id, task_type),
    ).fetchall()
    expected = _dedupe_request_key(request_data)
    for row in rows:
        job = dict(row)
        if _dedupe_request_key(_analysis_job_request(job)) == expected:
            return job
    return None


def _dedupe_request_key(request: dict[str, Any]) -> tuple[str, str, bool]:
    question = " ".join(str(request.get("question") or "").split()).lower()
    model = str(request.get("effective_model") or request.get("model") or "")
    force_refresh = bool(request.get("force_refresh"))
    return question, model, force_refresh


def _analysis_job_request(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("request_json") or "{}"
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("analysis job request_json is invalid") from exc
    if not isinstance(parsed, dict):
        raise ValueError("analysis job request_json must be an object")
    return parsed


def _extraction_batch_size(conn, setting_key: str, default: int) -> int:
    """Return a per-task extraction batch size, overridable via a settings row."""
    raw = database.get_setting(conn, setting_key)
    parsed = _as_int(raw)
    return parsed if parsed and parsed > 0 else default


def _extraction_concurrency(conn, setting_key: str, default: int = 2, maximum: int = 4) -> int:
    """Per-task model-call concurrency, overridable via a settings row.

    Out-of-range or non-numeric values fall back to the default.
    """
    raw = database.get_setting(conn, setting_key)
    parsed = _as_int(raw)
    if parsed is None or parsed < 1 or parsed > maximum:
        return default
    return parsed


def _run_summary_cache_key(model: str, task_type: str, novel_id: int, job_id: int) -> str:
    """Deterministic per-job cache key for one orchestration run-summary record."""
    return cache_key(
        model=model,
        task_type=f"{task_type}_run_summary",
        input_hash_value=input_hash(f"{task_type}_run_summary", f"novel:{novel_id}:job:{job_id}"),
    )


def _run_summary_cache_keys_for_novel(conn, novel_id: int, task_type: str) -> list[str]:
    """Enumerate existing run-summary cache records for one novel + task type."""
    rows = conn.execute(
        "SELECT cache_key, output_json FROM model_cache WHERE task_type = ?",
        (f"{task_type}_run_summary",),
    ).fetchall()
    keys: list[str] = []
    for row in rows:
        try:
            output = json.loads(str(row["output_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(output, dict):
            continue
        if int(output.get("novel_id") or -1) == novel_id:
            keys.append(str(row["cache_key"]))
    return _unique_list(keys)


def _write_run_summary(
    conn,
    *,
    model: str,
    task_type: str,
    novel_id: int,
    job_id: int,
    summary: dict[str, Any],
    failed_batches: list[dict[str, Any]],
) -> str:
    """Persist one {task_type}_run_summary cache record and point the job at it.

    The summary keeps the full provenance story (source / provider flags /
    model_error from _cache_metadata) plus failed-batch details so the job page
    can show where results came from after the fact.
    """
    key = _run_summary_cache_key(model, task_type, novel_id, job_id)
    metadata = summary.get("_cache_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    record = {k: v for k, v in summary.items() if k != "_cache_metadata"} | {
        "_cache_metadata": metadata,
        "novel_id": novel_id,
        "job_id": job_id,
        "failed_batches": failed_batches,
    }
    put_cache(
        conn,
        key=key,
        model=model,
        task_type=f"{task_type}_run_summary",
        input_hash_value=input_hash(f"{task_type}_run_summary", f"novel:{novel_id}:job:{job_id}"),
        output=record,
    )
    update_analysis_job(conn, job_id, result_cache_key=key)
    return key


def _base_source(source: str) -> str:
    """Map cached_* / mixed provenance sources back to the raw source labels."""
    if source == "cached_remote_model":
        return "remote_model"
    if source in {"local_fallback", "cached_local_fallback"}:
        return "local_fallback"
    if source == "cached_partial":
        return "mixed"
    return source


async def _run_batched_fact_extraction_job(
    conn,
    novel_id: int,
    model: str,
    force_refresh: bool,
    job_id: int,
    *,
    task_type: str,
    batch_size: int,
    payload_builder,
    persist_fn,
    local_fn,
    job_label: str,
) -> dict[str, Any]:
    rows = list_chapters(conn, novel_id)
    if not rows:
        raise ValueError("novel not found or has no chapters")
    batches = [rows[start:start + batch_size] for start in range(0, len(rows), batch_size)]
    update_analysis_job(conn, job_id, status="running", progress=5)
    attempted = False
    succeeded = 0
    fallback_count = 0
    persisted_total = 0
    seen_keys: set[Any] = set()
    model_errors: list[str] = []
    failed_batches: list[dict[str, Any]] = []

    # Phase 1 (serial): prepare every batch and probe its cache. The sqlite
    # connection never crosses threads; only model calls run concurrently.
    api_key = database.get_setting(conn, "api_key")
    api_key = secrets.decrypt_secret(api_key)
    base_url = database.get_setting(conn, "base_url")
    concurrency = _extraction_concurrency(conn, f"{task_type}_concurrency")
    wave_size = 50
    prepared: list[dict[str, Any]] = []
    for batch_rows in batches:
        full_rows = [get_chapter(conn, int(row["id"])) for row in batch_rows]
        payload = payload_builder(batch_rows, full_rows)
        prepared.append(
            {
                "batch_rows": batch_rows,
                "full_rows": full_rows,
                "payload": payload,
                "fallback": local_fn(full_rows),
                "cached": _extraction_cache_probe(conn, task_type, payload, model, force_refresh),
            }
        )

    def _cancelled_result() -> dict[str, Any]:
        return with_model_provenance(
            {"status": "cancelled", "task_type": task_type, "batches": len(batches)},
            task_type=task_type,
            model_used=model,
            cache_hit=False,
            input_hash_value="",
            cache_key_value="",
            job_id=job_id,
            source="local_fallback",
            provider_call_attempted=attempted,
            provider_call_succeeded=succeeded > 0,
        ) | {"persisted_facts": persisted_total}

    for wave_start in range(0, len(prepared), wave_size):
        if str(get_analysis_job(conn, job_id)["status"]) == "cancelled":
            update_analysis_job(conn, job_id, progress=5 + 90 * wave_start // len(batches))
            return _cancelled_result()
        wave = prepared[wave_start:wave_start + wave_size]

        # Phase 2 (concurrent): model calls only, no sqlite access.
        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_batch_call(spec: dict[str, Any]) -> None:
            if spec["cached"] is not None:
                return
            async with semaphore:
                spec["output"] = await _call_extraction_batch_model(
                    task_type,
                    spec["payload"],
                    model,
                    api_key,
                    base_url,
                    spec["fallback"],
                )

        await asyncio.gather(*(_bounded_batch_call(spec) for spec in wave))

        # Phase 3 (serial): validate, cache fresh outputs, persist, progress.
        for index, spec in enumerate(wave):
            batch_index = wave_start + index
            batch_rows = spec["batch_rows"]
            result = spec["cached"] if spec["cached"] is not None else spec["output"]
            invalid_reason = _invalid_output_reason(result, task_type)
            if invalid_reason is not None:
                error = f"{job_label} batch {batch_index + 1}/{len(batches)} failed: {invalid_reason}"
                update_analysis_job(conn, job_id, status="failed", progress=100, error=error)
                payload_hash = input_hash(task_type, spec["payload"])
                _write_run_summary(
                    conn,
                    model=model,
                    task_type=task_type,
                    novel_id=novel_id,
                    job_id=job_id,
                    summary=_with_cache_metadata(
                        {"status": "failed", "task_type": task_type, "batches": len(batches), "model_error": error},
                        source="mixed",
                        provider_call_attempted=True,
                        provider_call_succeeded=False,
                        model_error=error,
                    ),
                    failed_batches=[
                        {
                            "batch_index": batch_index + 1,
                            "chapter_range": f"{batch_rows[0]['chapter_order']}-{batch_rows[-1]['chapter_order']}",
                            "error": error,
                        }
                    ],
                )
                return with_model_provenance(
                    result,
                    task_type=task_type,
                    model_used=model,
                    cache_hit=False,
                    input_hash_value=payload_hash,
                    cache_key_value=cache_key(model=model, task_type=task_type, input_hash_value=payload_hash),
                    job_id=job_id,
                    source="remote_model",
                    model_error=error,
                    provider_call_attempted=True,
                    provider_call_succeeded=False,
                ) | {"persisted_facts": persisted_total}
            provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
            batch_error = str(provenance.get("model_error") or result.get("model_error") or "").strip()
            if batch_error:
                model_errors.append(batch_error)
            if provenance.get("provider_call_attempted"):
                attempted = True
                if provenance.get("provider_call_succeeded"):
                    succeeded += 1
            if result.get("source") == "local_fallback" or result.get("status") == "local_fallback":
                fallback_count += 1
                failed_batches.append(
                    {
                        "batch_index": batch_index + 1,
                        "chapter_range": f"{batch_rows[0]['chapter_order']}-{batch_rows[-1]['chapter_order']}",
                        "error": batch_error or "local_fallback",
                    }
                )
            if spec["cached"] is None:
                # Cache semantics match _cached_model_task: successful model
                # output and un-attempted local fallback are cacheable; a
                # fallback produced by a failed model call is never cached.
                attempted_call = bool(provenance.get("provider_call_attempted"))
                call_succeeded = bool(provenance.get("provider_call_succeeded"))
                if not attempted_call or call_succeeded:
                    put_cache(
                        conn,
                        key=_task_cache_key(task_type, spec["payload"], model),
                        model=model,
                        task_type=task_type,
                        input_hash_value=input_hash(task_type, spec["payload"]),
                        output=_cacheable_output(result),
                    )
            persisted_total += persist_fn(conn, novel_id, result, job_id, seen_keys)
            update_analysis_job(
                conn, job_id, status="running", progress=5 + 90 * (batch_index + 1) // len(batches)
            )

    if fallback_count == 0:
        # 无失败批次即视为模型来源（本轮调用或缓存命中）。
        status = "ok"
        source = "remote_model"
    elif not attempted:
        status = "local_fallback"
        source = "local_fallback"
    else:
        status = "partial"
        source = "mixed"
    last_error = model_errors[-1] if model_errors else ""
    _write_run_summary(
        conn,
        model=model,
        task_type=task_type,
        novel_id=novel_id,
        job_id=job_id,
        summary=_with_cache_metadata(
            {"status": status, "task_type": task_type, "batches": len(batches)},
            source=source,
            provider_call_attempted=attempted,
            provider_call_succeeded=attempted and succeeded == len(batches),
            model_error=last_error or None,
        ),
        failed_batches=failed_batches,
    )
    update_analysis_job(
        conn,
        job_id,
        status="completed",
        progress=100,
        error=last_error or _PARTIAL_HINT if status == "partial" else None,
    )
    summary = {"status": status, "task_type": task_type, "batches": len(batches)}
    return with_model_provenance(
        summary,
        task_type=task_type,
        model_used=model,
        cache_hit=False,
        input_hash_value="",
        cache_key_value="",
        job_id=job_id,
        source=source,
        provider_call_attempted=attempted,
        provider_call_succeeded=attempted and succeeded == len(batches),
    ) | {"persisted_facts": persisted_total}