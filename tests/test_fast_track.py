import pytest
from fastapi.testclient import TestClient

import server
from arogya.lib.claim_preapproval import claim_preapprovals as cpa
from arogya.lib.claims_db import claims_db
from arogya.lib.patient_360 import patients

api = TestClient(server.app, headers={"X-Adjudicator-Key": "demo-key"})
P = {p["name"]: p for p in patients()}
RAJ, AMIT = P["Rajesh Sharma"], P["Amit Patel"]
NARAYANA = "Narayana Multispeciality Hospital"


def ask(name, hospital, procedure, room="semi_private", days=0, date="2026-11-02", last4=None):
    p = P[name]
    return cpa.request(hospital, p["policy"], procedure, date, room, days, last4 or p["registeredContactMasked"][-4:])


def failed(rec):
    return {c["code"] for c in rec["eligibility"] if not c["ok"]}


def chemo_bill(room_days=1, rate=2800, package=25000, extra=0, number="NM-1", total=None):
    lines = []
    if room_days:
        lines.append({"description": "Semi-private room", "category": "room", "qty": room_days, "rate": rate, "amount": room_days * rate})
    lines.append({"description": "Chemotherapy package", "category": "procedure", "qty": 1, "rate": package, "amount": package})
    if extra:
        lines.append({"description": "Admission kit", "category": "consumables", "qty": 1, "rate": extra, "amount": extra})
    return {"billNumber": number, "lines": lines, "totalStated": total if total is not None else sum(l["amount"] for l in lines)}


def disch(days=1, day="2026-11-02"):
    from datetime import date, timedelta
    end = (date.fromisoformat(day) + timedelta(days=days)).isoformat()
    return {"admissionDate": day, "dischargeDate": end, "roomCategory": "semi_private", "roomDays": days}


def chemo(date="2026-11-02"):
    rec = ask("Amit Patel", NARAYANA, "Chemotherapy cycle (day care)", days=1, date=date)
    assert rec["status"] == "preapproved", failed(rec)
    return rec


chemo2 = chemo


def test_eligible_patient_is_preapproved_from_contracted_rates():
    rec = ask("Rajesh Sharma", "Apollo Hospitals", "Haemodialysis session")
    assert rec["status"] == "preapproved" and rec["assessed"]["total"] == 2800 and rec["assessed"]["payable"] == 2660 and rec["token"]


@pytest.mark.parametrize("name,hospital,proc,expect", [
    ("Sneha Kulkarni", "Apollo Reach Hospital", "Physiotherapy session", "NO_PAST_FLAGS"),
    ("Vikramaditya Roy", "Ruby Hall Clinic", "Haemodialysis session", "NO_FRAUD_HISTORY"),
    ("Meenakshi Sundaram", "Sterling Hospital", "Haemodialysis session", "NETWORK_HOSPITAL"),
    ("Sunil Verma", "Max Super Speciality Hospital", "Haemodialysis session", "NO_OPEN_CASE"),
    ("Arjun Nair", "Apollo Hospitals", "Haemodialysis session", "REGULAR_TREATMENT"),
])
def test_ineligible_patients_go_to_standard_review_with_the_reason(name, hospital, proc, expect):
    rec = ask(name, hospital, proc)
    assert rec["status"] == "standard_review" and expect in failed(rec) and rec["token"] is None


def test_unusual_hospital_phone_or_room_stops_the_fast_flow():
    assert "USUAL_HOSPITAL" in failed(ask("Amit Patel", "Apollo Hospitals", "Chemotherapy cycle (day care)", days=1))
    assert "SESSION_IN_PATTERN" in failed(ask("Rajesh Sharma", "Apollo Hospitals", "Haemodialysis session", last4="0000"))
    assert "ROOM_WITHIN_CAP" in failed(ask("Rajesh Sharma", "Apollo Hospitals", "Haemodialysis session", room="deluxe", days=1))


def test_cover_must_be_enough(monkeypatch):
    from arogya.lib.preauth import preauth
    monkeypatch.setattr(preauth, "cover_left", lambda policy: 1000)
    assert "COVER" in failed(ask("Rajesh Sharma", "Apollo Hospitals", "Haemodialysis session"))


def test_bill_matching_the_assessment_settles_unchanged():
    rec = chemo()
    out = cpa.reconcile(rec["id"], chemo_bill(), disch())
    assert out["status"] == "settled" and out["reconciliation"]["delta"] == 0 and out["reconciliation"]["payable"] == rec["assessed"]["payable"]
    assert rec["id"] in claims_db.settled


def test_downward_corrections_room_days_rate_and_non_payables():
    rec = chemo()
    out = cpa.reconcile(rec["id"], chemo_bill(room_days=2, rate=3000, extra=400), disch(1))["reconciliation"]
    codes = {a["code"]: a["amount"] for a in out["adjustments"]}
    assert codes == {"ROOM_DAYS": 3000, "ROOM_RATE": 200, "NON_PAYABLE": 400}
    assert out["direction"] == "none" and out["payable"] == rec["assessed"]["payable"]      # the bill was padded; the corrections bring it back to the assessment
    rec = chemo2("2026-11-23")
    out = cpa.reconcile(rec["id"], chemo_bill(package=24000, number="NM-LOW"), disch(1, "2026-11-23"))["reconciliation"]
    assert out["direction"] == "down" and out["delta"] == -950 and out["adjustments"][0]["code"] == "BELOW_PACKAGE"


def test_documented_extra_day_is_an_upward_adjustment():
    rec = chemo()
    out = cpa.reconcile(rec["id"], chemo_bill(room_days=2), disch(2))
    rc = out["reconciliation"]
    assert out["status"] == "settled" and rc["direction"] == "up" and rc["delta"] == 2660 and rc["payable"] == rec["assessed"]["payable"] + 2660


def test_what_a_simple_check_cannot_settle_goes_to_an_adjudicator():
    cases = [chemo_bill(package=30000), chemo_bill(total=99999), chemo_bill(rate=5200), chemo_bill(room_days=4)]
    for i, b in enumerate(cases):
        rec = chemo() if i else chemo()
        d = disch(1) if i != 3 else disch(4)
        out = cpa.reconcile(rec["id"], {**b, "billNumber": f"NM-{i}"}, d)
        assert out["status"] == "adjudication" and out["reconciliation"]["exits"], i
        assert rec["id"] not in claims_db.settled


def test_duplicate_bill_and_double_settlement_are_blocked():
    cpa.reconcile(chemo()["id"], chemo_bill(number="NM-DUP"), disch())
    out = cpa.reconcile(chemo("2026-11-23")["id"], chemo_bill(number="NM-DUP"), disch(1, "2026-11-23"))
    assert out["status"] == "adjudication" and "already settled" in out["reconciliation"]["exits"][0]
    with pytest.raises(ValueError):
        cpa.reconcile(out["id"], chemo_bill(number="NM-X"), disch())


def test_exit_needs_a_senior_with_a_note_and_settles_the_proposed_amount():
    rec = chemo()
    cpa.reconcile(rec["id"], chemo_bill(package=30000), disch())
    for who, level, note in (("Asha", "junior", "checked with hospital"), ("Ravi", "senior", "ok")):
        with pytest.raises(ValueError):
            cpa.resolve(rec["id"], "approve", who, level, note)
    out = cpa.resolve(rec["id"], "approve", "Ravi", "senior", "Hospital supplied the extra implant invoice")
    assert out["status"] == "settled" and out["reconciliation"]["settledBy"] == "Ravi"


def test_settled_session_counts_as_history_for_the_next_check():
    rec = ask("Rajesh Sharma", "Apollo Hospitals", "Haemodialysis session", date="2026-10-27")
    bill = {"billNumber": "AP-1", "totalStated": 2800, "lines": [{"description": "Haemodialysis package", "category": "procedure", "qty": 1, "rate": 2800, "amount": 2800}]}
    out = cpa.reconcile(rec["id"], bill, {"admissionDate": "2026-10-27", "dischargeDate": "2026-10-27", "roomCategory": "general_ward", "roomDays": 0})
    assert out["status"] == "settled"
    again = ask("Rajesh Sharma", "Apollo Hospitals", "Haemodialysis session", date="2026-10-27")
    assert again["status"] == "standard_review" and "SESSION_IN_PATTERN" in failed(again)


def test_api_roles_see_the_right_things():
    r = api.post("/api/hospital/Apollo Hospitals/claim-preapproval", json={"policy": RAJ["policy"], "procedure": "Haemodialysis session", "serviceDate": "2026-11-02",
                                                                         "roomCategory": "general_ward", "roomDays": 0, "contactLast4": RAJ["registeredContactMasked"][-4:]}).json()
    assert r["state"] == "preapproved" and r["guaranteed"] == 2660 and "eligibility" not in r
    assert api.post(f"/api/hospital/Max Super Speciality Hospital/claim-preapproval/{r['id']}/bill", json={}).status_code in (404, 422)
    home = api.get(f"/api/patient/{RAJ['policy']}").json()
    assert home["preapprovals"][0]["guaranteed"] == 2660 and "eligibility" not in str(home)
    assert any(x["id"] == r["id"] and x["eligibility"] for x in api.get("/api/adjudicator/fast-track").json())
    assert TestClient(server.app).get("/api/adjudicator/fast-track").status_code == 401
