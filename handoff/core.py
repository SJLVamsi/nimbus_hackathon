from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import config
from .db import now_iso, parse_ts, tx
from .state import (
    ACTIVE_STATES,
    ARRIVED,
    BLOCKED,
    DONE,
    IN_CONSULT,
    IN_TRIAGE,
    LAB_PROCESSING,
    NOTIFIED,
    OWNER_LABEL,
    PATIENT_ACTION,
    READY_FOR_PICKUP,
    STATE_LABEL,
    STATE_OWNER,
    WAITING_CONSULT,
    WAITING_DOC_FIX,
    WAITING_LAB_COLLECTION,
    WAITING_RECEPTION,
    WAITING_REPORT_REVIEW,
    WAITING_STATES,
    WAITING_TRIAGE,
    transition,
)


def _patient_dict(row, now=None):
    now = now or datetime.now()
    entered = parse_ts(row["entered_state_at"])
    wait = max(0.0, (now - entered).total_seconds() / 60)
    state = row["state"]
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "reason": row["reason_text"],
        "state": state,
        "state_label": STATE_LABEL.get(state, state),
        "owner": row["owner"],
        "owner_label": OWNER_LABEL.get(row["owner"], row["owner"]),
        "wait_minutes": round(wait, 1),
        "entered_state_at": row["entered_state_at"],
        "blocked": bool(row["blocked"]),
        "block_reason": row["block_reason"],
        "blocked_since": row["blocked_since"],
        "prev_state": row["prev_state"],
        "action": PATIENT_ACTION.get(state, "waiting"),
    }


def create_patient(patient_id, name, ptype="walkin", reason="", ts=None, meta=None):
    ts = ts or now_iso()
    with tx() as conn:
        conn.execute(
            "INSERT INTO patients(id,name,type,reason_text,state,owner,entered_state_at,created_at,meta)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (patient_id, name, ptype, reason, ARRIVED, "reception", ts, ts, json.dumps(meta or {})),
        )
        conn.execute(
            "INSERT INTO events(ts,patient_id,type,actor,payload,state_after) VALUES(?,?,?,?,?,?)",
            (
                ts,
                patient_id,
                "PATIENT_ARRIVED",
                "reception",
                json.dumps({"reason": reason, "patient_type": ptype}),
                ARRIVED,
            ),
        )
    return get_patient(patient_id)


def _insert_event(conn, ts, patient_id, etype, actor, payload, state_after):
    conn.execute(
        "INSERT INTO events(ts,patient_id,type,actor,payload,state_after) VALUES(?,?,?,?,?,?)",
        (ts, patient_id, etype, actor, json.dumps(payload or {}), state_after),
    )


def _open_handoff(conn, patient_id, from_role, to_role, item, ts):
    sla = config.SLA_MINUTES.get((from_role, to_role), config.DEFAULT_SLA_MINUTES)
    cur = conn.execute(
        "INSERT INTO handoffs(patient_id,from_role,to_role,item,initiated_at,status,sla_minutes)"
        " VALUES(?,?,?,?,?, 'open', ?)",
        (patient_id, from_role, to_role, item, ts, sla),
    )
    return cur.lastrowid


def _accept_handoff(conn, patient_id, ts, actor):
    row = conn.execute(
        "SELECT id FROM handoffs WHERE patient_id=? AND status IN ('open','dropped') ORDER BY id DESC LIMIT 1",
        (patient_id,),
    ).fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE handoffs SET status='accepted', accepted_at=?, accepted_by=? WHERE id=?",
        (ts, actor or "staff", row["id"]),
    )
    conn.execute(
        "UPDATE alerts SET status='resolved', resolved_at=? WHERE patient_id=? AND kind='DROPPED_HANDOFF' AND status!='resolved'",
        (ts, patient_id),
    )
    conn.execute(
        "UPDATE alerts SET status='resolved', resolved_at=? WHERE patient_id=? AND kind='IDLE_OWNER' AND status!='resolved'",
        (ts, patient_id),
    )
    return row["id"]


def exec_event(patient_id, etype, actor=None, payload=None, ts=None):
    payload = dict(payload or {})
    ts = ts or now_iso()
    with tx() as conn:
        row = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        if not row:
            raise KeyError(f"Unknown patient {patient_id}")
        if row["state"] == DONE and etype not in ("INTERRUPTION",):
            raise ValueError("Patient is already completed")

        state = row["state"]
        blocked = bool(row["blocked"])
        base_state = row["prev_state"] if blocked and row["prev_state"] else state

        if etype == "INTERRUPTION":
            new_state, effects, resulting_state = state, [], state
        elif etype == "ESCALATION_ACCEPTED":
            if not blocked:
                raise ValueError("Patient is not blocked")
            new_state, effects, resulting_state = base_state, [], base_state
        else:
            result = transition(base_state, etype, payload)
            if result is None:
                raise ValueError(
                    f"Cannot apply {etype} while patient is in {STATE_LABEL.get(state, state)}"
                )
            new_state, effects = result
            resulting_state = new_state

        for effect in effects:
            if effect[0] == "open":
                _, to_role, item = effect
                from_role = STATE_OWNER.get(base_state)
                _open_handoff(conn, patient_id, from_role, to_role, item, ts)
            elif effect[0] == "accept":
                _accept_handoff(conn, patient_id, ts, actor or STATE_OWNER.get(new_state))

        if etype == "INTERRUPTION":
            pass
        elif blocked:
            if new_state != base_state:
                conn.execute(
                    "UPDATE patients SET state=?, owner=?, entered_state_at=?, blocked=0, block_reason=NULL,"
                    " blocked_since=NULL, prev_state=NULL, prev_owner=NULL WHERE id=?",
                    (new_state, STATE_OWNER.get(new_state), ts, patient_id),
                )
            else:
                conn.execute(
                    "UPDATE patients SET state=?, owner=?, blocked=0, block_reason=NULL, blocked_since=NULL,"
                    " prev_state=NULL, prev_owner=NULL WHERE id=?",
                    (new_state, STATE_OWNER.get(new_state), patient_id),
                )
        elif new_state != state:
            conn.execute(
                "UPDATE patients SET state=?, owner=?, entered_state_at=? WHERE id=?",
                (new_state, STATE_OWNER.get(new_state), ts, patient_id),
            )

        payload = dict(payload)
        if actor:
            payload.setdefault("actor", actor)
        _insert_event(conn, ts, patient_id, etype, actor, payload, resulting_state)
    return get_patient(patient_id)


def advance(patient_id, event_type, actor=None, payload=None, ts=None):
    return exec_event(patient_id, event_type, actor=actor, payload=payload, ts=ts)


def log_interruption(patient_id, at_role="reception", note=""):
    return exec_event(
        patient_id,
        "INTERRUPTION",
        actor=at_role,
        payload={"at_role": at_role, "note": note, "patient_id": patient_id},
    )


def notify_patient(patient_id, message="", channel="sms", actor="reception"):
    return exec_event(
        patient_id,
        "NOTIFIED",
        actor=actor,
        payload={"channel": channel, "message": message},
    )


def resolve_handoff(handoff_id, by="clinician"):
    with tx() as conn:
        handoff = conn.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
    if not handoff:
        raise KeyError(f"Unknown handoff {handoff_id}")
    if handoff["status"] not in ("open", "dropped"):
        return {
            "ok": True,
            "already_resolved": True,
            "handoff_id": handoff_id,
            "status": handoff["status"],
        }
    patient_id = handoff["patient_id"]
    try:
        patient = exec_event(
            patient_id,
            "HANDOFF_ACCEPTED",
            actor=by,
            payload={"handoff_id": handoff_id},
        )
    except Exception:  # noqa: BLE001
        ts = now_iso()
        with tx() as conn:
            conn.execute(
                "UPDATE handoffs SET status='accepted', accepted_at=?, accepted_by=? WHERE id=?",
                (ts, by, handoff_id),
            )
            conn.execute(
                "UPDATE patients SET state=COALESCE(prev_state, state), owner=COALESCE(prev_owner, owner),"
                " blocked=0, block_reason=NULL, blocked_since=NULL, prev_state=NULL, prev_owner=NULL"
                " WHERE id=?",
                (patient_id,),
            )
            conn.execute(
                "UPDATE alerts SET status='resolved', resolved_at=? WHERE patient_id=? AND status!='resolved'",
                (ts, patient_id),
            )
            _insert_event(
                conn,
                ts,
                patient_id,
                "HANDOFF_ACCEPTED",
                by,
                {"handoff_id": handoff_id, "forced": True},
                None,
            )
        patient = get_patient(patient_id)
    return {"ok": True, "handoff_id": handoff_id, "patient": patient}


def get_patient(patient_id):
    with tx() as conn:
        row = conn.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not row:
        raise KeyError(f"Unknown patient {patient_id}")
    patient = _patient_dict(row)
    handoffs = list_handoffs(patient_id=patient_id)
    patient["handoffs"] = handoffs
    open_handoffs = [h for h in handoffs if h["status"] in ("open", "dropped")]
    patient["open_handoff"] = open_handoffs[-1] if open_handoffs else None
    return patient


def list_handoffs(patient_id=None, status=None):
    query = "SELECT * FROM handoffs WHERE 1=1"
    args = []
    if patient_id:
        query += " AND patient_id=?"
        args.append(patient_id)
    if status:
        query += " AND status=?"
        args.append(status)
    query += " ORDER BY id"
    with tx() as conn:
        rows = conn.execute(query, args).fetchall()
    return [dict(r) for r in rows]


def get_queue(role=None, status="waiting"):
    with tx() as conn:
        rows = conn.execute("SELECT * FROM patients ORDER BY entered_state_at").fetchall()
    now = datetime.now()
    out = []
    for row in rows:
        patient = _patient_dict(row, now)
        if status == "waiting" and (patient["state"] not in WAITING_STATES or patient["blocked"]):
            continue
        if status == "active" and patient["state"] not in ACTIVE_STATES:
            continue
        if status == "blocked" and not patient["blocked"]:
            continue
        if status == "open" and patient["state"] == DONE:
            continue
        if status == "done" and patient["state"] != DONE:
            continue
        if role and patient["owner"] != role:
            continue
        out.append(patient)
    out.sort(key=lambda p: p["wait_minutes"], reverse=True)
    return out


def next_for(role):
    candidates = [p for p in get_queue(role=role, status="open") if not p["blocked"]]
    if not candidates:
        return {"role": role, "patient": None, "reason": "No patients waiting for this role"}
    patient = candidates[0]
    reason = patient["state_label"]
    handoffs = [h for h in list_handoffs(patient_id=patient["id"]) if h["status"] in ("open", "dropped")]
    if handoffs and handoffs[-1]["status"] == "dropped":
        reason = f"{reason} — handoff overdue ({handoffs[-1]['item']})"
    return {
        "role": role,
        "patient": patient,
        "reason": reason,
        "waited_minutes": patient["wait_minutes"],
    }


def get_alerts(status="open"):
    query = "SELECT a.*, p.name AS patient_name FROM alerts a LEFT JOIN patients p ON p.id=a.patient_id"
    args = []
    if status != "all":
        query += " WHERE a.status=?"
        args.append(status)
    query += " ORDER BY a.id DESC"
    with tx() as conn:
        rows = conn.execute(query, args).fetchall()
    return [dict(r) for r in rows]


def _alert_exists(conn, patient_id, kind):
    return (
        conn.execute(
            "SELECT id FROM alerts WHERE patient_id=? AND kind=? AND status!='resolved'",
            (patient_id, kind),
        ).fetchone()
        is not None
    )


def _create_alert(conn, patient_id, kind, message, handoff_id=None, ts=None):
    if _alert_exists(conn, patient_id, kind):
        return None
    conn.execute(
        "INSERT INTO alerts(ts,patient_id,handoff_id,kind,message,status) VALUES(?,?,?,?,?, 'open')",
        (ts or now_iso(), patient_id, handoff_id, kind, message),
    )
    return True


def refresh():
    now = datetime.now()
    newly_flagged = []
    with tx() as conn:
        open_handoffs = conn.execute("SELECT * FROM handoffs WHERE status='open'").fetchall()
        for handoff in open_handoffs:
            patient = conn.execute(
                "SELECT * FROM patients WHERE id=?", (handoff["patient_id"],)
            ).fetchone()
            if not patient or patient["state"] == DONE:
                conn.execute(
                    "UPDATE handoffs SET status='accepted', accepted_at=?, accepted_by='system' WHERE id=?",
                    (now_iso(), handoff["id"]),
                )
                continue
            if patient["blocked"]:
                continue
            initiated = parse_ts(handoff["initiated_at"])
            age = (now - initiated).total_seconds() / 60
            sla = handoff["sla_minutes"] or config.DEFAULT_SLA_MINUTES
            if age <= sla:
                continue
            conn.execute("UPDATE handoffs SET status='dropped' WHERE id=?", (handoff["id"],))
            blocked_since = (initiated + timedelta(minutes=sla)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE patients SET blocked=1, prev_state=state, prev_owner=owner, blocked_since=?,"
                " block_reason=? WHERE id=? AND blocked=0",
                (
                    blocked_since,
                    f"{handoff['item']} handoff from {handoff['from_role']} to {handoff['to_role']} overdue",
                    handoff["patient_id"],
                ),
            )
            _insert_event(
                conn,
                now_iso(),
                handoff["patient_id"],
                "FLAGGED_DROPPED_HANDOFF",
                "system",
                {
                    "handoff_id": handoff["id"],
                    "to_role": handoff["to_role"],
                    "item": handoff["item"],
                    "age_minutes": round(age, 1),
                },
                BLOCKED,
            )
            created = _create_alert(
                conn,
                handoff["patient_id"],
                "DROPPED_HANDOFF",
                f"{patient['name']} ({patient['id']}) has waited {age:.0f} min for "
                f"{handoff['to_role']} — {handoff['item']}",
                handoff_id=handoff["id"],
            )
            if created:
                newly_flagged.append(handoff["patient_id"])

        clinician_busy = conn.execute(
            "SELECT COUNT(*) n FROM patients WHERE state IN ('IN_TRIAGE','IN_CONSULT')"
        ).fetchone()["n"]
        if clinician_busy == 0:
            waiting = conn.execute(
                "SELECT * FROM patients WHERE blocked=0 AND owner='clinician' AND state IN"
                " ('WAITING_CONSULT','WAITING_REPORT_REVIEW')"
            ).fetchall()
            for patient in waiting:
                waited = (now - parse_ts(patient["entered_state_at"])).total_seconds() / 60
                if waited > config.IDLE_OWNER_MINUTES:
                    _create_alert(
                        conn,
                        patient["id"],
                        "IDLE_OWNER",
                        f"{patient['name']} ({patient['id']}) has waited {waited:.0f} min for the doctor,"
                        " but no consultation is in progress",
                    )
        else:
            conn.execute(
                "UPDATE alerts SET status='resolved', resolved_at=? WHERE kind='IDLE_OWNER' AND status!='resolved'",
                (now_iso(),),
            )
    return newly_flagged


def timeline(patient_id, limit=60):
    with tx() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE patient_id=? ORDER BY id DESC LIMIT ?", (patient_id, limit)
        ).fetchall()
    return [_humanize_event(r) for r in reversed(rows)]


def _humanize_event(row):
    payload = json.loads(row["payload"] or "{}")
    etype = row["type"]
    text = etype.replace("_", " ").title()
    if etype == "PATIENT_ARRIVED":
        text = f"Arrived — {payload.get('reason') or 'no reason recorded'}"
    elif etype == "ROUTED":
        text = f"Routed to {payload.get('queue', 'reception')}"
    elif etype == "CALLED":
        text = f"Called in by {OWNER_LABEL.get(row['actor'], row['actor'] or 'staff')}"
    elif etype == "STARTED":
        text = f"Started with {OWNER_LABEL.get(row['actor'], row['actor'] or 'staff')}"
    elif etype == "COMPLETED":
        text = f"Completed by {OWNER_LABEL.get(row['actor'], row['actor'] or 'staff')}"
    elif etype == "LAB_ORDERED":
        text = f"Lab ordered — {payload.get('test', 'test')}"
    elif etype == "LAB_RESULT_READY":
        text = "Lab result ready"
    elif etype == "HANDOFF_INITIATED":
        text = f"Handoff {payload.get('from')} → {payload.get('to')}: {payload.get('item')}"
    elif etype == "HANDOFF_ACCEPTED":
        text = f"Handoff accepted by {row['actor'] or 'staff'}"
    elif etype == "REPORT_FIX_REQUESTED":
        text = "Document fix requested"
    elif etype == "REPORT_FIXED":
        text = "Document fixed"
    elif etype == "NOTIFIED":
        text = f"Patient notified via {payload.get('channel', 'sms')}"
    elif etype == "PICKED_UP":
        text = "Report collected"
    elif etype == "INTERRUPTION":
        text = f"Interruption at {payload.get('at_role', 'desk')} — {payload.get('note') or 'asked why they wait'}"
    elif etype == "FLAGGED_DROPPED_HANDOFF":
        text = f"Flagged: {payload.get('item')} handoff to {payload.get('to_role')} overdue"
    elif etype == "ESCALATION_ACCEPTED":
        text = "Escalation accepted"
    return {
        "id": row["id"],
        "ts": row["ts"],
        "type": etype,
        "actor": row["actor"],
        "text": text,
        "state_after": row["state_after"],
    }


def steps_for(patient):
    state = patient["prev_state"] if patient["blocked"] and patient["prev_state"] else patient["state"]
    if patient["type"] in ("report", "return"):
        labels = ["Arrived", "Documents checked", "Report ready", "Collected"]
        mapping = {
            ARRIVED: 0,
            WAITING_RECEPTION: 1,
            WAITING_DOC_FIX: 1,
            READY_FOR_PICKUP: 2,
            NOTIFIED: 2,
            DONE: 3,
        }
    else:
        labels = ["Arrived", "Triage", "Consultation", "Lab", "Report review", "Done"]
        mapping = {
            ARRIVED: 0,
            WAITING_RECEPTION: 0,
            WAITING_TRIAGE: 1,
            IN_TRIAGE: 1,
            WAITING_CONSULT: 2,
            IN_CONSULT: 2,
            WAITING_DOC_FIX: 2,
            WAITING_LAB_COLLECTION: 3,
            LAB_PROCESSING: 3,
            WAITING_REPORT_REVIEW: 4,
            READY_FOR_PICKUP: 5,
            NOTIFIED: 5,
            DONE: 5,
        }
    index = mapping.get(state, 0)
    steps = []
    for i, label in enumerate(labels):
        status = "done" if i < index else ("current" if i == index else "todo")
        steps.append({"label": label, "status": status})
    return steps
