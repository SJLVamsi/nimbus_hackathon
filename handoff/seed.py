from __future__ import annotations

import random
from datetime import datetime, timedelta

from . import core, db

RNG = random.Random(11)
BASE = datetime.now()


def t(minutes_ago):
    return (BASE - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def _sim_consult(pid, name, ptype, reason, start, with_lab=None):
    cursor = start
    triage_wait = RNG.randint(3, 9)
    triage = RNG.randint(3, 7)
    consult_wait = RNG.randint(6, 26)
    consult = RNG.randint(5, 14)
    core.create_patient(pid, name, ptype, reason, ts=t(cursor))
    cursor -= 1
    core.exec_event(pid, "ROUTED", ts=t(cursor), payload={"queue": "triage"})
    cursor -= triage_wait
    core.exec_event(pid, "CALLED", actor="nurse", ts=t(cursor))
    cursor -= triage
    core.exec_event(pid, "COMPLETED", actor="nurse", ts=t(cursor))
    cursor -= consult_wait
    core.exec_event(pid, "CALLED", actor="clinician", ts=t(cursor))
    if with_lab:
        cursor -= 3
        core.exec_event(pid, "LAB_ORDERED", actor="clinician", ts=t(cursor), payload={"test": with_lab})
        cursor -= RNG.randint(2, 6)
        core.exec_event(pid, "STARTED", actor="lab", ts=t(cursor), payload={"test": with_lab})
        cursor -= RNG.randint(10, 20)
        core.exec_event(pid, "LAB_RESULT_READY", actor="lab", ts=t(cursor), payload={"report_id": f"R-{pid}"})
        cursor -= RNG.randint(4, 12)
        core.exec_event(pid, "HANDOFF_ACCEPTED", actor="clinician", ts=t(cursor))
        cursor -= 2
        core.exec_event(pid, "NOTIFIED", actor="reception", ts=t(cursor), payload={"channel": "sms"})
        cursor -= RNG.randint(4, 15)
        core.exec_event(pid, "PICKED_UP", actor="reception", ts=t(cursor))
    else:
        cursor -= consult
        core.exec_event(pid, "COMPLETED", actor="clinician", ts=t(cursor))


def _sim_report(pid, name, start):
    cursor = start
    core.create_patient(pid, name, "report", "Collect test report", ts=t(cursor))
    cursor -= 1
    core.exec_event(pid, "ROUTED", ts=t(cursor), payload={"queue": "report"})
    cursor -= RNG.randint(5, 12)
    core.exec_event(pid, "COMPLETED", actor="reception", ts=t(cursor))


def _interrupt(pid, role, minutes_ago, note):
    core.exec_event(
        pid,
        "INTERRUPTION",
        actor=role,
        payload={"at_role": role, "note": note, "patient_id": pid},
        ts=t(minutes_ago),
    )


def _seed_completed():
    _sim_consult("P01", "Ravi Kumar", "walkin", "Fever since yesterday", 310)
    _sim_consult("P04", "Priya Sharma", "appointment", "BP review", 270, with_lab="lipid profile")
    _sim_consult("P06", "Imran Khan", "walkin", "Dry cough for a week", 240)
    _sim_report("P08", "Sowmya Reddy", 200)
    _sim_consult("P11", "Gopal Das", "appointment", "Sugar check", 170, with_lab="HbA1c")
    _sim_consult("P13", "Nisha Patel", "walkin", "Headache", 150)
    _sim_report("P15", "Vikram Singh", 120)
    _sim_consult("P16", "Ananya Bose", "return", "Follow-up dressing change", 110)
    _sim_consult("P17", "Rahul Jain", "walkin", "Stomach pain", 95)
    _sim_report("P18", "Shalini Gupta", 60)


def _seed_active():
    core.create_patient("P02", "Fatima Sheikh", "report", "Collect scan report", ts=t(55))
    core.exec_event("P02", "ROUTED", ts=t(54), payload={"queue": "report"})
    core.exec_event("P02", "MARK_READY", actor="reception", ts=t(8))
    core.exec_event("P02", "NOTIFIED", actor="reception", ts=t(4), payload={"channel": "sms"})

    core.create_patient("P03", "Arjun Menon", "appointment", "Diabetes follow-up", ts=t(45))
    core.exec_event("P03", "ROUTED", ts=t(44), payload={"queue": "triage"})
    core.exec_event("P03", "CALLED", actor="nurse", ts=t(40))
    core.exec_event("P03", "COMPLETED", actor="nurse", ts=t(35))
    core.exec_event("P03", "CALLED", actor="clinician", ts=t(6))

    core.create_patient(
        "P05", "Divya Nair", "return", "Spelling mistake in my discharge summary", ts=t(12)
    )
    core.exec_event("P05", "ROUTED", ts=t(11), payload={"queue": "doc_fix"})

    core.create_patient("P07", "Lakshmi Iyer", "report", "Collect my scan report", ts=t(19))
    core.exec_event("P07", "ROUTED", ts=t(18), payload={"queue": "report"})

    core.create_patient("P09", "Suresh Babu", "walkin", "Fever and body ache", ts=t(26))
    core.exec_event("P09", "ROUTED", ts=t(25), payload={"queue": "triage"})
    core.exec_event("P09", "CALLED", actor="nurse", ts=t(22))
    core.exec_event("P09", "COMPLETED", actor="nurse", ts=t(17))

    core.create_patient("P10", "Anjali Verma", "appointment", "Thyroid check", ts=t(8))
    core.exec_event("P10", "ROUTED", ts=t(7), payload={"queue": "triage"})

    core.create_patient(
        "P12",
        "Meera Krishnan",
        "walkin",
        "Came about my blood report, my name is printed wrong",
        ts=t(58),
    )
    core.exec_event("P12", "ROUTED", ts=t(57), payload={"queue": "clinician", "summary": "reception guessed consultation"})
    core.exec_event("P12", "CALLED", actor="clinician", ts=t(36))
    core.exec_event("P12", "LAB_ORDERED", actor="clinician", ts=t(33), payload={"test": "blood panel"})
    core.exec_event("P12", "STARTED", actor="lab", ts=t(31), payload={"test": "blood panel"})
    core.exec_event("P12", "LAB_RESULT_READY", actor="lab", ts=t(26), payload={"report_id": "R-P12"})

    core.create_patient("P14", "Kiran Rao", "walkin", "Lower back pain", ts=t(30))
    core.exec_event("P14", "ROUTED", ts=t(29), payload={"queue": "triage"})
    core.exec_event("P14", "CALLED", actor="nurse", ts=t(25))
    core.exec_event("P14", "COMPLETED", actor="nurse", ts=t(20))


def _seed_interruptions():
    _interrupt("P12", "reception", 40, "asked why she is still waiting")
    _interrupt("P02", "reception", 30, "asked if report is ready")
    _interrupt("P12", "reception", 25, "asked again about the report")
    _interrupt("P12", "reception", 20, "asked why nobody called her name")
    _interrupt("P14", "nurse", 16, "asked how long for the doctor")
    _interrupt("P03", "clinician", 15, "family asked about P12's report")
    _interrupt("P09", "reception", 12, "asked queue position")
    _interrupt("P07", "reception", 9, "asked where her report is")
    _interrupt("P03", "clinician", 8, "family asked again about P12")
    _interrupt("P05", "reception", 5, "asked if spelling fix is done")
    _interrupt("P10", "reception", 4, "asked if she will be called soon")


def seed_demo():
    global BASE
    BASE = datetime.now()
    db.init_db()
    db.reset_db()
    _seed_completed()
    _seed_active()
    _seed_interruptions()
    flagged = core.refresh()
    return {
        "patients": db.table_count("patients"),
        "events": db.table_count("events"),
        "handoffs": db.table_count("handoffs"),
        "alerts": db.table_count("alerts"),
        "flagged": flagged,
    }


if __name__ == "__main__":
    summary = seed_demo()
    print("seeded:", summary)
