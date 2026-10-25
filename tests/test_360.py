import pytest
from fastapi.testclient import TestClient

import server
from arogya.lib.network_360 import network_overview
from arogya.lib.patient_360 import get_patient, patient_360, patients
from arogya.lib.preauth import preauth

KEY = {"X-Adjudicator-Key": "demo-key"}
api = TestClient(server.app)
P = {p["name"]: p["policy"] for p in patients()}


def codes(rid):
    return {f["code"] for f in preauth.requests[rid]["flags"]}


def test_regular_treatments_are_found_from_history():
    kinds = {n: [r["procedure"].split()[0] for r in patient_360(p)["regularTreatments"]] for n, p in P.items()}
    assert kinds["Rajesh Sharma"] == ["Haemodialysis"] and kinds["Amit Patel"] == ["Chemotherapy"] and kinds["Sneha Kulkarni"] == ["Physiotherapy"]
    assert kinds["Arjun Nair"] == [] and kinds["Kavita Joshi"] == []


def test_past_account_misuse_pattern_is_visible():
    t = patient_360(P["Sneha Kulkarni"])["timeline"]
    flagged = [x for x in t if x["flags"]]
    assert [x["date"] for x in flagged] == ["2026-10-06", "2026-10-08"] and "NEW_CONTACT" in {f["code"] for f in flagged[0]["flags"]}


def test_each_planted_pattern_break_is_caught():
    assert preauth.requests["PA-1001"]["status"] == "awaiting" and preauth.requests["PA-1001"]["recommendation"]
    assert {"NEW_HOSPITAL", "OTHER_CITY"} <= codes("PA-1002")
    assert "AMOUNT_HIGH" in codes("PA-1003")
    assert codes("PA-1004") == {"NEW_CONTACT"}
    assert codes("PA-1005") == {"TOO_SOON"}
    assert all(preauth.requests[r]["status"] == "held" for r in ("PA-1002", "PA-1003", "PA-1004", "PA-1005"))


def test_standing_authorisation_clears_later_sessions_and_stops_breaks():
    preauth.grant_standing("PA-1001", "Asha", "junior")
    assert preauth.requests["PA-1001"]["status"] == "cleared"
    ok = api.post("/api/hospital/Apollo Hospitals/preauth", json={"policy": P["Rajesh Sharma"], "procedure": "Haemodialysis session", "serviceDate": "2026-10-28",
                                                                  "amount": 2800, "contactLast4": get_patient(P["Rajesh Sharma"])["registeredContactMasked"][-4:]}).json()
    assert ok["status"] == "Approved"
    wrong_phone = api.post("/api/hospital/Apollo Hospitals/preauth", json={"policy": P["Rajesh Sharma"], "procedure": "Haemodialysis session", "serviceDate": "2026-10-30",
                                                                          "amount": 2800, "contactLast4": "0000"}).json()
    assert wrong_phone["status"].startswith("Being reviewed")
    same_day = api.post("/api/hospital/Apollo Hospitals/preauth", json={"policy": P["Rajesh Sharma"], "procedure": "Haemodialysis session", "serviceDate": "2026-10-28",
                                                                       "amount": 2800, "contactLast4": get_patient(P["Rajesh Sharma"])["registeredContactMasked"][-4:]}).json()
    assert same_day["status"].startswith("Being reviewed")      # second session on the same day


def test_standing_terms_cannot_exceed_recommendation_or_start_from_a_held_request():
    with pytest.raises(ValueError):
        preauth.grant_standing("PA-1001", "Asha", "junior", {"perSessionCap": 99999})
    with pytest.raises(ValueError):
        preauth.grant_standing("PA-1002", "Asha", "senior")


def test_held_request_needs_senior_and_verification_note():
    with pytest.raises(ValueError):
        preauth.resolve("PA-1004", "approve", "Asha", "junior", "called")
    with pytest.raises(ValueError):
        preauth.resolve("PA-1004", "approve", "Ravi", "senior", "ok")
    assert preauth.resolve("PA-1004", "approve", "Ravi", "senior", "Called registered number, patient confirmed new phone")["status"] == "approved"
    with pytest.raises(ValueError):
        preauth.resolve("PA-1004", "approve", "Ravi", "senior", "Called registered number, patient confirmed new phone")


def test_overpriced_hospital_surfaces_in_network_360():
    rows = network_overview()
    assert rows[0]["hospital"] == "Sterling Hospital" and rows[0]["status"] == "review" and rows[0]["billingVsBenchmark"] > 2
    assert {r["hospital"] for r in rows if r["status"] == "normal"} >= {"Apollo Hospitals", "Max Super Speciality Hospital"}


def test_patient_and_hospital_never_see_reasons():
    pol = P["Ananya Deshmukh"]
    home = api.get(f"/api/patient/{pol}").json()
    text = str(home) + str(api.get("/api/hospital/Manipal Hospital").json())
    assert "NEW_CONTACT" not in text and "registered number" in text and "Being reviewed" in text


def test_adjudicator_routes_need_the_key():
    for url in ("/api/adjudicator/queue", f"/api/adjudicator/patients/{P['Rajesh Sharma']}", "/api/adjudicator/network"):
        assert api.get(url).status_code == 401 and api.get(url, headers=KEY).status_code == 200
    assert api.post("/api/claims/CLM-001/decision", json={"approver": "x"}).status_code == 401
