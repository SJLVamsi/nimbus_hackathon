ARRIVED = "ARRIVED"
WAITING_RECEPTION = "WAITING_RECEPTION"
WAITING_TRIAGE = "WAITING_TRIAGE"
IN_TRIAGE = "IN_TRIAGE"
WAITING_CONSULT = "WAITING_CONSULT"
IN_CONSULT = "IN_CONSULT"
WAITING_LAB_COLLECTION = "WAITING_LAB_COLLECTION"
LAB_PROCESSING = "LAB_PROCESSING"
WAITING_REPORT_REVIEW = "WAITING_REPORT_REVIEW"
WAITING_DOC_FIX = "WAITING_DOC_FIX"
READY_FOR_PICKUP = "READY_FOR_PICKUP"
NOTIFIED = "NOTIFIED"
BLOCKED = "BLOCKED_NO_OWNER"
DONE = "DONE"

STATE_OWNER = {
    ARRIVED: "reception",
    WAITING_RECEPTION: "reception",
    WAITING_TRIAGE: "nurse",
    IN_TRIAGE: "nurse",
    WAITING_CONSULT: "clinician",
    IN_CONSULT: "clinician",
    WAITING_LAB_COLLECTION: "lab",
    LAB_PROCESSING: "lab",
    WAITING_REPORT_REVIEW: "clinician",
    WAITING_DOC_FIX: "reception",
    READY_FOR_PICKUP: "reception",
    NOTIFIED: "patient",
    BLOCKED: "system",
    DONE: None,
}

STATE_LABEL = {
    ARRIVED: "Just arrived",
    WAITING_RECEPTION: "Waiting at reception",
    WAITING_TRIAGE: "Waiting for triage",
    IN_TRIAGE: "In triage",
    WAITING_CONSULT: "Waiting for doctor",
    IN_CONSULT: "With the doctor",
    WAITING_LAB_COLLECTION: "Waiting for lab sample",
    LAB_PROCESSING: "Lab processing",
    WAITING_REPORT_REVIEW: "Waiting for report review",
    WAITING_DOC_FIX: "Waiting for document fix",
    READY_FOR_PICKUP: "Ready for pickup",
    NOTIFIED: "Notified, awaiting pickup",
    BLOCKED: "No owner — flagged",
    DONE: "Completed",
}

OWNER_LABEL = {
    "reception": "Front desk",
    "nurse": "Nurse",
    "clinician": "Doctor",
    "lab": "Lab",
    "patient": "You",
    "system": "System",
}

PATIENT_ACTION = {
    ARRIVED: "being registered",
    WAITING_RECEPTION: "front desk to check your documents",
    WAITING_TRIAGE: "the nurse to call you for vitals",
    IN_TRIAGE: "the nurse to finish vitals",
    WAITING_CONSULT: "the doctor to call you in",
    IN_CONSULT: "your consultation",
    WAITING_LAB_COLLECTION: "the lab to collect your sample",
    LAB_PROCESSING: "the lab to finish processing your sample",
    WAITING_REPORT_REVIEW: "the doctor to review your report",
    WAITING_DOC_FIX: "front desk to correct your document",
    READY_FOR_PICKUP: "front desk to hand over your report",
    NOTIFIED: "you to collect your report",
    BLOCKED: "staff to be alerted — no owner on your wait",
    DONE: "nothing — all done",
}

DEFAULT_ETA = {
    WAITING_RECEPTION: 8,
    WAITING_TRIAGE: 6,
    IN_TRIAGE: 4,
    WAITING_CONSULT: 12,
    IN_CONSULT: 10,
    WAITING_LAB_COLLECTION: 8,
    LAB_PROCESSING: 15,
    WAITING_REPORT_REVIEW: 8,
    WAITING_DOC_FIX: 6,
    READY_FOR_PICKUP: 5,
    NOTIFIED: 0,
    ARRIVED: 2,
    BLOCKED: 5,
}

WAITING_STATES = {
    ARRIVED,
    WAITING_RECEPTION,
    WAITING_TRIAGE,
    WAITING_CONSULT,
    WAITING_LAB_COLLECTION,
    LAB_PROCESSING,
    WAITING_REPORT_REVIEW,
    WAITING_DOC_FIX,
    READY_FOR_PICKUP,
    NOTIFIED,
    BLOCKED,
}
ACTIVE_STATES = {IN_TRIAGE, IN_CONSULT}
CLINICAL_ACTIVE_STATES = {IN_TRIAGE, IN_CONSULT}


def transition(state, etype, payload):
    payload = payload or {}
    if etype == "ROUTED":
        target = {
            "triage": WAITING_TRIAGE,
            "reception": WAITING_RECEPTION,
            "report": WAITING_RECEPTION,
            "doc_fix": WAITING_DOC_FIX,
            "clinician": WAITING_CONSULT,
        }.get(payload.get("queue"))
        return (target or WAITING_RECEPTION), []
    if state == WAITING_TRIAGE and etype == "CALLED":
        return IN_TRIAGE, [("accept",)]
    if state == IN_TRIAGE and etype == "COMPLETED":
        return WAITING_CONSULT, [("open", "clinician", "consultation")]
    if state == WAITING_CONSULT and etype in ("CALLED", "HANDOFF_ACCEPTED"):
        return IN_CONSULT, [("accept",)]
    if state == IN_CONSULT and etype == "COMPLETED":
        return DONE, []
    if state == IN_CONSULT and etype == "LAB_ORDERED":
        return WAITING_LAB_COLLECTION, [("open", "lab", "collect sample")]
    if state in (IN_CONSULT, WAITING_RECEPTION) and etype == "REPORT_FIX_REQUESTED":
        return WAITING_DOC_FIX, [("open", "reception", "fix document")]
    if state == WAITING_DOC_FIX and etype == "REPORT_FIXED":
        return READY_FOR_PICKUP, [("accept",)]
    if state == WAITING_LAB_COLLECTION and etype in ("STARTED", "HANDOFF_ACCEPTED"):
        return LAB_PROCESSING, [("accept",)]
    if state == LAB_PROCESSING and etype == "LAB_RESULT_READY":
        return WAITING_REPORT_REVIEW, [("open", "clinician", "review report")]
    if state == WAITING_REPORT_REVIEW and etype == "HANDOFF_ACCEPTED":
        return READY_FOR_PICKUP, [("accept",), ("open", "reception", "hand over report")]
    if state == READY_FOR_PICKUP and etype == "NOTIFIED":
        return NOTIFIED, [("accept",)]
    if state in (READY_FOR_PICKUP, NOTIFIED) and etype == "PICKED_UP":
        return DONE, []
    if state == WAITING_RECEPTION and etype == "COMPLETED":
        return DONE, []
    if state == WAITING_RECEPTION and etype == "MARK_READY":
        return READY_FOR_PICKUP, []
    if state in WAITING_STATES and etype == "HANDOFF_ACCEPTED":
        return state, [("accept",)]
    return None
