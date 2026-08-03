# AGENTS.md

## Project

Build a Windows desktop application for long-form web novel analysis. The app lets users import million-character novels, creates a structured novel knowledge base, and supports outline generation, character/worldbuilding extraction, relationship graphs, timeline analysis, setting-conflict detection, and evidence-based Q&A through API models.

The app should be treated as an AI-assisted novel editor and knowledge-base tool, not as a simple chatbot.

Product requirements live in `docs/PRD.md` (single source of truth, Chinese). This file defines engineering constraints only. When PRD and this file disagree on scope, the PRD wins.

## Platform

- Primary: Windows desktop. Flutter frontend (`flutter build windows`) + local FastAPI backend (`run_backend.ps1`), frontend connects to `127.0.0.1:8000`.
- Secondary: Android APK. Same codebase, build capability retained, no device-driven acceptance.
- Single-machine software: no accounts, no cloud sync, no self-hosted server. Only external dependency is the user-configured model API.

## Product Positioning

Name placeholder: Long-Form Novel AI Analysis Assistant.

Primary goal:

- Import a long novel (TXT, Chinese encoding detection).
- Split and index the text.
- Extract structured facts with chapter-level evidence.
- Build summaries, entities, events, rules, relationships, timelines, and conflict reports.
- Let users ask questions and generate detailed outlines using retrieved evidence.

Important boundary:

- Do not claim perfect accuracy.
- The product identifies probable issues and cites evidence.
- Human review must remain part of conflict detection and editorial judgment.

## Version Scope

Authoritative scope decisions (keep / cut / postponed) are in `docs/PRD.md` section 2. Summary:

In scope (current phase):

1. TXT import with encoding detection, chapter recognition, chunking.
2. Layered summaries: chunk → chapter → arc (~200 chapters) → book outline.
3. Batched extraction: characters, relationships, world rules, factions, locations, events, setting facts.
4. Setting conflict detection with human review workflow.
5. Evidence-based Q&A over a two-level retrieval pipeline.
6. Whole-book batch analysis with progress, cancellation, resume.
7. Markdown report export; model/API settings with usage stats.
8. All-Chinese UI.

Explicitly cut: EPUB/DOCX import, foreshadowing tracking, GraphRAG graph construction/traversal (structured fact layer retained), multi-model routing, cost dashboard (simple usage stats instead), cloud sync, multi-device, multi-book comparison.

Postponed (not deleted): writing assistant, Word export, chapter-range analysis.

## Required Pages

13 pages (converged from 18): bookshelf, novel import, novel detail (with analysis-jobs tab and whole-book analysis entry), chapter reader, outline center, character profiles, relationship graph, settings/worldbuilding page (rules + factions + locations + setting facts), event timeline, setting conflict detection (with review workflow), AI Q&A, export report, model & API settings.

UI language: Chinese everywhere, including test assertions. Material 3 theming pass after localization.

## Core Data Model

All AI conclusions must be backed by source evidence whenever possible.

Entities (existing tables retained): Novel, Chapter, TextChunk, Summary (via model_cache), Setting, AnalysisJob, ExtractedFact, ReviewAction.

`extracted_facts` types: `character_profile`, `character_relationship` (existing), plus `world_rule`, `faction`, `location`, `event`, `setting_fact`, `setting_conflict` (new).

Every extracted fact should include:

- `id`.
- `type`.
- `content`.
- `entities`.
- `chapter_id`.
- `chunk_id` when available.
- `source_quote`.
- `confidence`.
- `status` such as `active`, `updated`, `contradicted`, `pending_review`.
Character profiles must be structured per attribute: appearance, personality, identity/background, abilities, and key experiences are extracted as separate attributes, each carrying its own original-text evidence passages (multiple quotes with chapter references). All evidence items must be persisted, never truncated to the first one. Attributes with no textual basis are left empty and marked as unmentioned; never invent details.

## Long Text Strategy

Never send a million-character novel to a model in one request. This is a code-review red line: any whole-book operation must go through layered summarization or batched extraction.

Use this pipeline:

1. Import text.
2. Detect chapters.
3. Split long chapters into stable chunks.
4. Generate chunk summaries.
5. Generate chapter summaries from chunk summaries and selected evidence.
6. Generate arc summaries (~200 chapters per arc) from chapter summaries.
7. Generate the whole-book outline from arc summaries only.
8. Extract structured facts from chunks and chapters in batches (N chapters per batch), merging incrementally with dedup.
9. Build retrieval indexes (fact store + chapter-summary index).
10. Answer or generate using retrieved evidence only.

Failure handling: schema-invalid or failed model results must not be written to the model cache. Retrying a failed task must perform a real new model call.

## Setting Conflict Detection

The conflict detection page finds probable contradictions, not final judgments.

Detect these categories:

- Character profile conflicts: age, identity, faction, ability, personality, life/death state.
- World rule conflicts: cultivation rules, magic rules, restrictions, geography, economy, social rules.
- Timeline conflicts: impossible order, inconsistent elapsed time, simultaneous locations.
- Item and ability conflicts: item state, quantity, usage limits, cooldowns, damaged or restored objects.
- Plot logic conflicts: forgotten goals, repeated resolved problems, unexplained reversals.
- Relationship conflicts: enemy/ally/romantic/family/faction changes without transition.

Conflict workflow:

1. Extract setting facts with source quotes.
2. Group facts by entity and fact type.
3. Find candidate contradictions through deterministic rules and semantic retrieval.
4. Search for possible explanations in nearby and later chapters.
5. Ask the model to judge only with provided evidence.
6. Store severity and confidence.
7. Require user review before treating a conflict as confirmed.

Conflict records should include:

- `type`.
- `severity`: `high`, `medium`, `low`.
- `title`.
- `entities`.
- `earlier_evidence`.
- `later_evidence`.
- `possible_explanation`.
- `model_judgment`.
- `confidence`.
- `status`: `pending_review`, `confirmed`, `dismissed`, `explained`, `watching`.

## Hallucination Controls

AI hallucination is a serious risk in this product. Implement safeguards by default.

Rules:

1. Any factual answer must cite chapter-level evidence.
2. Separate `fact`, `inference`, and `suggestion` in outputs.
3. If evidence is insufficient, say so and ask for more context or perform retrieval.
4. Do not invent plot details, character motives, abilities, relationships, or world rules.
5. Conflict detection must include the original source quotes that triggered the judgment.
6. Generated outlines must explicitly list which existing facts they rely on.
7. The UI should mark AI findings as pending until user confirmation where appropriate.

## Cache-First Model Calling

The model API should be called with a stable prefix to maximize prompt caching.

Keep these parts stable and at the beginning of model requests:

1. Long system prompt.
2. JSON schemas.
3. Task policy.
4. Output rules.
5. Fixed examples.

Put variable data after the stable prefix:

1. Novel id.
2. Chapter ids or batch range.
3. Retrieved evidence.
4. User question.
5. Current task parameters.

Do not inject timestamps, random ids, changing progress text, changing user names, or debug strings into the stable prefix.

Use deterministic chunk ids and cache keys:

- Chunk key should be based on normalized text hash plus chapter id and model task version.
- Summary key should be based on model name, prompt version, task type, and input hash.
- Extraction key should be based on model name, schema version, task type, and input hash.
- Batched extraction keys must include the batch chapter range.

Recommended cache key format:

`{app_version}:{prompt_version}:{model}:{task_type}:{schema_version}:{input_hash}`

Only schema-validated successful results may be written to `model_cache`.

Prompt/schema version guard: modifying any task prompt or JSON schema requires bumping
`DEFAULT_PROMPT_VERSION` / `DEFAULT_SCHEMA_VERSION` in `backend/app/cache.py`. Cache keys embed
these versions but not prompt content; forgetting to bump makes the new prompt silently hit
stale cache rows (this is a red-line-level rule; the bump intentionally invalidates old keys).

## Engineering Guidance

- Keep the working core stable before adding new extraction types.
- Prefer structured APIs and parsers over ad hoc text parsing when practical.
- Store raw source text and extracted data separately.
- Make parsing and batch jobs resumable and idempotent.
- Every long-running operation needs progress, cancellation, retry, and failure state.
- Clean up zombie jobs (running-but-stale) on backend startup; mark them failed and retryable.
- Keep model prompts versioned.
- Keep migrations explicit.
- Test chapter splitting, cache hits, failure-not-cached behavior, extraction parsing, and evidence citation behavior.
- Frontend: split `main.dart` into per-page files before the localization pass.

## Version Control Discipline

Code that is not committed to git does not exist. An uncommitted implementation can be wiped by a single destructive git command with no recovery source. This section exists because a previous run lost ~456 lines of uncommitted `backend/app/main.py` work (B1/B2/B3/B5) to an incautious `git checkout -- backend/app/main.py`.

Rules:
1. Start every task block by creating a task branch: `git switch -c codex/<task>`. Commit a `WIP` snapshot (`git add -A && git commit -m "WIP: <task> snapshot"`) before writing real code, so there is always a point to fall back to.
2. Commit at each small milestone (one test turning green, one extraction type done). WIP commits do not need to be clean or pretty; they must exist. Never let multiple feature blocks accumulate as uncommitted changes in the working tree.
3. Never run destructive git commands against a file that has uncommitted changes. Treat `git checkout -- <file>`, `git restore <file>`, and `git clean -fd` as red lines on par with "do not delete `novel_mvp.sqlite3`". To undo a mistaken editor edit, use the editor undo / local history instead; if you truly must discard a file, first `git stash push -- <file>` so it can be recovered with `git stash pop`.
4. Before any risky file operation, snapshot the target file: `git add <file>` (so HEAD/index has it) or copy it out of tree. "If it is not in git and not backed up, assume losing it is permanent."
5. Reduce single-file blast radius. Large files (~1500+ lines) such as `backend/app/main.py` must be split by task domain into separate modules (`app/jobs/<task>.py`, etc.) when adding new functionality; `main.py` should only assemble routes. A single 3800+ line file makes one bad line-number edit take down the whole file and makes `checkout --` loss catastrophic.
6. Keep an editor local history / autosave enabled (VS Code Local History or equivalent) and, for critical files, write periodic copies to a `.snapshots/` directory. This guarantees the "I have a backup" recovery path even when git does not contain the latest work.
7. Long text and whole-book batch operations must continue to honor the red lines in "Long Text Strategy" and "Cache-First Model Calling"; the version control rules above supplement, they do not relax, those constraints.

### File Editing Method on Windows (verified 2026-07-31; do not retry rejected paths)

Verified facts in this environment:

1. The bare commands `apply_patch` / `applypatch` / `apply-patch` resolve to `C:\Users\Lenovo\.codex\bin\apply_patch.bat` and fail with `Access is denied`. Never invoke them.
2. Piping a patch to that command via stdin does not work either (`codex.exe --codex-run-as-apply-patch` requires the patch as a command-line argument).
3. `& "C:\Users\Lenovo\.codex\bin\apply_patch.ps1" "<full patch text as one argument>"` works, BUT it rewrites the changed lines as LF while leaving the rest CRLF, producing mixed line endings (verified on a CRLF probe: `CRLF=2 LF=1`). It must NOT be used on files that must stay CRLF (e.g. `backend/app/main.py`).

Default editing method (accurate for all text files, preserves the file's own line endings):

Run a small inline Python script with an exact-match assertion; never do line-number-based blind patching.

```python
from pathlib import Path
p = Path("<repo-relative path>")
text = p.read_bytes().decode("utf-8").replace("\r\n", "\n")
old = "<exact old text>"
new = "<exact new text>"
assert text.count(old) == 1, f"pattern count={text.count(old)}"
text = text.replace(old, new, 1)
out = text.replace("\n", "\r\n").encode("utf-8")
p.write_bytes(out)
```

Rules:

- Assert the old text matches exactly once before replacing; if the assertion fails, abort without writing and re-inspect the file first.
- After every edit, verify line endings and BOM:
  ```python
  b = Path("<file>").read_bytes()
  assert b.count(b"\n") == b.count(b"\r\n"), "stray LF"
  assert b[:3] != b"\xef\xbb\xbf", "unexpected BOM"
  ```
- `backend/app/main.py` baseline is CRLF + UTF-8 no BOM (verified 5621 CRLF / 0 LF); keep it that way. The same CRLF preservation applies to `backend/app/database.py`, `AGENTS.md`, and docs — check each file's own line endings first and preserve them.
- For brand-new files, match the line endings of neighboring files.

## Acceptance Criteria

Authoritative criteria are in `docs/PRD.md` section 8 (desktop edition, validated against a 7M-character / 3170-chapter novel). Highlights:

1. `flutter build windows` works; default backend is `127.0.0.1:8000`.
2. Chinese TXT import has no mojibake; chapter recognition is accurate.
3. Whole-book batch analysis completes with progress, cancellation, and resume.
4. Layered book outline generates reliably; characters cover the full chapter range.
5. Settings/timeline/conflict pages show evidence-backed data; conflicts are reviewable.
6. Q&A responds in seconds with accurate citations.
7. Failed tasks are never cached; repeated successful tasks always hit cache.
8. UI is fully Chinese; backend and frontend test suites are green.

---

## BMAD Method v6.10.0 - Codex Integration

This project has BMAD Method v6.10.0 installed locally. Use the local BMAD files when the user mentions `bmad`, asks to run a BMAD skill, or asks for BMAD-style planning, analysis, architecture, stories, review, or agent perspectives.

### BMAD File Layout

- Skill manifest: `_bmad/_config/skill-manifest.csv`
- Skill files: paths listed in the manifest, for example `_bmad/core/bmad-help/SKILL.md`
- Claude mirror: `.claude/skills/<skill-name>/SKILL.md`
- Configuration: `_bmad/config.toml`
- Output directory: `_bmad-output/`
- Project knowledge: `docs/`

### BMAD Usage Rules

1. Read `_bmad/_config/skill-manifest.csv` to find the requested skill and its canonical path.
2. Read the `SKILL.md` file at the manifest path under `_bmad`; only use `.claude/skills/` as a fallback mirror.
3. Follow the skill instructions directly and adapt outputs to this project only where the skill allows customization.
4. Write durable BMAD artifacts to `_bmad-output/` unless the user asks for another location.
5. If a requested BMAD skill is missing, report the missing path and check `.claude/skills/<skill-name>/SKILL.md` before proceeding without the skill.

### Common BMAD Skills

| Skill | Purpose |
|------|---------|
| `bmad-help` | Analyze current BMAD state and recommend next steps |
| `bmad-brainstorming` | Facilitate structured brainstorming |
| `bmad-advanced-elicitation` | Push deeper critique and refinement |
| `bmad-document-project` | Generate AI-oriented project documentation |
| `bmad-create-architecture` | Create architecture artifacts |
| `bmad-create-epics-and-stories` | Split plans into epics and stories |
| `bmad-code-review` | Review code through BMAD workflow |
| `bmad-party-mode` | Run multi-agent discussion |
| `bmad-agent-pm` | Product manager perspective |
| `bmad-agent-analyst` | Business analyst perspective |
| `bmad-agent-architect` | Architect perspective |
| `bmad-agent-dev` | Developer perspective |