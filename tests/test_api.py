from fastapi.testclient import TestClient

import server

api = TestClient(server.app, headers={"X-Adjudicator-Key": "demo-key"})


def test_run_decide_and_metrics():
    assert api.get("/healthz").json() == {"ok": True}
    assert api.post("/api/claims/NOPE/run").status_code == 404
    r = api.post("/api/claims/CLM-001/run").json()
    assert r["status"] == "waiting_for_adjudicator"
    level = "senior" if r["worksheet"]["requiresSeniorApproval"] else "junior"
    assert api.post("/api/claims/CLM-001/decision", json={"approver": "Asha", "level": level}).json()["outcome"] == "settled"
    assert api.post("/api/claims/CLM-001/decision", json={"approver": "Asha"}).status_code == 409
    assert "arogya_claims_total" in api.get("/metrics").text
    assert api.get("/api/metrics/summary").json()["claims"] == 80


def test_lessons_and_report():
    assert len(api.get("/api/lessons").json()) == 4
    assert api.post("/api/lessons/LESSON-001/approve", json={"reviewer": "x"}).status_code == 400   # already approved
    api.post("/api/claims/CLM-002/run")
    assert "payable" in api.post("/api/llm/settlement-report", json={"claimId": "CLM-002"}).json()["text"].lower()
