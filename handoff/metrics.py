from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from . import config
from .db import parse_ts, tx
from .state import STATE_LABEL, WAITING_STATES


def _cutoff(window_minutes):
    if not window_minutes:
        return None
    return (datetime.now() - timedelta(minutes=window_minutes)).isoformat(timespec="seconds")


def stage_durations(window_minutes=None):
    cutoff = _cutoff(window_minutes)
    with tx() as conn:
        if cutoff:
            rows = conn.execute(
                "SELECT patient_id, ts, state_after FROM events WHERE ts >= ? ORDER BY patient_id, ts, id",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT patient_id, ts, state_after FROM events ORDER BY patient_id, ts, id"
            ).fetchall()
    durations = defaultdict(list)
    previous = None
    for row in rows:
        if previous and previous["patient_id"] == row["patient_id"]:
            delta = (parse_ts(row["ts"]) - parse_ts(previous["ts"])).total_seconds() / 60
            if 0 <= delta <= config.MAX_REASONABLE_SEGMENT_MINUTES:
                durations[previous["state_after"]].append(delta)
        previous = row
    return dict(durations)


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1)))))
    return round(ordered[index], 1)


def stage_median(state):
    values = stage_durations().get(state) or []
    if len(values) >= 3:
        return percentile(values, 50)
    return None


def handoff_stats():
    with tx() as conn:
        rows = conn.execute("SELECT * FROM handoffs").fetchall()
    latencies = []
    dropped = 0
    open_count = 0
    for row in rows:
        if row["status"] == "dropped":
            dropped += 1
        if row["status"] == "open":
            open_count += 1
        if row["accepted_at"]:
            latency = (parse_ts(row["accepted_at"]) - parse_ts(row["initiated_at"])).total_seconds() / 60
            if latency >= 0:
                latencies.append(latency)
    return {
        "total": len(rows),
        "open": open_count,
        "dropped": dropped,
        "accepted": len(latencies),
        "median_latency_minutes": percentile(latencies, 50),
        "p90_latency_minutes": percentile(latencies, 90),
    }


def interruptions(window_minutes=24 * 60, role=None):
    cutoff = _cutoff(window_minutes)
    query = "SELECT ts, patient_id, actor, payload FROM events WHERE type='INTERRUPTION'"
    args = []
    if cutoff:
        query += " AND ts >= ?"
        args.append(cutoff)
    with tx() as conn:
        rows = conn.execute(query + " ORDER BY id DESC", args).fetchall()
    out = []
    for row in rows:
        import json

        payload = json.loads(row["payload"] or "{}")
        at_role = payload.get("at_role") or row["actor"]
        if role and at_role != role:
            continue
        out.append(
            {
                "ts": row["ts"],
                "patient_id": row["patient_id"],
                "at_role": at_role,
                "note": payload.get("note", ""),
            }
        )
    return out


def kpis():
    now = datetime.now()
    with tx() as conn:
        patients = conn.execute("SELECT * FROM patients").fetchall()
    active = [p for p in patients if p["state"] != "DONE"]
    blocked = [p for p in active if p["blocked"]]
    waiting = [p for p in active if not p["blocked"] and p["state"] in WAITING_STATES]
    current_waits = [
        max(0.0, (now - parse_ts(p["entered_state_at"])).total_seconds() / 60)
        for p in waiting + blocked
    ]
    orphan_minutes = 0.0
    for p in blocked:
        since = p["blocked_since"] or p["entered_state_at"]
        orphan_minutes += max(0.0, (now - parse_ts(since)).total_seconds() / 60)
    total_waiting = len(waiting) + len(blocked)
    with_owner = len(waiting)
    today = now.strftime("%Y-%m-%d")
    with tx() as conn:
        interruptions_today = conn.execute(
            "SELECT COUNT(*) n FROM events WHERE type='INTERRUPTION' AND ts >= ?", (today,)
        ).fetchone()["n"]
    stats = handoff_stats()
    return {
        "active_patients": len(active),
        "waiting": len(waiting),
        "blocked": len(blocked),
        "median_wait_minutes": percentile(current_waits, 50),
        "p90_wait_minutes": percentile(current_waits, 90),
        "waits_with_owner_pct": round(100.0 * with_owner / total_waiting, 0) if total_waiting else 100.0,
        "dropped_handoffs": stats["dropped"],
        "open_handoffs": stats["open"],
        "handoff_median_latency_minutes": stats["median_latency_minutes"],
        "interruptions_today": interruptions_today,
        "orphan_minutes": round(orphan_minutes, 1),
    }


def bottlenecks(window_minutes=240):
    durations = stage_durations(window_minutes)
    out = []
    for state, values in durations.items():
        if state not in WAITING_STATES:
            continue
        out.append(
            {
                "state": state,
                "state_label": STATE_LABEL.get(state, state),
                "samples": len(values),
                "total_minutes": round(sum(values), 1),
                "p50_minutes": percentile(values, 50),
                "p90_minutes": percentile(values, 90),
            }
        )
    out.sort(key=lambda item: item["total_minutes"], reverse=True)
    return out
