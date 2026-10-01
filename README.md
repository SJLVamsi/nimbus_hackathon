# Handoff — the clinic queue nobody has to explain twice

**Every wait has an owner and a reason.**

A neighborhood clinic runs reception, clinicians, and the lab on three disconnected
registries. Patients repeat their story at every desk, missed handoffs look like slow
consultations, and queue numbers never say *why* anyone is waiting.

Handoff unifies every patient flow into one event log, tracks every handoff between
roles, **detects dropped handoffs automatically**, and uses an LLM
(OpenAI / Anthropic / any OpenAI-compatible endpoint) to explain waits in plain
language. The whole system is exposed as an **MCP server**, so any agent can run
the desk: see the queue, find bottlenecks, rescue stuck patients.

> Focused problem statement: *Design a unified, agent-accessible queue that tracks
> every patient's state and every handoff across reception, clinicians, and the lab —
> so every wait has an owner and a plain-language reason, and coordination failures
> are detected before they look like slow service.*

## 60-second demo

```bash
make install        # one-time: creates .venv, installs fastapi/uvicorn/httpx/mcp
cp .env.example .env  # optional: add OPENAI_API_KEY or ANTHROPIC_API_KEY for live LLM
make demo           # seeds the clinic day + serves http://localhost:8080
```

Open the dashboard at **http://localhost:8080** and the patient page at
**http://localhost:8080/patient?pid=P12**.

The hero story is live on load: **Meera (P12)** arrived about a blood report, was
misrouted into the consultation queue, and her lab result has sat unreviewed for
~25 minutes because the lab→doctor handoff was never accepted. The board shows her
in red, an alert explains exactly why, the agent answers "where did time go?", and
one click on **Resolve handoff** turns her green and the KPIs to 100% owned.

No API key? Everything still works — the app runs in **deterministic fallback mode**
(template routing, template explanations, rules-based agent narration). The header
pill always tells you which mode you are in.

## What each persona gets

| Persona | Surface | Moment |
|---|---|---|
| Patient | `/patient?pid=…` — status, ETA, plain-language reason (EN/हिं), journey steps, "I still don't know why I'm waiting" button | Scan a QR at the desk |
| Reception / nurse / doctor | Dashboard board + patient modal with role actions (call in, order lab, mark fixed, notify…) | Run the shift |
| Manager | "Ask the manager agent" + KPIs + before/after flow | Answer *where is waiting time accumulating, and whose work is interrupted?* |

## How it works

```
 [reception sim] [clinician sim] [lab sim]      ← three registries, one log
        \              |              /
         v             v             v
   FastAPI core: append-only event log + state machine + metrics (SQLite)
         |                    |
    REST (dashboard)     MCP server (11 tools)
         |                    |
    web dashboard       LLM agent loop (dashboard chat + CLI)
```

- **Event log as source of truth.** Every arrival, routing, call, lab order, result,
  notification, and *interruption* (a patient asking "why am I waiting?") is an event.
- **Explicit state machine.** 14 patient states, each with an owner. Illegal
  transitions are rejected with a human-readable error.
- **Watchdog.** An open handoff older than its SLA
  (lab→doctor: 5 min, doctor→lab: 10 min, …) is marked *dropped* and the patient is
  flagged *no owner*. A second detector flags patients waiting on a doctor while no
  consultation is in progress. Runs continuously in the API and on every tool call.
- **Metrics.** Stage p50/p90, current median wait, waits-with-owner %, dropped
  handoffs, orphan minutes, interruptions per role — all computed live from the log.
- **AI where language lives.** Intake classification from free text, patient-wait
  explanations with ETA, pickup notification drafts, and a tool-calling manager
  agent. Every AI path has a deterministic fallback, plus response caching.

## MCP server

```bash
make mcp            # stdio transport (default)
```

11 tools: `get_queue`, `get_patient`, `next_for`, `list_bottlenecks`,
`list_missed_handoffs`, `staff_interruptions`, `explain_wait`, `advance`,
`accept_handoff`, `notify_patient`, `register_patient`. Mutations change the live
queue, so the agent can actually run the desk.

Test it without an LLM:

```bash
.venv/bin/python -m agent.demo_agent --selftest
.venv/bin/python -m agent.demo_agent "Where did waiting time go today?"
```

Add it to any MCP host. For opencode (`~/.config/opencode/opencode.json`),
run from the repo root:

```json
{
  "mcp": {
    "handoff": {
      "type": "local",
      "command": ["/absolute/path/to/nimbus-college/.venv/bin/python", "-m", "mcp_server.server"],
      "enabled": true
    }
  }
}
```

Claude Desktop and Cursor use the same command/args shape in their MCP config.

## API reference

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/`, `/patient?pid=…` | Staff dashboard, patient status page |
| GET | `/api/state` | KPIs + patients + alerts + handoffs + interruptions |
| GET | `/api/patients/{id}` | Patient, timeline, journey steps |
| GET | `/api/patients/{id}/explain?language=en\|hi` | LLM wait explanation + ETA |
| POST | `/api/intake` | `{name, reason}` → classified + routed patient |
| POST | `/api/patients/{id}/event` | Workflow events (`CALLED`, `LAB_ORDERED`, …) |
| POST | `/api/patients/{id}/notify` | Draft + send pickup notification |
| POST | `/api/patients/{id}/interruption` | Log a "why am I waiting?" moment |
| POST | `/api/handoffs/{hid}/accept` | Resolve a (dropped) handoff |
| POST | `/api/ask` | Manager agent over MCP-style tools |
| POST | `/api/sim/reset` | Reseed the demo day |

## Layout

```
nimbus-college/
  plan.md       # the overnight build plan
  handoff/      core.py state.py db.py metrics.py ai.py tools.py seed.py
  api/          main.py                     # FastAPI + static serving
  mcp_server/   server.py                   # MCPServer, 11 tools
  agent/        demo_agent.py               # CLI MCP client + agent loop
  web/          index.html app.js style.css patient.html patient.js patient.css
  tests/        test_core.py
  scenario.json # the demo day as data
  plan.md       # the overnight build plan
```

## Demo script (5 minutes)

1. **The mess (0:40).** Three columns, one question: "who is P12 waiting on?" Only the red card answers.
2. **One queue (1:00).** One board, owners, exact wait times. P12's story in her timeline.
3. **Patient view (1:00).** Open `/patient?pid=P12` on a phone. Plain English, ETA, Hindi toggle.
4. **Desk view (1:00).** Reception asks the agent "what should reception do next?" — grounded tool calls.
5. **Manager view (1:00).** "Where did waiting time go today?" Bottleneck chart in words: waiting-for-doctor, 11 interruptions, 1 dropped handoff.
6. **Resolution (0:30).** Resolve the handoff live: 86% → 100% owned, orphan minutes → 0. Close: *every wait has an owner and a reason.*

## Privacy

Synthetic data only — no real patients, no real health data. Names, reasons and
timings in `seed.py` are invented for the demo.

## Troubleshooting

- `make demo` reseed gives a fresh relative-to-now day; use it before every pitch.
- If the LLM is unreachable mid-demo, the header pill shows fallback mode and all
  features keep working; pre-ask once to warm the response cache.
- Record a backup walkthrough video — hackathon Wi-Fi owes you nothing.
