"""System prompt (outputs/novel_ai_system_prompt.md) scope guard (2026-08-03).

The prompt file is the stable system layer sent with every model call
(model_client.load_system_prompt). Product scope decisions live in PRD; this
test keeps removed scope and contradictory shapes from leaking back in, and
keeps the authoritative-schema rule present.
"""

from app.cache import DEFAULT_PROMPT_VERSION
from app.model_client import load_system_prompt


def test_system_prompt_scope_matches_product_v2():
    text = load_system_prompt()

    # Removed product scope must not reappear (PRD 2.3: foreshadowing and
    # GraphRAG are cut; writing-assistant chapter plans are postponed).
    for removed in (
        "foreshadowing",
        "GraphRAG",
        "timeline_entries",
        "Plot Outline",
        "why_it_may_conflict",
        "non_conflicts",
        "needs_more_search",
    ):
        assert removed not in text, removed

    # Task payload schemas are authoritative; generic shapes removed.
    assert "Task Schemas Are Authoritative" in text
    assert "task payload schema always wins" in text

    # Current product identity and pipeline red lines are present.
    assert "\u4e66\u955c\u8fa8\u7ae0" in text
    assert "~200 chapters per arc" in text
    assert "Event Story-Time Ordering" in text
    assert "fact / inference / suggestion" in text

    # The prompt version is a stable constant in the cache module; bumping it
    # intentionally invalidates all model-cache keys (AGENTS.md red line).
    assert DEFAULT_PROMPT_VERSION.startswith("novel-ai-system-v")
