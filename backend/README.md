# Long-Form Novel AI Analysis Assistant MVP Backend

This is the first MVP slice for the Android APK project. It deliberately avoids GraphRAG, cloud sync, advanced conflict review, and complex multi-model routing.

## What Works Now

- TXT text decoding with Chinese-friendly encoding detection.
- Automatic chapter recognition for common Chinese chapter titles.
- Long chapter chunking with stable text hashes.
- SQLite persistence for novels, chapters, chunks, settings, analysis jobs, and model output cache.
- Cache-first model task endpoints for chapter summaries, whole-book outline, basic character extraction, and evidence-based Q&A.
- Analysis job status records for summary, outline, character extraction, and Q&A tasks.
- Markdown export endpoint for a novel title, chapter list, and chapter excerpts.
- OpenAI-compatible chat completions API support.

## Run Locally

```powershell
cd C:\Users\Lenovo\Documents\Codex\2026-07-04\codex-api-ai-2\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open API docs:

```text
http://127.0.0.1:8000/docs
```

## Quick Manual Test

1. Call `POST /dev/import-sample` to import a small sample novel.
2. Call `GET /novels` to see the bookshelf data.
3. Call `GET /novels/{novel_id}/chapters` to view recognized chapters.
4. Call `POST /settings/model` to save API settings.
5. Call `POST /chapters/{chapter_id}/summary` to summarize a chapter.
6. Call `POST /novels/{novel_id}/outline` to generate a whole-book outline.
7. Call `POST /novels/{novel_id}/characters` to extract a basic character list.
8. Call `POST /novels/{novel_id}/qa` to ask an evidence-based question.
9. Call `GET /novels/{novel_id}/export/markdown` to export a Markdown report.

If no API key is configured, chapter summary, whole-book outline, and character extraction use local fallback results and cache them by stable cache key. Model-only tasks without a fallback return `needs_api_key`.

## Run Tests

```powershell
cd C:\Users\Lenovo\Documents\Codex\2026-07-04\codex-api-ai-2\backend
python -m pytest
```

## Next MVP Step

Install Flutter, then build the Android client screens against these backend endpoints:

- Bookshelf: `GET /novels`
- TXT import: `POST /novels/import-txt`
- Chapter list: `GET /novels/{novel_id}/chapters`
- Chapter reader: `GET /chapters/{chapter_id}`
- Summary: `POST /chapters/{chapter_id}/summary`
- Whole-book outline: `POST /novels/{novel_id}/outline`
- Characters: `POST /novels/{novel_id}/characters`
- Q&A: `POST /novels/{novel_id}/qa`
- Analysis jobs: `GET /analysis-jobs`, `GET /analysis-jobs/{job_id}`, `POST /analysis-jobs/{job_id}/retry`
- Markdown export: `GET /novels/{novel_id}/export/markdown`
- Model settings: `GET/POST /settings/model`
