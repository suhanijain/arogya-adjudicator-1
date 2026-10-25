"""Patient, hospital and adjudicator routes. Patients and hospitals see simple statuses; only adjudicators see reasons.
Demo access control: patients are identified by policy number, hospitals by name, adjudicators by an API key
(ADJUDICATOR_KEY, default "demo-key"). A real deployment needs proper sign-in (OTP for patients, SSO for staff)."""
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from arogya.lib import agent_orchestrator as orch
from arogya.lib import provider_db
from arogya.lib.claim_preapproval import claim_preapprovals as cpa
from arogya.lib.network_360 import network_360, network_overview
from arogya.lib.patient_360 import AS_OF, HOSPITALS, get_patient, patient_360, patients, regular_treatments, remaining_cover
from arogya.lib.preauth import preauth


def adjudicator(x_adjudicator_key: str = Header(default="")):
    if not secrets.compare_digest(x_adjudicator_key, os.getenv("ADJUDICATOR_KEY", "demo-key")):
        raise HTTPException(401, "adjudicator key required")


patient_api, hospital_api = APIRouter(prefix="/api/patient"), APIRouter(prefix="/api/hospital")
adj_api = APIRouter(prefix="/api/adjudicator", dependencies=[Depends(adjudicator)])


def known(policy: str) -> dict:
    p = get_patient(policy)
    if not p:
        raise HTTPException(404, "unknown policy")
    return p


# ---- patient ----------------------------------------------------------------------------------
@patient_api.get("")
def patient_list():
    return [{"policy": p["policy"], "name": p["name"]} for p in patients()]


@patient_api.get("/{policy}")
def patient_home(policy: str):
    p = known(policy)
    eps = sorted(p["episodes"], key=lambda e: e["date"])
    regs = regular_treatments(eps)
    auths = [a for a in preauth.standing.values() if a["policy"] == policy and not a["revoked"]]
    claim = orch.snapshot(p["claimId"])
    return {"name": p["name"], "policy": policy, "validTo": p["policyValidTo"], "registeredContact": p["registeredContactMasked"], "remainingCover": preauth.cover_left(policy),
            "preapprovals": cpa.for_policy(policy),
            "coverTotal": p["sumInsured"] + p["cumulativeBonus"],
            "ongoingTreatments": [{"procedure": r["procedure"], "usualHospital": r["usualHospitals"][0], "everyDays": r["typicalGapDays"], "lastSession": r["lastDate"], "sessions": r["episodes"],
                                   "recentDates": [e["date"] for e in eps if e["procedure"] == r["procedure"]][-16:]} for r in regs],
            "standingApprovals": [{"id": a["id"], "procedure": a["procedure"], "hospitals": a["hospitals"], "sessionsLeft": a["sessionsLeft"], "validTo": a["validTo"]} for a in auths],
            "requests": preauth.for_policy(policy),
            "recent": [{k: e[k] for k in ("date", "hospital", "procedure", "billed", "paid", "status")} for e in eps[-10:][::-1]],
            "claim": {"claimId": p["claimId"], "status": claim["status"], "outcome": claim.get("outcome")}}


# ---- hospital ---------------------------------------------------------------------------------
class SessionRequest(BaseModel):
    policy: str
    procedure: str
    serviceDate: str
    amount: int
    contactLast4: str


@hospital_api.get("")
def hospital_list():
    return sorted(HOSPITALS)


@hospital_api.get("/{name}")
def hospital_home(name: str):
    if name not in HOSPITALS:
        raise HTTPException(404, "unknown hospital")
    n = network_360(name)
    return {"hospital": name, "city": n["city"], "requests": preauth.for_hospital(name), "claimPreapprovals": cpa.for_hospital(name), "rates": provider_db.provider(name), "ratesSyncedAt": provider_db.synced_at(),
            "scorecard": {k: n[k] for k in ("treatments", "patients", "billingVsBenchmark", "claims")}}


@hospital_api.post("/{name}/preauth")
def hospital_submit(name: str, r: SessionRequest):
    if name not in HOSPITALS:
        raise HTTPException(404, "unknown hospital")
    p = known(r.policy)
    contact = p["registeredContactId"] if r.contactLast4 == p["registeredContactMasked"][-4:] else f"C-{r.contactLast4}"
    rec = preauth.submit({"policy": r.policy, "hospital": name, "procedure": r.procedure, "serviceDate": r.serviceDate, "amount": r.amount, "contactId": contact, "submittedBy": "hospital"})
    return preauth.public(rec)


class PreApprovalRequest(BaseModel):
    policy: str
    procedure: str
    serviceDate: str
    roomCategory: str = "general_ward"
    roomDays: int = 0
    contactLast4: str


class BillLine(BaseModel):
    description: str
    category: str
    qty: float
    rate: float
    amount: float


class Bill(BaseModel):
    billNumber: str
    totalStated: float
    lines: list[BillLine]


class Discharge(BaseModel):
    admissionDate: str
    dischargeDate: str
    roomCategory: str
    roomDays: int


class BillSubmission(BaseModel):
    bill: Bill
    discharge: Discharge


@hospital_api.post("/{name}/claim-preapproval")
def claim_preapproval(name: str, r: PreApprovalRequest):
    if name not in HOSPITALS:
        raise HTTPException(404, "unknown hospital")
    known(r.policy)
    return cpa.public(guarded(cpa.request, name, r.policy, r.procedure, r.serviceDate, r.roomCategory, r.roomDays, r.contactLast4))


@hospital_api.post("/{name}/claim-preapproval/{rid}/bill")
def claim_bill(name: str, rid: str, b: BillSubmission):
    rec = cpa.items.get(rid)
    if not rec or rec["hospital"] != name:
        raise HTTPException(404, "unknown pre-approval for this hospital")
    return cpa.public(guarded(cpa.reconcile, rid, b.bill.model_dump(), b.discharge.model_dump()))


# ---- adjudicator ------------------------------------------------------------------------------
class Act(BaseModel):
    approver: str
    level: str = "junior"
    note: str = ""
    terms: dict | None = None


def guarded(fn, *a, **k):
    try:
        return fn(*a, **k)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@adj_api.get("/queue")
def queue():
    out = []
    for r in preauth.queue():
        p = get_patient(r["policy"])
        out.append({**r, "patient": p["name"], "homeCity": p["homeCity"], "hospitalCity": HOSPITALS[r["hospital"]]["city"]})
    return {"asOf": AS_OF.isoformat(), "items": out}


@adj_api.get("/patients/{policy}")
def adj_patient(policy: str):
    known(policy)
    return {**{**patient_360(policy), "remainingCover": preauth.cover_left(policy)}, "standing": [a for a in preauth.standing.values() if a["policy"] == policy],
            "requests": [r for r in preauth.requests.values() if r["policy"] == policy]}


@adj_api.get("/network")
def adj_network():
    return network_overview()


@adj_api.get("/network/{hospital}")
def adj_hospital(hospital: str):
    n = network_360(hospital)
    if not n:
        raise HTTPException(404, "unknown hospital")
    return {**n, "requests": [r for r in preauth.requests.values() if r["hospital"] == hospital]}


@adj_api.post("/preauth/{rid}/approve")
def approve(rid: str, a: Act):
    return guarded(preauth.resolve, rid, "approve", a.approver, a.level, a.note)


@adj_api.post("/preauth/{rid}/reject")
def reject(rid: str, a: Act):
    return guarded(preauth.resolve, rid, "reject", a.approver, a.level, a.note)


@adj_api.post("/preauth/{rid}/standing")
def standing(rid: str, a: Act):
    return guarded(preauth.grant_standing, rid, a.approver, a.level, a.terms)


@adj_api.post("/standing/{aid}/revoke")
def revoke(aid: str, a: Act):
    return guarded(preauth.revoke, aid, a.approver, a.note)


@adj_api.get("/fast-track")
def fast_track():
    order = {"adjudication": 0, "standard_review": 1, "preapproved": 2, "settled": 3, "rejected": 4}
    return sorted(cpa.items.values(), key=lambda x: (order[x["status"]], x["createdAt"]))


@adj_api.post("/fast-track/{rid}/approve")
def ft_approve(rid: str, a: Act):
    return guarded(cpa.resolve, rid, "approve", a.approver, a.level, a.note)


@adj_api.post("/fast-track/{rid}/reject")
def ft_reject(rid: str, a: Act):
    return guarded(cpa.resolve, rid, "reject", a.approver, a.level, a.note)
