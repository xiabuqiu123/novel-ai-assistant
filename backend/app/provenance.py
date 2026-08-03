from __future__ import annotations

from typing import Any

from .cache import DEFAULT_PROMPT_VERSION, DEFAULT_SCHEMA_VERSION


def model_provenance(
    *,
    task_type: str,
    model_used: str,
    source: str,
    cache_hit: bool,
    local_fallback: bool,
    model_error: str | None,
    input_hash_value: str,
    cache_key_value: str,
    job_id: int | None,
    provider_call_attempted: bool = False,
    provider_call_succeeded: bool = False,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "model_used": model_used,
        "source": source,
        "cache_hit": cache_hit,
        "local_fallback": local_fallback,
        "model_error": model_error,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "input_hash": input_hash_value,
        "cache_key": cache_key_value,
        "job_id": job_id,
        "provider_call_attempted": provider_call_attempted,
        "provider_call_succeeded": provider_call_succeeded,
    }


def with_model_provenance(
    output: dict[str, Any],
    *,
    task_type: str,
    model_used: str,
    cache_hit: bool,
    input_hash_value: str,
    cache_key_value: str,
    job_id: int | None,
    source: str | None = None,
    model_error: str | None = None,
    provider_call_attempted: bool = False,
    provider_call_succeeded: bool = False,
) -> dict[str, Any]:
    visible_output = {key: value for key, value in output.items() if key != "_cache_metadata"}
    source_value = source or str(visible_output.get("source") or "remote_model")
    local_fallback = source_value in {"local_fallback", "cached_local_fallback"} or visible_output.get("status") in {
        "local_fallback",
        "needs_api_key",
    }
    error = model_error or visible_output.get("model_error")
    if error is not None:
        error = str(error)
    provenance = model_provenance(
        task_type=task_type,
        model_used=model_used,
        source=source_value,
        cache_hit=cache_hit,
        local_fallback=local_fallback,
        model_error=error,
        input_hash_value=input_hash_value,
        cache_key_value=cache_key_value,
        job_id=job_id,
        provider_call_attempted=provider_call_attempted,
        provider_call_succeeded=provider_call_succeeded,
    )
    return visible_output | {
        "source": source_value,
        "cache_hit": cache_hit,
        "cache_key": cache_key_value,
        "job_id": job_id,
        "provenance": provenance,
    }
