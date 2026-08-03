from __future__ import annotations

import hashlib

APP_VERSION = "mvp-0.1.0"
# P3 guard: cache keys embed prompt/schema versions, not prompt content.
# Changing any task prompt requires bumping DEFAULT_PROMPT_VERSION, and any
# JSON schema change requires bumping DEFAULT_SCHEMA_VERSION; otherwise new
# prompts silently hit stale cache rows (the version is the only guard).
DEFAULT_PROMPT_VERSION = "novel-ai-system-v2"
DEFAULT_SCHEMA_VERSION = "mvp-json-v1"


def input_hash(*parts: str) -> str:
    joined = "\n---part---\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def cache_key(
    *,
    model: str,
    task_type: str,
    input_hash_value: str,
    app_version: str = APP_VERSION,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
) -> str:
    return f"{app_version}:{prompt_version}:{model}:{task_type}:{schema_version}:{input_hash_value}"
