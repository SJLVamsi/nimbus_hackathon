# Clinic Queue Prototype — Overnight Build Plan

## 0. Verdict on the problem statement

**It's strong, but there is a trap.**

What's good:
- Real, specific, and relatable. Three personas (patient, staff, manager) each have a concrete symptom.
- The symptoms all point at one root cause: **three disconnected registries and no shared state**, so patients act as the integration layer and handoffs fail silently.
- It maps naturally onto MCP + LLMs: the queue becomes a set of agent-callable tools, and LLMs turn state into language humans understand.

The trap:
- The obvious build is "a nicer queue display." That is **not** the problem. A queue board that still can't say *why* someone waits solves nothing.
- The differentiator must be the **explanation + handoff-detection layer**: every wait has an owner and a reason, and dropped handoffs are detected before they become "slow consultations."

One-line focused problem statement (put this on the slide):

> **Design a unified, agent-accessible queue that tracks every patient's state and every handoff across reception, clinicians, and the lab — so every wait has an owner and a plain-language reason, and coordination failures are detected before they look like slow service.**

Success metrics we choose (judges love this):
- % of waits with a named owner + reason (target: 100%, baseline: ~0%)
- Handoff drop rate and median handoff latency
- Staff interruptions per hour
- p50/p90 wait time per stage

---

## 1. What NOT to build (scope discipline)

- No auth, no real hospital integrations, no mobile apps, no payments.
- No ML model training. "AI" here means LLM + MCP, used where language/coordination actually occurs.
- No chatbot that just repeats the queue board. Every AI output must **reduce a specific wait or interruption**.
- Synthetic data only. Fake names, no real patient info.

---

## 2. Solution concept

Working name: **Handoff** (alt: Unqueue, QFlow)

Four building blocks:

1. **One event log** = single source of truth. Three adapters simulate reception register, clinician list, and lab spreadsheet feeding the same log.
2. **Explicit patient state machine** where every waiting state names an owner (person or role) and a required next event.
3. **Handoff detector**: if state says "waiting on X" but X's side emitted a done/report-ready event with no accept event (or SLA exceeded), flag `BLOCKED_NO_OWNER` and escalate.
4. **Three AI surfaces over one MCP server**:
   - Patient (QR page): "why am I waiting + ETA" in plain language, multilingual.
   - Reception/clinician: "who needs me next, and why."
   - Manager: "where did waiting time go today" via an LLM agent calling MCP tools.

Tagline for the pitch: **"Every wait has an owner and a reason."**

---

## 3. Domain model

### States

| State | Owner | Exits on |
|---|---|---|
| ARRIVED | reception | routed |
| WAITING_RECEPTION | reception | docs cleared / queued |
| WAITING_TRIAGE | nurse | called |
| IN_TRIAGE | nurse | done |
| WAITING_CONSULT | clinician | called |
| IN_CONSULT | clinician | order/complete |
| WAITING_LAB_COLLECTION | lab tech | sample taken |
| LAB_PROCESSING | lab system | result ready |
| WAITING_REPORT_REVIEW | clinician | report accepted |
| WAITING_DOC_FIX | reception | fixed |
| READY_FOR_PICKUP | reception | notified |
| NOTIFIED | reception | picked up |
| DONE | — | — |
| BLOCKED_NO_OWNER | system | escalation accepted |

### Handoff matrix

- Reception → Clinician (consult)
- Clinician → Lab (order)
- Lab → Clinician (result ready, needs review)
- Clinician → Reception (doc fix / pickup)
- Reception → Patient (notify)

### Events (append-only)

```
PATIENT_ARRIVED { patient_id, reason_text, type: appt|walkin|return|report }
ROUTED { queue }
CALLED { by }
STARTED { by }
COMPLETED { by }
HANDOFF_INITIATED { from, to, item }
HANDOFF_ACCEPTED { by }
LAB_ORDERED { test }
LAB_RESULT_READY { report_id }
REPORT_FIX_REQUESTED { note }
REPORT_FIXED { by }
NOTIFIED { channel }
PICKED_UP
INTERRUPTION { at_role, patient_id, note }   # someone asked "why am I waiting"
```

Every wait is derived as "time since last event, state owner = X". Orphan detection = upstream `COMPLETED`/`RESULT_READY` exists but no `HANDOFF_ACCEPTED`.

---

## 4. AI usage — explicit, necessary, not decorative

| Feature | Tech | Why AI is required | Fallback if API fails |
|---|---|---|---|
| Intake classification: free-text reason → route (consult / doc fix / report pickup) | LLM structured output | Patients write/say messy things; misrouting causes the exact delays described | keyword rules |
| Wait explanation: event log → 1–2 sentence patient message + ETA | LLM | Translating system state into human language, multilingual | template strings |
| Missed-handoff detection | **Hybrid**: rules detect mismatch; LLM classifies ambiguous free-text notes + drafts escalation | Deterministic detection + language understanding of staff notes | rules only |
| Manager analytics agent | MCP tools + LLM tool-calling | Multi-step questions over live state ("why is the lab backed up?") | fixed dashboard |
| Notification drafts (SMS/WhatsApp copy) | LLM | Tone + language per patient | template |

The hybrid handoff detector is the hero AI feature: **AI that finds coordination failures**, not a chatbot.

---

## 5. Architecture

```
 [Reception sim]   [Clinician sim]   [Lab sim]
        \               |              /
         v              v             v
        FastAPI core: event log + state machine + metrics
             |            |            |
         REST/SSE      MCP server    SQLite
             |            |
      Web UI (3 views)    LLM agent (tool-calling over MCP)
             |
      Patient QR page
```

Stack (fast to build overnight):
- **Python 3.11, FastAPI, SQLite, uvicorn**
- **MCP**: official Python SDK, stdio transport (streamable HTTP if time permits)
- **LLM**: one provider via env `LLM_API_KEY` (OpenAI/Anthropic/Gemini), structured outputs + tool calling, response caching
- **Frontend**: Next.js + Tailwind + Recharts (or Streamlit if team < 3 — faster, less impressive)
- **Simulator**: `scenario.json` replayed by a script; event injection endpoint for live demo

Repo layout:

```
clinic-queue/
  server/     app.py  core.py  db.py  metrics.py  adapters/  seed.py
  mcp_server/ server.py
  web/        (dashboard + patient QR page)
  agent/      demo_agent.py
  scenario.json
  README.md
```

---

## 6. MCP tool contract (freeze this first, then code)

Tools:
- `get_queue(role?, status?)` — list patients with state + owner
- `get_patient(patient_id)` — state, timeline, current owner
- `next_for(role)` — who to serve next, with reason
- `list_bottlenecks(window_minutes)` — p50/p90 per state, orphan minutes
- `list_missed_handoffs()` — concrete dropped handoffs
- `staff_interruptions(role, window)` — count + notes
- `explain_wait(patient_id)` — LLM explanation (facts + natural language)
- `advance(patient_id, event_type, notes?)` — mutation; lets the agent actually run the desk
- `notify_patient(patient_id, channel?)` — LLM drafts + sends (mocked)

Resource:
- `clinic://snapshot` — full current state for agent context

Test with **MCP Inspector** before wiring any UI. Mutation tools are what make the MCP usage meaningful.

---

## 7. Build timeline (relative hours; sample 20:00 start)

| Clock | Block | Owner |
|---|---|---|
| H+0:00–0:45 | Scope lock, repo setup, freeze event schema + MCP tool signatures | all |
| H+0:45–2:30 | Core: event log, state machine, metrics, simulator | A |
| H+0:45–2:30 | Frontend shell: unified queue board + patient timeline | C |
| H+2:30–4:00 | MCP server + Inspector tests | B |
| H+4:00–5:30 | Agent loop: manager Q&A over MCP | B |
| H+4:30–6:30 | LLM: intake classifier, wait explainer, notify drafts | A/B |
| H+6:00–8:00 | Patient QR page + desk "next" view | C |
| H+8:00–9:30 | Integrate + seed the demo-day scenario | all |
| H+9:30–10:30 | Before/after metrics dashboard | C |
| H+10:30–11:30 | Freeze, rehearse twice, record backup demo video | all |
| H+11:30–12:00 | README, cleanup, submit | all |

Team of 3 assumed. Adapt if different.

---

## 8. Demo script (5 minutes)

1. **0:00–0:40 — The mess.** Three tabs: reception register, clinician list, lab sheet. Ask the judges: "Who is patient 12 waiting on?" Nobody can answer. That's the actual product.
2. **0:40–1:30 — One queue.** Unified board appears. Patient 12's story: arrived with report query, misrouted into consult queue, sat 40 min.
3. **1:30–2:30 — Patient view.** Scan QR: "You're waiting for Dr. Rao to review your lab report. About 12 minutes. No action needed from you." Toggle local language.
4. **2:30–3:30 — Desk view.** Receptionist asks the agent: "Who should I process next?" Agent calls `next_for("reception")` + `list_missed_handoffs()`, processes a doc fix.
5. **3:30–4:30 — Manager view.** "Where did waiting time go today?" Agent calls tools, returns chart + narrative: "42% of wait time is report review; Dr. Rao interrupted 11 times; 3 handoffs dropped at lab→clinician."
6. **4:30–5:00 — Before/after.** Median wait, % waits with an owner, interruptions. Close: "Every wait has an owner and a reason."

---

## 9. Risks & fallbacks

| Risk | Mitigation |
|---|---|
| LLM API/network flaky during demo | Cache responses; deterministic fallbacks; record backup video by H+10:30 |
| Scope creep | The cut list in §1; one hero flow (report → clinician review) |
| MCP client complexity | stdio server tested with Inspector early; own thin agent loop, don't depend on Desktop apps |
| Frontend eats the night | Streamlit fallback; backend + MCP is the graded core |
| Demo machine issues | `make demo` one-command startup; seed data committed |

---

## 10. Stretch goals (only if everything is green)

- ETA model: rolling median per state (not ML, just honest math)
- Voice intake via Whisper for walk-ins
- WhatsApp/SMS webhook (mocked)
- Multi-day trend view for the manager

---

## 11. Judging alignment

- **Problem clarity**: focused statement in §0 with measurable metrics.
- **Technical depth**: event-sourced core, real MCP server with mutations, hybrid AI detector.
- **AI used meaningfully**: explanation + routing + coordination-failure detection, not chatbot garnish.
- **Demo**: a story with real numbers, before/after.
- **Impact**: fewer interruptions, fewer dropped handoffs, transparent waits.

Pitch outline: problem (40s) → live mess demo (40s) → how it works (60s) → AI/MCP in action (120s) → metrics + close (60s).

---

## 12. Assumptions & open questions

- Team size: assumed 3. Adjust the timeline owners if different.
- Which LLM API keys are available? (pick one and freeze it)
- UI choice: Next.js vs Streamlit (depends on team skills/time)
- Patient message language(s) for the multilingual demo?
- Confirm: synthetic data only.

---

## 13. Build log (2026-10-01) — what was actually built

Status: **complete and verified.** Every section above shipped; deviations noted below.

### Decisions taken during the build
- **Frontend: zero-dependency static HTML/JS/CSS served by FastAPI** instead of Next.js/Streamlit.
  Rationale: no build step, works fully offline at the venue, and the demo needs
  reliability more than framework polish. Decision recorded; revisit only if the
  team has spare hours and wants component structure.
- **MCP SDK v2** (`from mcp.server.mcpserver import MCPServer`). The old
  `FastMCP` import path no longer exists in mcp 2.x — verified by introspecting
  the installed package before writing the server.
- **SLA tuning:** nurse→clinician 30 min (ordinary consult waits are not drops),
  lab→clinician 5 min (this is the hero detector), everything else 8–15 min.
- **Fallback-first AI:** every LLM feature has a deterministic fallback and the UI
  header always shows live vs fallback mode, because hackathon Wi-Fi and missing
  keys must never break the demo.

### Added beyond the original plan
- `MARK_READY` event (report found at reception → ready for pickup).
- `ESCALATION_ACCEPTED` path and `resolve_handoff` rescue flow.
- `IDLE_OWNER` detector (patient waits on a doctor while no consultation runs).
- "Next up" per-role strip and "Where time goes" bottleneck bars on the dashboard.
- "Log interruption" button in the patient modal (staff side of the loop).
- `scenario.json` (demo day as data), `CHANGELOG.md` (this log's sibling).
- 11 unit tests (was: "write tests if time permits").

### Verification performed
- 11/11 unit tests pass (`make test`).
- MCP selftest: server connects, all 11 tools callable (`agent/demo_agent.py --selftest`).
- API smoke: `/api/state`, `/api/patients/P12/explain`, `/api/ask`, `/api/intake`.
- Full UI pass in Chromium via Playwright: board, patient modal, agent chat with
  tool trace, patient page EN + Hindi, resolve flow (86% → 100% owned, orphan 21m → 0).
- Console clean (only a favicon 404, fixed with an inline SVG icon).

### Open questions — resolved
- LLM provider: **both** OpenAI-compatible and Anthropic supported; Anthropic wins
  if both keys exist; everything works with neither.
- UI: static web (see above). Languages: English + Hindi patient messages.
- Synthetic data only: confirmed, `seed.py` + `scenario.json`.

### Known limits (honest, say them on stage if asked)
- Live-LLM tool-calling paths are implemented but **never exercised** (no key in
  the build environment). Add a key and re-run `make ask` before finals.
- Single user, no auth, reseed wipes the day — correct for a prototype.
- ETAs are rolling medians with static defaults, not a learned model.
