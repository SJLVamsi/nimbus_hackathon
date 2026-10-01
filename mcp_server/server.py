from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from handoff.tools import dispatch  # noqa: E402

mcp = MCPServer(
    "handoff",
    instructions=(
        "Handoff is a neighborhood clinic queue. It tracks every patient's state, the owner of every "
        "wait, and every handoff between reception, nurses, clinicians and the lab. Use the tools to "
        "see the queue, find where waiting time accumulates, detect dropped handoffs, and act on them."
    ),
)


def _run(name, args):
    return json.dumps(dispatch(name, args), default=str)


@mcp.tool()
def get_queue(role: str | None = None, status: str = "waiting") -> str:
    """List patients in the clinic queue with state, owner and wait time. status: waiting|active|blocked|open|done|all."""
    return _run("get_queue", {"role": role, "status": status})


@mcp.tool()
def get_patient(patient_id: str) -> str:
    """Full status of one patient including state, owner, waiting time, steps, timeline and handoffs."""
    return _run("get_patient", {"patient_id": patient_id})


@mcp.tool()
def next_for(role: str) -> str:
    """Who a given role should serve next, and why. role: reception|nurse|clinician|lab."""
    return _run("next_for", {"role": role})


@mcp.tool()
def list_bottlenecks(window_minutes: int = 240) -> str:
    """Where waiting time is accumulating per state, with p50/p90 and totals."""
    return _run("list_bottlenecks", {"window_minutes": window_minutes})


@mcp.tool()
def list_missed_handoffs() -> str:
    """Handoffs that were never accepted in time (dropped), with patient details."""
    return _run("list_missed_handoffs", {})


@mcp.tool()
def staff_interruptions(role: str | None = None, window_minutes: int = 1440) -> str:
    """Interruptions where patients asked staff why they are waiting."""
    return _run("staff_interruptions", {"role": role, "window_minutes": window_minutes})


@mcp.tool()
def explain_wait(patient_id: str, language: str = "en") -> str:
    """Plain-language explanation of a patient's wait with ETA. language: en|hi."""
    return _run("explain_wait", {"patient_id": patient_id, "language": language})


@mcp.tool()
def advance(patient_id: str, event_type: str, actor: str | None = None, notes: str | None = None) -> str:
    """Record a workflow event: CALLED, STARTED, COMPLETED, LAB_ORDERED, LAB_RESULT_READY, REPORT_FIX_REQUESTED, REPORT_FIXED, MARK_READY, PICKED_UP."""
    return _run(
        "advance",
        {"patient_id": patient_id, "event_type": event_type, "actor": actor, "notes": notes},
    )


@mcp.tool()
def accept_handoff(handoff_id: int, by: str = "clinician") -> str:
    """Accept or rescue a dropped handoff, which clears the patient's blocked state."""
    return _run("accept_handoff", {"handoff_id": handoff_id, "by": by})


@mcp.tool()
def notify_patient(patient_id: str, channel: str = "sms") -> str:
    """Draft and send a pickup notification to a patient whose report is ready."""
    return _run("notify_patient", {"patient_id": patient_id, "channel": channel})


@mcp.tool()
def register_patient(name: str, reason: str) -> str:
    """Register a new patient from a free-text reason, classifying the right queue automatically."""
    return _run("register_patient", {"name": name, "reason": reason})


if __name__ == "__main__":
    mcp.run()
