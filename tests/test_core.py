import os
import tempfile
import unittest
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="handoff-test-")
os.environ["HANDOFF_DB"] = os.path.join(TMP, "test.db")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_BASE_URL", None)

from handoff import ai, core, db, metrics, tools  # noqa: E402


def ago(minutes):
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


class CoreFlowTest(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.reset_db()

    def _to_review(self, pid):
        core.create_patient(pid, "Test Patient", "walkin", "fever")
        core.advance(pid, "ROUTED", payload={"queue": "triage"})
        core.advance(pid, "CALLED", actor="nurse")
        core.advance(pid, "COMPLETED", actor="nurse")
        core.advance(pid, "CALLED", actor="clinician")
        core.advance(pid, "LAB_ORDERED", actor="clinician", payload={"test": "blood"})
        core.advance(pid, "STARTED", actor="lab")
        core.advance(pid, "LAB_RESULT_READY", actor="lab")

    def test_full_flow_to_done(self):
        self._to_review("T1")
        self.assertEqual(core.get_patient("T1")["state"], "WAITING_REPORT_REVIEW")
        core.advance("T1", "HANDOFF_ACCEPTED", actor="clinician")
        self.assertEqual(core.get_patient("T1")["state"], "READY_FOR_PICKUP")
        tools.dispatch("notify_patient", {"patient_id": "T1"})
        self.assertEqual(core.get_patient("T1")["state"], "NOTIFIED")
        core.advance("T1", "PICKED_UP", actor="reception")
        self.assertEqual(core.get_patient("T1")["state"], "DONE")

    def test_dropped_handoff_detection_and_rescue(self):
        self._to_review("T2")
        with db.tx() as conn:
            conn.execute(
                "UPDATE handoffs SET initiated_at=? WHERE patient_id='T2' AND status='open'", (ago(20),)
            )
        flagged = core.refresh()
        self.assertIn("T2", flagged)
        patient = core.get_patient("T2")
        self.assertTrue(patient["blocked"])
        self.assertIn("overdue", patient["block_reason"])
        handoff = core.list_handoffs(patient_id="T2", status="dropped")[0]
        result = core.resolve_handoff(handoff["id"], by="clinician")
        patient = result["patient"]
        self.assertFalse(patient["blocked"])
        self.assertEqual(patient["state"], "READY_FOR_PICKUP")
        self.assertEqual(core.get_alerts(status="open"), [])

    def test_invalid_transition_raises(self):
        core.create_patient("T3", "Test", "walkin", "fever")
        with self.assertRaises(ValueError):
            core.advance("T3", "COMPLETED", actor="clinician")

    def test_intake_classification_fix_beats_report(self):
        result = tools.dispatch(
            "register_patient",
            {"name": "Test", "reason": "spelling mistake in my blood report"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["classification"]["queue"], "doc_fix")
        result = tools.dispatch(
            "register_patient",
            {"name": "Test2", "reason": "I came to collect my scan report"},
        )
        self.assertEqual(result["classification"]["queue"], "report")

    def test_next_for_longest_waiting(self):
        core.create_patient("T4", "A", "walkin", "fever")
        core.advance("T4", "ROUTED", payload={"queue": "triage"})
        core.create_patient("T5", "B", "walkin", "cough")
        core.advance("T5", "ROUTED", payload={"queue": "triage"})
        with db.tx() as conn:
            conn.execute("UPDATE patients SET entered_state_at=? WHERE id='T5'", (ago(25),))
        pick = core.next_for("nurse")
        self.assertEqual(pick["patient"]["id"], "T5")

    def test_metrics_and_bottlenecks(self):
        self._to_review("T6")
        core.advance("T6", "HANDOFF_ACCEPTED", actor="clinician")
        core.advance("T6", "PICKED_UP", actor="reception")
        kpis = metrics.kpis()
        self.assertIn("median_wait_minutes", kpis)
        self.assertGreaterEqual(len(metrics.bottlenecks(240)), 1)

    def test_fallback_explanation(self):
        core.create_patient("T7", "Test", "report", "collect report")
        core.advance("T7", "ROUTED", payload={"queue": "report"})
        result = tools.dispatch("explain_wait", {"patient_id": "T7"})
        self.assertTrue(result["ok"])
        self.assertIn("front desk", result["explanation"]["message"].lower())

    def test_accept_handoff_via_dispatch(self):
        self._to_review("T8")
        with db.tx() as conn:
            conn.execute(
                "UPDATE handoffs SET initiated_at=? WHERE patient_id='T8' AND status='open'", (ago(20),)
            )
        core.refresh()
        handoff = core.list_handoffs(patient_id="T8", status="dropped")[0]
        result = tools.dispatch("accept_handoff", {"handoff_id": handoff["id"], "by": "clinician"})
        self.assertTrue(result["ok"])
        patient = core.get_patient("T8")
        self.assertFalse(patient["blocked"])
        self.assertEqual(patient["state"], "READY_FOR_PICKUP")

    def test_refresh_is_idempotent(self):
        self._to_review("T9")
        with db.tx() as conn:
            conn.execute(
                "UPDATE handoffs SET initiated_at=? WHERE patient_id='T9' AND status='open'", (ago(20),)
            )
        core.refresh()
        core.refresh()
        alerts = [
            a
            for a in core.get_alerts(status="open")
            if a["patient_id"] == "T9" and a["kind"] == "DROPPED_HANDOFF"
        ]
        self.assertEqual(len(alerts), 1)

    def test_bottlenecks_shape(self):
        self._to_review("T10")
        core.advance("T10", "HANDOFF_ACCEPTED", actor="clinician")
        core.advance("T10", "PICKED_UP", actor="reception")
        items = metrics.bottlenecks(240)
        self.assertGreaterEqual(len(items), 1)
        for item in items:
            self.assertIn("state_label", item)
            self.assertIn("p90_minutes", item)

    def test_next_for_empty_role(self):
        pick = core.next_for("lab")
        self.assertIsNone(pick["patient"])

    def test_routed_rejected_from_active_states(self):
        core.create_patient("T11", "Test", "walkin", "fever")
        core.advance("T11", "ROUTED", payload={"queue": "triage"})
        core.advance("T11", "CALLED", actor="nurse")
        with self.assertRaises(ValueError):
            core.advance("T11", "ROUTED", payload={"queue": "reception"})

    def test_reroute_allowed_between_waiting_states(self):
        core.create_patient("T12", "Test", "walkin", "report query")
        core.advance("T12", "ROUTED", payload={"queue": "clinician"})
        patient = core.advance("T12", "ROUTED", payload={"queue": "reception"})
        self.assertEqual(patient["state"], "WAITING_RECEPTION")

    def test_notify_fails_outside_pickup(self):
        core.create_patient("T13", "Test", "walkin", "fever")
        core.advance("T13", "ROUTED", payload={"queue": "triage"})
        result = tools.dispatch("notify_patient", {"patient_id": "T13"})
        self.assertFalse(result["ok"])

    def test_idle_alert_clears_when_doctor_busy(self):
        self._to_review("T14")
        with db.tx() as conn:
            conn.execute(
                "UPDATE patients SET owner='clinician', state='WAITING_CONSULT',"
                " entered_state_at=? WHERE id='T14'",
                (ago(25),),
            )
        core.refresh()
        self.assertTrue(
            any(
                a["kind"] == "IDLE_OWNER" and a["patient_id"] == "T14"
                for a in core.get_alerts(status="open")
            )
        )
        with db.tx() as conn:
            conn.execute(
                "UPDATE patients SET owner='clinician', state='IN_CONSULT',"
                " entered_state_at=? WHERE id='T14'",
                (ago(2),),
            )
        core.refresh()
        self.assertFalse(
            any(
                a["kind"] == "IDLE_OWNER" and a["patient_id"] == "T14"
                for a in core.get_alerts(status="open")
            )
        )

    def test_reset_requires_confirm(self):
        from fastapi.testclient import TestClient

        from api.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            denied = client.post("/api/sim/reset", json={})
            self.assertEqual(denied.status_code, 400)
            allowed = client.post("/api/sim/reset", json={"confirm": True})
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("P12", allowed.json().get("flagged", []))


if __name__ == "__main__":
    unittest.main()
