# NewsLens Development Log

This file tracks all major development progress, decisions, challenges, and solutions for interview and portfolio purposes.

---

## [2026-04-30] - Project Setup

### What Was Done
- Created project structure and Cursor rules
- Set up .cursor/rules with 9 modular rule files
- Created PROJECT_CONTEXT.md and initial DEVLOG.md

### Technical Decisions
- Adopted modular .mdc rules for better Cursor Agent performance
- Added caching strategy and longitudinal tracking from the beginning

### Status
- Project initialized
- Ready to start Phase 1: Data Pipeline

## [2026-04-30] - Phase 1: Data Pipeline

### What Was Done
- Created `backend/` with `main.py`, `news_fetcher.py`, `nlp_pipeline.py`, `llm_analyzer.py`, and `database.py`
- Added `backend/requirements.txt` with FastAPI, SQLAlchemy, News/API/NLP dependencies
- Added `backend/.env` from root `.env.example` structure
- Implemented SQLAlchemy 2.0 schema for `articles`, `article_scores`, and `topic_analysis` with required longitudinal fields and indexes
- Implemented NewsAPI fetch flow for BBC News, Reuters, Fox News, CNN, and Al Jazeera with text cleaning, URL dedupe, and DB persistence
- Added FastAPI `/health` endpoint with standardized response envelope and startup table creation

### Technical Decisions
- Used SQLAlchemy declarative models and `create_all` startup initialization for quick local setup
- Added 24-hour cache check before external NewsAPI calls to protect rate limits
- Used `httpx` async client for outbound API calls and explicit error wrapping for graceful failures
- Scoped env loading to `backend/.env` for predictable local development behavior

### Challenges & Solutions
- Problem: Installing dependencies globally was blocked by local permission restrictions.
- Solution: Created project-local `.venv` and installed all backend dependencies there.
- Problem: Package import path failed when launching as module.
- Solution: Switched to package-relative imports for backend modules.

### Status
- Phase 1 backend foundation complete
- Pending final live ingestion verification once valid `NEWSAPI_KEY` is provided

## [2026-04-30] - Phase 1: Data Pipeline (Verification Complete)

### What Was Done
- Completed end-to-end validation for Phase 1 after environment keys were configured
- Verified database initialization creates `articles`, `article_scores`, and `topic_analysis` tables as expected
- Verified required `articles` indexes for `topic`, `fetched_at`, and `snapshot_date`
- Ran live NewsAPI ingestion for topic `climate change` and confirmed successful persistence to SQLite
- Confirmed health check returns the expected API envelope format

### Technical Decisions
- Kept route logic minimal and used service-style separation (`news_fetcher.py`) for easier extension toward `/analyze`
- Used package-relative imports in backend modules to support reliable module execution
- Maintained strict dependency pinning in `backend/requirements.txt` to keep local/dev behavior reproducible
- Retained 24-hour cache-first behavior to reduce NewsAPI usage and enforce rate-limit-safe ingestion

### Challenges & Solutions
- Problem: Initial server start attempts failed due to interpreter path and import resolution issues.
- Solution: Corrected execution path assumptions and switched to package-relative imports.
- Problem: Live external fetch initially failed under restricted network execution.
- Solution: Re-ran ingestion with proper network permissions and confirmed successful article retrieval/storage.

### Status
- Phase 1 is complete and validated
- Current verified state: `climate change` ingestion saved 25 articles to SQLite
- Next step: begin Phase 2 NLP sentiment/bias pipeline integration