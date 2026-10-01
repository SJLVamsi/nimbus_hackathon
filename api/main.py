from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from handoff import ai, config, core, db, metrics, tools
from handoff.seed import seed_demo

WEB_DIR = config.ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if db.table_count("patients") == 0:
        seed_demo()
    stop = threading.Event()

    def watchdog():
        while not stop.wait(5):
            try:
                core.refresh()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    yield
    stop.set()


app = FastAPI(title="Handoff", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.exception_handler(KeyError)
async def not_found_handler(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=404)


@app.exception_handler(ValueError)
async def bad_request_handler(request, exc):
    return JSONResponse({"error": str(exc)}, status_code=400)


class IntakeBody(BaseModel):
    name: str
    reason: str


class EventBody(BaseModel):
    event_type: str
    actor: str | None = None
    notes: str | None = None
    payload: dict | None = None


class NotifyBody(BaseModel):
    channel: str = "sms"


class AcceptBody(BaseModel):
    by: str = "clinician"


class AskBody(BaseModel):
    question: str


class ResetBody(BaseModel):
    confirm: bool = False


class InterruptionBody(BaseModel):
    at_role: str = "reception"
    note: str = ""


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/patient")
async def patient_page():
    return FileResponse(WEB_DIR / "patient.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "llm": ai.status(), "db": str(config.DB_PATH)}


@app.get("/api/state")
async def state():
    core.refresh()
    patients = core.get_queue(status="all")
    return {
        "kpis": metrics.kpis(),
        "patients": patients,
        "alerts": core.get_alerts(status="open"),
        "handoffs": [h for h in core.list_handoffs() if h["status"] in ("open", "dropped")],
        "interruptions": metrics.interruptions(window_minutes=24 * 60)[:8],
        "bottlenecks": metrics.bottlenecks(240),
        "next_up": {role: core.next_for(role) for role in ("reception", "nurse", "clinician", "lab")},
        "llm": ai.status(),
    }


@app.get("/api/patients/{patient_id}")
async def patient(patient_id: str):
    data = core.get_patient(patient_id)
    data["timeline"] = core.timeline(patient_id, limit=40)
    data["steps"] = core.steps_for(data)
    return data


@app.get("/api/patients/{patient_id}/timeline")
async def patient_timeline(patient_id: str):
    return {"timeline": core.timeline(patient_id, limit=40)}


@app.get("/api/patients/{patient_id}/explain")
async def explain(patient_id: str, language: str = Query("en")):
    core.refresh()
    data = core.get_patient(patient_id)
    data["timeline"] = core.timeline(patient_id, limit=20)
    data["steps"] = core.steps_for(data)
    _, facts = tools.patient_facts(patient_id, language=language)
    explanation = ai.explain_wait(facts, language=language)
    return {
        "patient": data,
        "steps": data["steps"],
        "explanation": explanation,
        "facts": facts,
    }


@app.post("/api/patients/{patient_id}/event")
async def patient_event(patient_id: str, body: EventBody):
    payload = dict(body.payload or {})
    if body.notes:
        payload["note"] = body.notes
    return core.advance(patient_id, body.event_type, actor=body.actor, payload=payload)


@app.post("/api/patients/{patient_id}/notify")
async def notify(patient_id: str, body: NotifyBody):
    result = tools.dispatch("notify_patient", {"patient_id": patient_id, "channel": body.channel})
    if not result.get("ok"):
        raise ValueError(result.get("error") or "Notify failed")
    return result


@app.post("/api/patients/{patient_id}/interruption")
async def interruption(patient_id: str, body: InterruptionBody):
    core.log_interruption(patient_id, at_role=body.at_role, note=body.note)
    return {"ok": True}


@app.post("/api/handoffs/{handoff_id}/accept")
async def accept_handoff(handoff_id: int, body: AcceptBody):
    return core.resolve_handoff(handoff_id, by=body.by)


@app.post("/api/intake")
async def intake(body: IntakeBody):
    return tools.register_patient(body.name, body.reason)


@app.post("/api/ask")
async def ask(body: AskBody):
    return tools.ask(body.question)


@app.post("/api/sim/reset")
async def reset(body: ResetBody):
    if not body.confirm:
        raise ValueError("Reset requires {confirm: true} — it wipes the clinic day")
    return seed_demo()
