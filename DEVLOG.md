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

## [2026-04-30] - Phase 2: NLP Layer

### What Was Done
- Replaced Phase 1 NLP placeholder with a production `NLPPipeline` that loads HuggingFace sentiment (`cardiffnlp/twitter-roberta-base-sentiment`) and political bias (`bucketresearch/politicalBiasBERT`) models once as a singleton.
- Added robust text preprocessing for NLP inference (HTML stripping, URL removal, whitespace normalization).
- Added per-model 512-token truncation before inference to keep inputs within model limits.
- Implemented batch analysis for all topic articles in one pass and persisted results to `article_scores` (scores + human-readable labels + raw score maps).
- Added `GET /scores?topic=...` endpoint that re-scores topic articles and returns aggregated per-outlet averages and dominant labels.
- Verified Phase 2 on existing `climate change` dataset (25 scored rows persisted, 0 null score/label fields, endpoint returned 5 outlets including empty-outlet placeholder when no articles exist).

### Technical Decisions
- Used a thread-safe singleton (`NLPPipeline.get_instance()`) and FastAPI lifespan initialization so models are loaded once at startup, never per request.
- Kept route logic thin while pushing scoring logic into service-style methods in `nlp_pipeline.py`.
- Included all tracked outlets in `/scores` output for consistent frontend shape, even when an outlet has zero articles in the current snapshot.
- Normalized Cardiff sentiment labels (`LABEL_0/1/2`) to `Negative/Neutral/Positive` for human readability.

### Challenges & Solutions
- Problem: HuggingFace model download failed in restricted execution and default cache path handling.
- Solution: Used local HF cache path and full-permission execution for model bootstrap during verification.
- Problem: Python 3.9 runtime rejected `zip(..., strict=True)`.
- Solution: Replaced with explicit length checks and standard `zip()` to preserve safety while remaining Python 3.9-compatible.
- Problem: FastAPI startup imported DB configuration before `.env` load, leading to wrong DB resolution.
- Solution: Reordered imports in `main.py` so `load_dotenv` runs before database module import.

### Status
- Phase 2 implementation complete and validated locally
- Next step: Phase 3 Claude missing-angle integration into `/analyze`

## [2026-04-30] - Phase 3: Claude LLM Integration

### What Was Done
- Replaced the Phase 3 placeholder in `backend/llm_analyzer.py` with a production `LLMAnalyzer` using the Anthropic Python SDK and model `claude-sonnet-4-6`.
- Added module-level `MISSING_ANGLE_SYSTEM_PROMPT` constant and implemented strict JSON-only prompting for missing-angle generation.
- Implemented pre-LLM context shaping: one article summary per outlet, each clipped to a maximum of 150 words, with a total context window of up to 8 outlets.
- Added cache-first logic against `topic_analysis` for same-day topic analysis reuse before any Claude API call.
- Added resilient fallback for Claude failures: full traceback logging, `missing_angle: null`, error flag propagation, and no crash behavior.
- Implemented `GET /analyze?topic=...` in `backend/main.py` to return combined output with fetch metadata, sentiment/bias scoring, and missing-angle data merged per outlet.

### Technical Decisions
- Kept route handlers thin by extracting score aggregation into `_build_outlet_scores` and encapsulating LLM logic in `LLMAnalyzer`.
- Reused existing outlet list (`ALLOWED_OUTLETS`) to enforce stable output shape, including missing-angle placeholders for all configured outlets.
- Stored structured Claude output in `topic_analysis.llm_summary` as JSON text while retaining top-level `missing_angle` in its dedicated column.
- Preserved existing scoring behavior (`score_topic_articles`) and layered LLM analysis on top without changing Phase 2 contracts.

### Challenges & Solutions
- Problem: Runtime validation in this environment could not load HuggingFace models due blocked remote access, and Anthropic network calls were unavailable.
- Solution: Verified `/analyze` response shape via FastAPI `TestClient` with an NLP dependency override while exercising real endpoint logic, confirming combined payload and LLM failure fallback handling.
- Problem: The requested `003-claude-llm.mdc` file name did not match repository rule numbering.
- Solution: Applied Claude integration constraints from the available Claude rule file and implemented the requested system prompt constant in module scope.

### Status
- Phase 3 backend integration is implemented and wired into `/analyze`
- Verified response includes sentiment, bias, and missing-angle components in one envelope
- LLM/network-dependent fields gracefully degrade when external access is unavailable

## [2026-04-30] - Phase 3: Resilience Test Hardening

### What Was Done
- Added `backend/tests/test_analyze_resilience.py` with two targeted tests to protect `/analyze` and LLM fallback behavior.
- Added test coverage for `LLMAnalyzer.generate_missing_angle(...)` to ensure Claude-call exceptions return safe fallback output (`missing_angle: null`, error flag set) without raising.
- Added integration-style `/analyze` endpoint test with dependency overrides to confirm endpoint remains stable and returns combined payload even when missing-angle generation fails.
- Installed and pinned `pytest==8.4.2` in `backend/requirements.txt` for reproducible local test execution.

### Technical Decisions
- Used in-memory SQLite with `StaticPool` to keep one shared test database across request threads used by FastAPI `TestClient`.
- Mocked external boundaries (`fetch_and_store_articles`, NLP dependency, and LLM generation) so tests validate contract behavior rather than third-party network availability.

### Challenges & Solutions
- Problem: Initial in-memory SQLite test setup failed with `no such table` due per-connection isolation.
- Solution: Switched test engine to `sqlite://` + `StaticPool` + `check_same_thread=False` so schema/data are shared across test and request execution.

### Status
- Protective regression tests are now in place and passing
- Current resilience guarantee: `/analyze` stays available and returns structured output when LLM calls fail

## [2026-05-01] - Phase 4a: Frontend Functional Dashboard

### What Was Done
- Replaced the static frontend with a fully interactive React dashboard in `frontend/app.js` (search, query-driven sections, and responsive layout).
- Implemented functional top navigation links (`Dashboard`, `Topics`, `Outlets`, `Methodology`) with dashboard as default active.
- Added hero search workflow with `Start Analysis` scroll/focus behavior, inline error handling, and skeleton loading states during fetch.
- Wired topic analysis to `GET /analyze?topic=...` using TanStack Query and introduced search history persistence (last 5 topics) via `localStorage` chips.
- Implemented dynamic dashboard modules: bias spectrum markers, outlet cards, headline comparison, emotional intensity, sentiment distribution grouped bars, 7-day timeline lines, and prominent missing-angle callout.
- Extended backend `GET /analyze` payload in `backend/main.py` with per-outlet latest `headline`, a 7-day `timeline`, and `missing_angle.reasoning` to support required frontend visualization features.

### Technical Decisions
- Used browser-native ESM imports (`esm.sh`) plus runtime JSX transpilation in `frontend/index.html` because local `npm` was unavailable in this environment.
- Kept TanStack Query as the single fetch layer for analysis requests to centralize loading/error/data state management.
- Reused backend outlet ordering to keep chart colors and cross-section outlet mapping consistent.
- Added backend timeline aggregation from persisted article snapshots and score records to avoid frontend-side data fabrication.

### Challenges & Solutions
- Problem: Frontend package manager tooling (`npm`) was not available, blocking a standard Vite/Tailwind setup.
- Solution: Implemented React + TanStack Query + Recharts with CDN/ESM runtime loading so the dashboard remains fully functional without local Node package installation.
- Problem: Original `/analyze` response did not include explicit headline comparison data or weekly timeline series needed by the UI requirements.
- Solution: Added lightweight backend response enrichments (latest outlet headline + 7-day bias timeline + reasoning text) while preserving the existing response envelope.

### Status
- Phase 4a frontend functionality is implemented with resilient loading/error/empty states and responsive behavior.
- Next step: run full visual QA against `ui-reference.png` in-browser and perform deployment-readiness checks for Phase 5.

## [2026-05-01] - Phase 4a: Frontend Constitution Alignment (Rule v1.3)

### What Was Done
- Re-reviewed the updated frontend constitution in `005-frontend-react.mdc` and re-applied styling/component requirements to the working dashboard.
- Refactored `frontend/app.js` into explicit modular components: `Header`, `Hero`, `BiasSpectrum`, `OutletGrid`, and `Timeline`.
- Updated neutral/balance visual mappings to the strict center gray `#9CA3AF` in spectrum and chart usage.
- Applied the Acctual-style visual system in `frontend/styles.css`: 24px card/input radii, glassmorphism cards (`rgba(255,255,255,0.7)` + `backdrop-filter: blur(12px)`), soft spread shadows, and 8px-grid-compliant margin/padding values.

### Technical Decisions
- Preserved TanStack Query data flow and existing API contracts while changing component architecture and styling, minimizing regression risk.
- Kept responsive breakpoints and skeleton loading behavior intact while adapting spacing and visual tokens to the new design constitution.

### Challenges & Solutions
- Problem: Existing UI already satisfied functional requirements but diverged from updated aesthetic constraints (radius, spacing system, neutral color token, component naming structure).
- Solution: Performed targeted refactor + design-token pass rather than rewriting flow logic, so behavior remained stable while design rules were enforced.

### Status
- Dashboard is now aligned with updated frontend rule set (v1.3) and remains fully functional.
- Next step: optional final pixel-polish pass directly against browser render for exact screenshot parity before deployment.

## [2026-05-01] - Phase 4b: Advanced Features (Compare, Profiles, Topic Trend, Share)

### What Was Done
- Added **outlet head-to-head comparison**: per-card “Compare” control, two-outlet selection with outline highlight, side-by-side panel (bias, sentiment, headline, emotional intensity, key framing phrase from existing `/analyze` outlet objects), and **Exit Comparison** to clear selection.
- Added **Source Profile** collapsible per outlet: historical aggregates from `article_scores` + `articles` (latest score per article, all topics) via new `GET /outlet-profile?outlet=`, shown as three stat pills plus a small bias sparkline over the last 14 snapshot days, with loading and error UI.
- Added **Topic coverage trend** below the narrative timeline: stacked **Recharts** `AreaChart` for article volume by outlet over the last 7 days for the current topic from `GET /topic-trend?topic=&days=7`, reusing `OUTLET_COLORS`, with loading and error states.
- Added **Share** in the results header: off-screen styled summary card + **html2canvas** PNG download (topic, bias distribution text, most left/right outlets, missing-angle teaser), with busy and inline error handling.
- Wired **CORS** middleware on the FastAPI app so a static frontend on another origin can call the API during development.
- Replaced the static `frontend/index.html` shell with a React mount (`#root` + `bundle.js`) and committed a **pre-built** `frontend/bundle.js` (esbuild JSX → `React.createElement`, runtime deps still loaded from `esm.sh` URLs inside the bundle).
- Added `frontend/package.json` (lists `html2canvas` and peers), optional local `esbuild` binary path ignored in `.gitignore`, and extended `frontend/styles.css` for new layouts (comparison grid, share card, outlet actions, sparkline).

### Technical Decisions
- Implemented `/outlet-profile` and `/topic-trend` only where specified; all comparison metrics stay on the existing `/analyze` payload (no duplicate analyze calls).
- Historical outlet stats intentionally come from **article_scores** joined with **articles** (per-out-row dedupe), because `topic_analysis` does not store per-outlet bias/sentiment—the requirement’s fallback SQL shape matches this implementation.
- Topic volume trend buckets by **UTC calendar day** derived from `articles.fetched_at`, grouped with `articles.topic`, aligned with “coverage volume” intent.

### Challenges & Solutions
- Problem: No `npm`/`node` toolchain in the agent environment to install packages or run the bundler.
- Solution: Downloaded the platform **esbuild** binary from the npm registry tarball, compiled `app.js` → `bundle.js`, and kept CDN `esm.sh` dependency URLs so the runtime matches Phase 4a’s ESM approach while supporting `html2canvas` and JSX.

### Status
- Phase 4b features are implemented end-to-end; rebuild `frontend/bundle.js` after editing `frontend/app.js` with `frontend/esbuild` or project-local `esbuild` (`npm run build` when Node is available).

## [2026-05-02] - Local dev scripts & stack smoke test

### What Was Done
- Hardened **`scripts/serve-frontend.sh`**: port detection uses a Python bind probe instead of `lsof`, tries **5173 → 8080 → 5174 → 3000**, and prints the canonical dashboard URL.
- Added **`scripts/serve-backend.sh`** so uvicorn always runs from the **repository root** with `.venv` (avoids broken `cd …venv/bin/python` merges).
- Added **`scripts/check-local-stack.sh`** to verify **`GET /health`** and static **`/` + `/bundle.js`** in one command.
- Added **`backend/__init__.py`** so `backend` is an explicit package for imports.

### Status
- Ran **pytest** (`backend/tests`) and **`check-local-stack.sh`** successfully against live local ports (API + static frontend).

## [2026-05-01] - Fix white screen: duplicate React (CDN + bundle)

### Bug
- Dashboard showed a blank page with `TypeError: Cannot read properties of null (reading 'useEffect')`, often from **multiple React copies** (hooks dispatcher attached to the wrong instance).

### Cause
- `frontend/app.js` imported React, React DOM, TanStack Query, Recharts, and html2canvas from **jsdelivr** `+esm` URLs. The esbuild output kept those as **runtime CDN imports** while other code paths still assumed a single bundled React, producing invalid hook behavior.

### Fix
- Switched `app.js` imports to **bare package specifiers** (`react`, `react-dom/client`, `@tanstack/react-query`, `recharts`, `html2canvas`) so **`npm run build`** bundles one React from `node_modules`.
- Confirmed `react` / `react-dom` versions align in `package.json`; no `vite.config` (esbuild-only frontend).
- Clean reinstall (`rm -rf node_modules package-lock.json`, `npm install`), `npm dedupe` (already single `react@18.3.1`), rebuilt `bundle.js`.
- Added **`npm run dev`** → `npm run build && npm run serve` for a one-shot local workflow.
- Tweaked `index.html` loading hint (no longer suggests jsdelivr for the app bundle).

### Status
- Rebuilt bundle verified: no jsdelivr URLs; static smoke test OK. Refresh the dashboard after rebuild.

## [2026-05-01] - Fix local dashboard “Failed to fetch” (backend down + explicit CORS)

### What Was Done
- Confirmed **`GET http://127.0.0.1:8000/health`** and **`GET /analyze?topic=…`** failed when no process listened on port **8000** — the static frontend at **5173** was running without the FastAPI server.
- Started the API with **`scripts/serve-backend.sh`** (repo-root **`.venv`**, **`uvicorn backend.main:app`** on **`127.0.0.1:8000`**), matching **`frontend/app.js`** default **`API_BASE_URL`** (`http://127.0.0.1:8000`).
- Replaced blanket **`allow_origins=["*"]`** with explicit development origins **`http://127.0.0.1:5173`** and **`http://localhost:5173`**, plus optional comma-separated **`CORS_ORIGINS`** for deployed frontends.
- Updated **`.cursor/.rules/006-env-config.mdc`** and **`001-backend-fastapi.mdc`** so local CORS expectations and **`CORSMiddleware`** are documented as mandatory.

### Verification
- **`/health`** returns JSON envelope; **`/analyze?topic=climate+change`** returns **200** with full analysis payload.
- OPTIONS preflight with **`Origin: http://127.0.0.1:5173`** returns **`access-control-allow-origin`** for that origin.

### Status
- For local development, run **`scripts/serve-backend.sh`** (or equivalent **`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`** from repo root) alongside **`npm run dev`** in **`frontend/`**.

## [2026-05-01] - Fix white screen on analysis results (assets, errors, API shape)

### Bug
- Running a topic search could leave the dashboard as a **blank white screen** instead of rendering results.

### Cause (combined)
- **Relative asset URLs** (`./bundle.js`, `./styles.css`) resolve incorrectly when the document path is not the site root (e.g. nested paths), so the React bundle may fail to load after navigation-style URLs.
- **Unhandled React render errors** (no error boundary) took down the entire tree with no UI fallback.
- **Fragile assumptions** about `/analyze` payload shape (missing arrays/labels) and a **regex lookbehind** in share teaser logic could break on some engines or odd payloads.

### Fix
- Switched **`frontend/index.html`** script and stylesheet to **absolute paths** (`/bundle.js`, `/styles.css`).
- Added **`installGlobalErrorHandlers()`** (window `error` + `unhandledrejection`) with a fixed **`#newslens-boot-error`** banner so failures are visible immediately.
- Added a React **`ErrorBoundary`** around the results stack with **Try again** / **Reload page**, plus **`normalizeAnalyzePayload`** / **`normalizeOutlet`** so outlets, timeline, and missing-angle blocks always match what components expect.
- **`console.log("[NewsLens] /analyze raw response:", payload)`** after JSON parse for debugging; **`sentimentBucket`** tolerates alternate label keys; **`firstSentence`** replaces lookbehind-based splitting; **`Timeline`** renders a friendly empty state when there are no rows.
- Rebuilt **`frontend/bundle.js`** via **`npm run build`**.
- Extended **`.cursor/.rules/007-self-review.mdc`** Verification: two topic searches required; white screen on results is a failure.

### Status
- Re-test: **climate change** and **trade war** searches with backend up; console should stay clean aside from intentional **`[NewsLens]`** analyze log.

## [2026-05-01] - Fix results crash: `toLowerCase` on null bias label

### Bug
- Error boundary showed **`Cannot read properties of null (reading 'toLowerCase')`** when analyzing topics (e.g. **trade war**) where an outlet had **`dominant_bias_label: null`**.

### Cause
- **`biasBadgeClass(outlet.dominant_bias_label)`** used `label.toLowerCase()`; default parameter **`""`** does not apply when the argument is explicitly **`null`**, so **`null.toLowerCase()`** threw.

### Fix
- **`biasBadgeClass`**: `String(label ?? "").toLowerCase()`.
- **`updateHistory`**: history entries coerced with **`String(item ?? "")`** before compare.

### Status
- Rebuilt **`frontend/bundle.js`**. Hard-refresh the dashboard and re-run **trade war**.
