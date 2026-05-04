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

### Phase 4b follow-up (verified): Confidence UI and query suggestions
- **Coverage status** from `GET /analyze` (`high` / `developing` / `insufficient`) is normalized in the dashboard state and drives UI: an amber glass **developing story** banner (with pulse indicator) above the results stack, and an **insufficient coverage** card with a **Try a broader search** control that returns focus to the hero search.
- **Suggested topics** appear as uppercase pill tags under the search field (aligned with outlet bias pill styling); choosing one fills the query and runs the same **Analyze** path as manual submit.
- **Start Analysis** in the header scrolls to `#search-anchor` and focuses/selects the search input; the **bias spectrum** bar shows a **shimmer** overlay while React Query reports `isFetching` for the active topic (works with `keepPreviousData` on refresh).
- Rebuilt `frontend/bundle.js` after `frontend/app.js` changes; spacing and radii stay on the 8px grid and 24px card radius per the frontend constitution.

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

## [2026-05-01] - Data logic: bias mix, full NLP scoring, missing angle

### What changed
- **Bias header percentages (BUG 1)**: Server now computes **`bias_distribution`** from each outlet’s **dominant bias label** using the same **left / center / right** keyword bucketing as the UI (not raw scores). The results header prefers this API field so the mix matches outlet badges. Shared logic lives in **`backend/bias_utils.py`**; frontend mirrors it in **`biasSpectrumBucket`** for fallback and badge colors.
- **Missing scores for some outlets (BUG 2)**: After a non-cached NewsAPI write, **`news_fetcher.py`** runs **`NLPPipeline.score_topic_articles`** so new rows get **`article_scores` immediately. **`/analyze`** calls scoring, then if any article still has no score (e.g. partial failure), it runs scoring again.
- **Missing angle / “4 summaries” (BUG 3)**: **`llm_analyzer.py`** builds one summary per **allowed outlet** (latest article: title + body, trimmed to ~150 words) in fixed source order, logs summary **count and sources** before calling Claude, and requires **at least 3** outlets (was 4) before the LLM call.

### Tests
- Added **`backend/tests/test_bias_utils.py`**. Full **`backend/tests`** pass after **`pip install -r backend/requirements.txt`**. **`npm run build`** in **`frontend/`** rebundles **`bundle.js`**.

### Status
- Re-run a multi-outlet topic with the backend up; check logs for **`Missing angle Claude input`** and confirm BBC/Reuters show labels when articles exist for the topic.

## [2026-05-01] - Dynamic outlets, pre-persist NLP, framing summaries

### What changed
- **Removed hardcoded five outlets.** **`news_fetcher.py`** queries NewsAPI **`everything`** with **`pageSize=100`** against a **pool of 15** IDs (`NEWSAPI_BROAD_SOURCES`), groups by **`source.name`**, keeps outlets with **≥2 articles**, takes **top 5 by volume**, then runs **`NLPPipeline.analyze_batch`** on all retained texts **before** inserting **`Article`** / **`ArticleScore`** rows. Topic rows are **replaced** on fresh fetch (delete prior topic articles + framing).
- **Extractive framing (`backend/framing_extract.py`)**: Per selected outlet, concatenate **title + first 100 words** for each article, pick **two** sentences with highest sentiment charge (**1 − P(neutral)**), persist on **`TopicOutletFraming`** (`topic`, `source`, `framing_summary`).
- **`/analyze`**: Builds outlet payload from **`compute_selected_outlets_from_db`** / fetch **`selected_outlets`**; **`bias_distribution`** uses **fixed denominator 5** (`left_pct = round(100 * left_count / 5)`); exposes **`most_left_outlet`** / **`most_right_outlet`** via **`extrem_bias_outlets`** (min / max **`avg_bias_score`**). Timeline and topic-volume charts key only **dynamic** outlet names.
- **`llm_analyzer.py`**: Missing-angle JSON schema uses **dynamic outlet list** from selection (no fixed five keys).
- **Frontend**: Results header bias mix uses **API percentages only**; spectrum extremes prefer **`most_left_outlet` / `most_right_outlet`**; outlet cards show **`framing_summary`** first.

### Tests / build
- **`PYTHONPATH=. python3 -m pytest backend/tests -q`**: 4 passed. **`npm run build`** rebundles **`frontend/bundle.js`**.

### Notes
- NewsAPI **`sources`** IDs must all be valid or the request fails; pool matches the product brief (`the-guardian-uk`, etc.). Adjust IDs against NewsAPI’s sources index if a publisher slug errors at runtime.

## [2026-05-01] - Phase 1 follow-ups: outlets, blended bias, framing

### Issue 1 — only ~3 outlets from NewsAPI
- **`MIN_ARTICLES_PER_SOURCE`** is **1** (single article qualifies per outlet).
- After the initial **`/v2/everything`** call (15-source pool), if fewer than **5** qualifying sources, the fetcher calls **`/v2/top-headlines?country=us&q=...`**, then if still short, **`/v2/everything`** again **without** the `sources` filter so any publisher can fill slots. **`[NewsLens]`** logs list **article counts per source** after each stage.
- **URL dedupe**: before scoring, article rows whose **`url`** already exists for **another** topic are dropped to avoid SQLite **`UNIQUE`** on `articles.url` when the same story appears across searches.

### Issue 2 — all outlets looked “CENTER” from HF alone
- **`NLPPipeline.analyze_batch`** blends **`final_axis = 0.4 * hf_axis + 0.6 * keyword_axis`**. HF contributes a probability-weighted axis from **`politicalBiasBERT`** labels; **`keyword_axis`** uses the specified left/right keyword lists (substring multi-word phrases, `\b…\b` for single words where appropriate). **`raw_scores["bias_blend"]`** stores **`hf_axis`**, **`keyword_axis`**, **`final_axis`**.
- **`bias_label_from_axis`** in **`bias_utils.py`** maps axis → **Left / Center / Right** (thresholds **0.47** / **0.51**); per-article labels and outlet **`dominant_bias_label`** both derive from this so the spectrum matches **`avg_bias_score`**.
- **`NLPPipeline.rescore_all_articles(db)`** replaces all **`article_scores`** using the new blend (run after deploy to refresh historical rows).

### Issue 3 — empty framing summaries
- **`framing_extract`**: **`SENTENCE_MIN_LEN = 10`**, structured **`logging`** for sentence counts, per-sentence charges, and neutral fallback; if all charges ≈ 0, uses the **first k** sentences; **`fallback_framing_best_article`** takes the **first two sentences** of the article with highest sentiment charge.
- **`news_fetcher`** chains extractive → fallback → corpus/title fallbacks; **`outletFramingBody`** in **`frontend/app.js`** avoids the empty-state copy when **`article_count > 0`** (uses **`Lead: …`** from **`headline`** if needed).

### Verification / build
- **`PYTHONPATH=. python3 -m pytest backend/tests -q`**: pass. **`npm run build`** in **`frontend/`** refreshes **`bundle.js`**.
- Example local check on **`us-iran war`** after a fresh fetch: **5** outlets, non-empty **`framing_summary`**, mixed **`bias_distribution`** (e.g. Bloomberg **Left**, Al Jazeera **Right**, others **Center** from blended axis).

## [2026-05-01] - Missing Angle: Claude → Google Gemini

### What changed
- **`backend/llm_analyzer.py`**: Replaced Anthropic **`claude-sonnet-4-6`** with **`google-generativeai`** and model **`gemini-2.0-flash`** (requested **`gemini-1.5-flash`** is not returned by **`list_models`** for this API; Flash-tier equivalent). **`MISSING_ANGLE_SYSTEM_PROMPT`**, JSON schema, **`topic_analysis` same-day cache**, and exception fallback behavior are unchanged.
- **`backend/requirements.txt`**: **`anthropic`** → **`google-generativeai==0.8.6`**.
- **Env**: **`GEMINI_API_KEY`** only; **`ANTHROPIC_API_KEY`** removed from **`.env`** and **`.env.example`**.
- **Cursor rules**: **`004-claude-llm.mdc`** renamed to **`004-llm-analyzer.mdc`** ( **`003-database.mdc`** keeps the **`003-`** slot ). **`006-env-config.mdc`** lists **`GEMINI_API_KEY`** instead of Anthropic.

### Reason
Anthropic API credits exhausted; Gemini free tier covers Missing Angle generation.

### Verification
- Restart backend; search **`trade war`**; Missing Angle should call Gemini when **`GEMINI_API_KEY`** is set and ≥3 outlet summaries exist. Local smoke test hit **`429`** quota on the dev key (confirms the **`gemini-2.0-flash`** path end-to-end). Optional **`GEMINI_MODEL`** env overrides the default Flash model id.

## [2026-05-01] - Outlet marker spectrum (bias bar redesign)

### What changed
- **`frontend/app.js`**: Replaced the decorative fixed thirds spectrum with an **outlet marker spectrum**: gradient bar segments sized by **`bias_distribution`** (normalized so segment widths match left/center/right outlet mix), pins at **`avg_bias_score`** on a **0–1** horizontal axis (replacing the incorrect **`(score+1)/2`** mapping), marker colors aligned to **`dominant_bias_label`** (**#3B82F6** / **#6B7280** / **#EF4444**), horizontal proximity stacking for overlapping pins, native **`title`** tooltips with exact scores, axis row (**most left** · **center** · **most right**) and a footnote using **`scoring.article_count`** from **`/analyze`** (fallback: sum of outlet **`article_count`**).
- **`frontend/styles.css`**: New layout/styles for the gradient bar, marker strip, axis labels, and footnote; removed the old equal-thirds track and static legend row.

### Reason
The prior bar used three equal columns and mis-scaled scores; it did not communicate real outlet positions or population mix.

### Verification
- **`npm run build`** in **`frontend/`** regenerates **`bundle.js`**.

## [2026-05-01] - Missing Angle: quota UX + Gemini 429 fallback

### What changed
- **`frontend/app.js`**: Missing Angle body and **Reasoning** use **`missingAnglePresentationalCopy`**: when **`value`** is empty or **reasoning** matches quota signals (**quota** / **429** / **exceeded**), both show **`Editorial analysis temporarily unavailable. Check back shortly.`** instead of raw API JSON. Share-card teaser uses the same rule so quota errors never surface in the exported image text.
- **`backend/llm_analyzer.py`**: Catch **`ResourceExhausted`** and **`TooManyRequests`** (429-class) and quota-shaped **`Exception`**s; **no retries**; **`logger.warning`** with the real exception; return **`GEMINI_QUOTA_USER_MESSAGE`** as **`error_message`** (same copy as the frontend). Non-quota failures still log traceback and return **`str(exc)`** as today.
- **`backend/.env`**: Comment — *If hitting quota, enable billing at aistudio.google.com* (next to **`GEMINI_API_KEY`**).
- **`backend/tests/test_analyze_resilience.py`**: **`test_llm_analyzer_quota_returns_safe_message_no_raw_payload`** for **`ResourceExhausted`**.

### Reason
Gemini free-tier quota returns **429** / JSON-heavy errors that were shown in **`/analyze`** **`missing_angle.reasoning`**; Missing Angle must stay optional and never expose vendor error payloads to users.

### Verification
- **`PYTHONPATH=. python3 -m pytest backend/tests/test_analyze_resilience.py -q`**: pass.
- **`npm run build`** in **`frontend/`**: **`bundle.js`** updated.

## [2026-05-02] - Credible outlet allowlist (15 sources)

### What changed
- **`backend/news_fetcher.py`**: Replaced the broad NewsAPI source pool and open **`everything`** / **`top-headlines`** fallbacks with a **fixed 15-source credibility allowlist** (AP, Reuters, BBC, NBC, ABC, CBS, NPR, WaPo, WSJ, Guardian, NYT, CNN, Fox, MSNBC, Bloomberg). Fetches **`everything`** with **`sources=`** that list only. Ingestion filters by **`source.id`** and maps IDs to **`SOURCE_DISPLAY_NAMES`** (e.g. **`associated-press`** → **Associated Press**). Selection remains **top 5 by article count** among approved outlets, with **tier order** as tie-break. If **fewer than 3** approved outlets have articles (after dedupe), the fetcher returns **`coverage_message`**: *Not enough credible coverage found for this topic yet* and **does not** persist articles. **24h cache** is reused only when **`compute_selected_outlets_from_db`** finds approved outlets; otherwise stale rows for the topic are **invalidated** and a fresh fetch runs.
- **`backend/tests/test_analyze_resilience.py`**: Seed / mock outlets use **Reuters** instead of **Al Jazeera English** so tests align with the approved set.
- **`frontend/app.js`**: **`normalizeAnalyzePayload`** exposes **`coverage_message`**; **`AnalysisResults`** shows a **Coverage** card when there are no outlets and the backend sent the shortfall message.
- **`frontend/styles.css`**: Styles for **`.coverage-shortfall`**.
- **`.cursor/.rules/000-core.mdc`**: Tech stack line documents the **15 approved tiers**.

### Reason
Dynamic outlet picking pulled low-quality domains (e.g. tabloid / niche sites) via unfiltered **`everything`** queries; NewsLens should compare **major, pre-vetted** outlets only.

### Verification
- **`PYTHONPATH=. python3 -m pytest backend/tests/test_analyze_resilience.py -q`**: pass.
- **`npm run build`** in **`frontend/`**: **`bundle.js`** updated.
- NewsAPI spot-check (**`trade war`**, **`climate policy`**): responses contained **only** allowlisted **`source.id`** values; no stray publishers.

## [2026-05-04] - Categorized vetted sources + query relaxation

### What changed
- **`backend/news_fetcher.py`**: Replaced the flat 15-ID list with **`VETTED_SOURCES_BY_CATEGORY`** (**GENERAL** 22+, **TECH** 10, **FINANCE** 10, **SCIENCE_HEALTH** 5). **`detect_source_categories_for_query`** always keeps **GENERAL** and adds other pools from keyword sets (e.g. chip/software → **TECH**, market/stock/CBDC → **FINANCE**). NewsAPI **`everything`** calls are **chunked to 20 source IDs** per request (API limit), merged client-side. **Iterative relaxation**: if a fetch pass yields **&lt; 10** articles in the vetted pool, the fetcher retries with **`relax_search_query`** (drop years / filler tokens), then with **all categories** and the relaxed query. Successful runs return **`source_pool`** and **`query_used`** in **`fetch`** metadata.
- **`backend/main.py`**: **`GET /analyze`** **`data`** now includes **`status`** (**`high`** / **`developing`** / **`insufficient`**) from stored article counts (≥10 / 5–9 / &lt;5) and top-level **`source_pool`** (category keys used for the run). Missing Angle still runs from **`compute_selected_outlets_from_db`** / seeded summaries when coverage exists; thresholds (**≥3** outlet summaries) are unchanged.
- **`backend/tests/test_news_fetcher_categorization.py`**: Covers category detection, relaxation, and pool sizes. **`test_analyze_resilience`** asserts **`status`** and **`source_pool`**.

### Reason
Broader, topic-aware sourcing improves recall on tech/finance/science queries while keeping a single credibility-ranked vetted universe; relaxation reduces empty results from over-specific queries without abandoning the allowlist.

### Verification
- **`PYTHONPATH=. python3 -m pytest backend/tests/ -q`**: pass.
