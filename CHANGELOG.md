# Changelog — Handoff (nimbus-college)

Every change to this project gets a dated entry here **and** its own git commit.
Rule going forward: no commit without a changelog entry, no changelog entry
without a commit.

## 2026-10-01 — initial prototype (built in one session)

### Scaffold
- Created repo layout: `handoff/`, `api/`, `mcp_server/`, `agent/`, `web/`, `tests/`.
- Added `requirements.txt` (fastapi, uvicorn, httpx, mcp), `Makefile`
  (`install/seed/api/mcp/ask/test/demo`), `.env.example`, `.gitignore`
  (venv, .env, sqlite files).

### Core queue engine (`handoff/`)
- `state.py`: 14 patient states, owner per state, patient-facing action phrases,
  default ETAs, pure `transition()` function with handoff side-effects.
- `db.py`: SQLite schema (events append-only log with `state_after`,
  patients projection, handoffs, alerts); WAL mode; `reset_db()`.
- `core.py`: `exec_event`/`advance`, patient projection maintenance, handoff
  open/accept lifecycle, alert resolution on accept, `resolve_handoff` rescue
  (incl. forced path), `refresh()` watchdog (dropped handoffs → `BLOCKED_NO_OWNER`
  + `DROPPED_HANDOFF` alert; idle-doctor → `IDLE_OWNER` alert), `next_for`,
  timeline humanization, journey `steps_for`.
- `metrics.py`: stage durations from the log, p50/p90, KPIs (median wait,
  waits-with-owner %, dropped/open handoffs, interruptions, orphan minutes),
  `bottlenecks()`, `interruptions()`.
- `seed.py`: synthetic clinic day (10 completed + 8 active patients, 11
  interruptions); hero story P12 misrouted to consultation with an unaccepted
  lab→doctor handoff, auto-flagged on seed.
- Fixes during build: added `MARK_READY` event; classification checks doc-fix
  words before report words (P12's reason contains both).

### AI layer (`handoff/ai.py`, `handoff/tools.py`)
- Provider-agnostic LLM client (OpenAI-compatible + Anthropic REST via httpx),
  tool-calling support, `json_mode` with retry, response cache.
- `classify_intake`, `explain_wait` (EN/HI), `draft_notification`, `narrate` —
  each with a deterministic fallback; `ai.status()` reports live/fallback mode.
- 11-tool registry shared by the MCP server, the API agent, and the dashboard:
  queue/patient/next/bottlenecks/missed-handoffs/interruptions/explain/advance/
  accept/notify/register.
- Fallback manager agent routes questions to tools by keyword and narrates results.
- Fix: `next_for` excludes blocked patients (flagged cases surface via alerts,
  not as routine "next up").
- Fix: fallback narrative composes the priority line from handoff fields.

### MCP server + CLI agent
- `mcp_server/server.py`: `MCPServer("handoff")` over stdio, 11 tools returning
  JSON (verified against mcp 2.2.0 API by introspection, not docs).
- `agent/demo_agent.py`: stdio MCP client, `--selftest` (list + call tools),
  LLM tool loop when a key exists, scripted tool calls + narration otherwise.

### API (`api/main.py`)
- FastAPI with background watchdog thread, permissive CORS for demo flexibility.
- Endpoints: `/api/state` (now incl. `bottlenecks` + `next_up`), patient
  detail/timeline/explain, intake, event, notify, interruption, handoff accept,
  ask, sim reset, health; `/` and `/patient` serve the UI.

### Web UI (`web/`)
- Dashboard: KPIs, 7-column board (Front desk, Flagged, Nurse, Doctor, Lab,
  Patient, Done), alerts with resolve, "Next up" per-role strip, "Where time
  goes" bottleneck bars, manager-agent chat with tool trace, walk-in intake
  with classification toast, reset demo.
- Patient page: status, wait + ETA, localized explanation, journey steps,
  timeline, EN/हिं toggle, "why am I waiting" interruption button.
- Fixes: Flagged column moved to position 2 (hero visible without scrolling);
  inline SVG favicon (killed 404); chip delegation fix.

### Tests (`tests/test_core.py`, 11 passing)
- Full consult→lab→review→notify→pickup flow; dropped-handoff detect + rescue;
  invalid transitions rejected; intake routing; longest-wait `next_for`;
  metrics/bottlenecks shape; fallback explanation; accept-via-dispatch;
  watchdog idempotency; empty-role `next_for`.

### Docs
- `plan.md`: problem verdict, focused statement, architecture, MCP contract,
  timeline, demo script, risks — plus §13 build log.
- `README.md`: 60-second demo, persona table, architecture, MCP host configs,
  API reference, layout, 5-minute demo script, troubleshooting.
- `scenario.json`: demo day as data (cast, hero beats, expected KPIs).

### Environment
- Installed superpowers plugin for opencode (global config) for methodology
  support; verified loaded (bootstrap + 15 skills + cached package).
- Python 3.13 venv, `pip install -r requirements.txt` clean.

## 2026-10-01 — review pass (post-build)
- Gap analysis vs the finalized problem statement: unified view ✓, wait-cause
  distinction ✓, next-action ownership ✓ (+UI strip), natural language ✓,
  handoff detection ✓, headline-question answering ✓.
- Added (this changelog's previous section already includes them):
  `bottlenecks`/`next_up` in `/api/state`, dashboard panels, modal
  "Log interruption", 4 regression tests, `next_for` blocked-exclusion.
- Verified: 11/11 tests, MCP selftest, API smoke, Playwright UI pass
  (board, modal, agent chat, patient EN/HI, resolve 86%→100%).

## Unreleased / next
- Exercise live-LLM paths with a real key (`make ask`, dashboard chat, HI via LLM).
- Backup demo video recording.
- Stretch only if green: rolling ETA refinement, WhatsApp/SMS webhook,
  multi-day trends, voice intake.

## 2026-10-01 — reviewer-driven hardening (pre-push review verdict: "with fixes")
- Pinned `mcp>=2.0` (code uses the v2 `MCPServer` import; 1.x floor was wrong).
- `POST /api/patients/{id}/notify` now returns HTTP 400 on failure instead of
  `{"ok": false}` with 200; dashboard checks `result.ok` before the sent toast.
- `POST /api/sim/reset` requires `{confirm: true}` plus a UI confirm dialog,
  so stray requests can't wipe the demo mid-pitch.
- `advance` tool docstring lists the full event vocabulary
  (ROUTED/CALLED/STARTED/COMPLETED/LAB_ORDERED/LAB_RESULT_READY/
  REPORT_FIX_REQUESTED/REPORT_FIXED/MARK_READY/NOTIFIED/PICKED_UP/
  HANDOFF_ACCEPTED/ESCALATION_ACCEPTED).
- Added the promised `clinic://snapshot` MCP resource (KPIs, open patients,
  alerts, handoffs, bottlenecks); verified via `read_resource`.
- `--selftest` now exercises all 11 tools on an isolated temp DB. Found and
  fixed a real bug: MCP SDK v2 stdio passes only an allowlisted env, so
  `HANDOFF_DB` never reached the server subprocess — the client now forwards it
  explicitly. (The first selftest run had polluted the demo DB with a scratch
  patient; reseeded clean afterwards.)
- `ROUTED` rejected from active/done states (re-routing still allowed between
  waiting states, e.g. fixing a misroute); `timeline()` returns the latest N
  events; open `IDLE_OWNER` alerts clear when a consultation resumes; orphaned
  `ARRIVED` patients now appear in the Front desk column.
- `provider()` requires an actual API key for live mode; intake `mode` stays
  `fallback` when the LLM call fails; `table_count` allowlisted.
- 5 new tests (16 total, all passing): routed guard, allowed re-route,
  notify-outside-pickup, idle-alert clearing, reset-confirm via TestClient.
