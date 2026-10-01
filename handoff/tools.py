from __future__ import annotations

import json

from . import ai, core, metrics
from .db import tx
from .state import DEFAULT_ETA

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_queue",
            "description": "List patients in the clinic queue with their state, owner and wait time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Filter by owner role",
                        "enum": ["reception", "nurse", "clinician", "lab", "patient"],
                    },
                    "status": {
                        "type": "string",
                        "enum": ["waiting", "active", "blocked", "open", "done", "all"],
                        "description": "Filter by status. Default waiting.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_patient",
            "description": "Full status of one patient including state, owner, waiting time and handoffs.",
            "parameters": {
                "type": "object",
                "properties": {"patient_id": {"type": "string"}},
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "next_for",
            "description": "Who a given role should serve next, and why.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["reception", "nurse", "clinician", "lab"]}
                },
                "required": ["role"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_bottlenecks",
            "description": "Where waiting time is accumulating, per state, with p50/p90.",
            "parameters": {
                "type": "object",
                "properties": {"window_minutes": {"type": "integer", "description": "Lookback window. Default 240."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_missed_handoffs",
            "description": "Handoffs that were never accepted in time (dropped), with patient details.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "staff_interruptions",
            "description": "Count and list interruptions where patients asked staff why they are waiting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["reception", "nurse", "clinician", "lab"]},
                    "window_minutes": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_wait",
            "description": "Plain-language explanation of a patient's wait, including ETA, for patient-facing display.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "language": {"type": "string", "enum": ["en", "hi"]},
                },
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "advance",
            "description": "Record a workflow event for a patient (CALLED, STARTED, COMPLETED, LAB_ORDERED, LAB_RESULT_READY, REPORT_FIX_REQUESTED, REPORT_FIXED, PICKED_UP).",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "event_type": {"type": "string"},
                    "actor": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["patient_id", "event_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_handoff",
            "description": "Accept/rescue a dropped or open handoff, clearing the patient's blocked state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "handoff_id": {"type": "integer"},
                    "by": {"type": "string"},
                },
                "required": ["handoff_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_patient",
            "description": "Draft and send a pickup notification to a patient whose report is ready.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {"type": "string"},
                    "channel": {"type": "string", "enum": ["sms", "whatsapp", "call"]},
                },
                "required": ["patient_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "register_patient",
            "description": "Register a new patient from a free-text reason, classifying the right queue automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "reason"],
            },
        },
    },
]


def patient_facts(patient_id, language="en"):
    patient = core.get_patient(patient_id)
    state = patient["prev_state"] if patient["blocked"] and patient["prev_state"] else patient["state"]
    eta = metrics.stage_median(state)
    if eta is None:
        eta = DEFAULT_ETA.get(state, 10)
    facts = {
        "patient_id": patient["id"],
        "name": patient["name"],
        "state": state,
        "state_label": patient["state_label"],
        "owner": patient["owner"],
        "owner_label": patient["owner_label"],
        "wait_minutes": patient["wait_minutes"],
        "eta_minutes": int(round(eta)),
        "blocked": patient["blocked"],
        "block_reason": patient["block_reason"],
        "action": patient["action"],
        "last_events": [event["text"] for event in core.timeline(patient_id)[-4:]],
        "language": language,
    }
    return patient, facts


def dispatch(name, args=None):
    args = args or {}
    core.refresh()
    try:
        return _dispatch(name, args)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _dispatch(name, args):
    if name == "get_queue":
        patients = core.get_queue(role=args.get("role"), status=args.get("status") or "waiting")
        return {"ok": True, "count": len(patients), "patients": patients}
    if name == "get_patient":
        patient = core.get_patient(args["patient_id"])
        patient["timeline"] = core.timeline(patient["id"], limit=30)
        patient["steps"] = core.steps_for(patient)
        return {"ok": True, "patient": patient}
    if name == "next_for":
        return {"ok": True, **core.next_for(args["role"])}
    if name == "list_bottlenecks":
        window = int(args.get("window_minutes") or 240)
        return {"ok": True, "window_minutes": window, "bottlenecks": metrics.bottlenecks(window)}
    if name == "list_missed_handoffs":
        handoffs = core.list_handoffs(status="dropped")
        enriched = []
        for handoff in handoffs:
            try:
                patient = core.get_patient(handoff["patient_id"])
                name_value = patient["name"]
                wait = patient["wait_minutes"]
            except KeyError:
                name_value = "Unknown"
                wait = None
            enriched.append(
                {
                    "handoff_id": handoff["id"],
                    "patient_id": handoff["patient_id"],
                    "patient_name": name_value,
                    "item": handoff["item"],
                    "from_role": handoff["from_role"],
                    "to_role": handoff["to_role"],
                    "initiated_at": handoff["initiated_at"],
                    "wait_minutes": wait,
                }
            )
        return {"ok": True, "count": len(enriched), "missed_handoffs": enriched}
    if name == "staff_interruptions":
        window = int(args.get("window_minutes") or 24 * 60)
        items = metrics.interruptions(window_minutes=window, role=args.get("role"))
        return {"ok": True, "count": len(items), "interruptions": items[:20]}
    if name == "explain_wait":
        patient, facts = patient_facts(args["patient_id"], args.get("language") or "en")
        explanation = ai.explain_wait(facts, language=args.get("language") or "en")
        return {
            "ok": True,
            "patient_id": patient["id"],
            "steps": core.steps_for(patient),
            "explanation": explanation,
        }
    if name == "advance":
        payload = {}
        if args.get("notes"):
            payload["note"] = args["notes"]
        patient = core.advance(
            args["patient_id"], args["event_type"], actor=args.get("actor"), payload=payload
        )
        return {"ok": True, "patient": patient}
    if name == "accept_handoff":
        return core.resolve_handoff(int(args["handoff_id"]), by=args.get("by") or "clinician")
    if name == "notify_patient":
        patient = core.get_patient(args["patient_id"])
        draft = ai.draft_notification(patient, channel=args.get("channel") or "sms")
        updated = core.notify_patient(
            patient["id"], message=draft["message"], channel=args.get("channel") or "sms"
        )
        return {
            "ok": True,
            "channel": args.get("channel") or "sms",
            "message": draft["message"],
            "mode": draft["mode"],
            "patient": updated,
        }
    if name == "register_patient":
        return register_patient(args["name"], args["reason"])
    return {"ok": False, "error": f"Unknown tool {name}"}


def next_patient_id():
    with tx() as conn:
        count = conn.execute("SELECT COUNT(*) n FROM patients").fetchone()["n"]
    candidate = count + 1
    while True:
        pid = f"P{candidate:02d}"
        with tx() as conn:
            exists = conn.execute("SELECT 1 FROM patients WHERE id=?", (pid,)).fetchone()
        if not exists:
            return pid
        candidate += 1


def register_patient(name, reason):
    classification = ai.classify_intake(reason, name=name)
    pid = next_patient_id()
    core.create_patient(
        pid,
        name,
        ptype=classification["patient_type"],
        reason=reason,
        meta={"intake": classification},
    )
    core.advance(
        pid,
        "ROUTED",
        actor="reception",
        payload={"queue": classification["queue"], "summary": classification.get("summary", "")},
    )
    return {
        "ok": True,
        "patient_id": pid,
        "classification": classification,
        "patient": core.get_patient(pid),
    }


def _summarize(result):
    if not isinstance(result, dict):
        return str(result)[:120]
    if not result.get("ok", True):
        return f"error: {result.get('error')}"
    for key in ("count", "patient", "bottlenecks", "missed_handoffs", "interruptions", "answer"):
        if key in result:
            value = result[key]
            if isinstance(value, list):
                return f"{len(value)} item(s)"
            if isinstance(value, dict) and "id" in value:
                return f"{value.get('id')} ({value.get('state_label')})"
            text = str(value)
            return text[:160]
    return "ok"


def ask(question, max_steps=6):
    core.refresh()
    trace = []
    facts = {"kpis": metrics.kpis()}
    if ai.provider() == "fallback":
        return _fallback_ask(question, trace, facts)
    messages = [
        {
            "role": "system",
            "content": (
                "You are the operations assistant for a neighborhood clinic called Handoff. "
                "Ground every number in tool results. Use get_patient for individual patients, "
                "list_bottlenecks for where time accumulates, list_missed_handoffs for dropped handoffs, "
                "staff_interruptions for interrupted work, and next_for for priorities. "
                "Be concise and recommend one concrete action."
            ),
        },
        {"role": "user", "content": question},
    ]
    for _ in range(max_steps):
        response = ai.chat(messages, tools=TOOL_SCHEMAS)
        if response.get("error"):
            break
        tool_calls = response.get("tool_calls") or []
        if not tool_calls:
            if response.get("content"):
                return {"answer": response["content"].strip(), "trace": trace, "mode": "llm"}
            break
        messages.append(
            {"role": "assistant", "content": response.get("content"), "tool_calls": tool_calls}
        )
        for call in tool_calls:
            result = dispatch(call["name"], call.get("args") or {})
            trace.append(
                {
                    "tool": call["name"],
                    "args": call.get("args") or {},
                    "summary": _summarize(result),
                }
            )
            if call["name"] == "list_missed_handoffs" and result.get("missed_handoffs"):
                facts["missed_handoffs"] = result["missed_handoffs"]
            if call["name"] == "list_bottlenecks":
                facts["bottlenecks"] = result.get("bottlenecks")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": json.dumps(result, default=str)[:6000],
                }
            )
    return _fallback_ask(question, trace, facts)


def _fallback_ask(question, trace, facts):
    text = (question or "").lower()

    def run(name, args=None):
        result = dispatch(name, args or {})
        trace.append({"tool": name, "args": args or {}, "summary": _summarize(result)})
        return result

    bottlenecks = run("list_bottlenecks", {"window_minutes": 240})
    facts["bottlenecks"] = bottlenecks.get("bottlenecks") or []
    missed = run("list_missed_handoffs")
    facts["missed_handoffs"] = missed.get("missed_handoffs") or []
    if any(word in text for word in ("who", "next", "serve", "priority")):
        role = "reception"
        for candidate in ("clinician", "lab", "nurse", "reception"):
            if candidate in text:
                role = candidate
                break
        run("next_for", {"role": role})
    run("staff_interruptions", {"window_minutes": 24 * 60})
    facts["kpis"] = metrics.kpis()
    narrative = ai.narrate(question, trace, facts)
    return {"answer": narrative["answer"], "trace": trace, "mode": narrative["mode"]}
