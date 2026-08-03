from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


class ModelHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# When frozen by PyInstaller the prompt is bundled via --add-data and lives
# under sys._MEIPASS; in dev it sits at the repo outputs dir next to backend/.
if getattr(sys, "frozen", False):
    SYSTEM_PROMPT_PATH = Path(sys._MEIPASS) / "novel_ai_system_prompt.md"
else:
    SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[2] / "outputs" / "novel_ai_system_prompt.md"


def load_system_prompt() -> str:
    try:
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "You are a cautious evidence-based Chinese novel analysis engine."


def parse_model_json(content: str, task_type: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return {
            "status": "invalid_model_json",
            "task_type": task_type,
            "raw_json": content,
            "parse_error": str(exc),
            "evidence_required": True,
        }
    return {
        "status": "ok",
        "task_type": task_type,
        "raw_json": content,
        "parsed_json": parsed,
        "evidence_required": True,
    }


async def call_openai_compatible(
    *,
    task_type: str,
    user_payload: str,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 120,
    retries: int = 1,
    retry_delay_seconds: float = 10,
) -> dict[str, Any]:
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        return {
            "status": "needs_api_key",
            "task_type": task_type,
            "facts": [],
            "inferences": [],
            "suggestions": ["Configure an API key in Model/API settings first."],
            "evidence": [],
        }

    url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": load_system_prompt()},
            {"role": "user", "content": user_payload},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(
                    f"{url}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                )
                if response.status_code < 200 or response.status_code >= 300:
                    retryable = response.status_code == 429 or response.status_code >= 500
                    if retryable and attempt < retries:
                        await asyncio.sleep(retry_delay_seconds)
                        continue
                    raise ModelHTTPError(response.status_code, _model_error_message(response))
                data = response.json()
            content = data["choices"][0]["message"]["content"]
            return parse_model_json(content, task_type)
        except httpx.TimeoutException:
            if attempt < retries:
                await asyncio.sleep(retry_delay_seconds)
                continue
            raise


async def test_openai_compatible_connection(
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float = 15,
) -> dict[str, Any]:
    key = api_key or os.getenv("OPENAI_API_KEY")
    url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    if not key:
        return {
            "ok": False,
            "status": "missing_api_key",
            "message": "API key is not configured.",
            "model": model,
            "base_url": url,
        }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return a tiny JSON object for a connection test."},
            {"role": "user", "content": "Return {\"ok\":true}."},
        ],
        "temperature": 0,
        "max_tokens": 16,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise ModelHTTPError(response.status_code, _model_error_message(response))
        data = response.json()
    return {
        "ok": True,
        "status": "ok",
        "message": "Connection test succeeded.",
        "model": model,
        "base_url": url,
        "response_id": data.get("id", ""),
    }


def _model_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:1200]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or error.get("type")
            if message:
                return str(message)
        if "message" in data:
            return str(data["message"])
    return str(data)[:1200]
