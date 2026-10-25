"""Pre-authorisation for ongoing treatment (dialysis, chemotherapy, physiotherapy).

Idea: an adjudicator approves a *standing authorisation* once (sessions, hospitals, per-session cap, end date).
After that each session is checked against the patient's own pattern and the authorisation, and clears in seconds
when it matches. Anything out of pattern stops and goes to a person with the reasons. The system never rejects
anyone on its own: the worst it does is hold a request for a person.
"""
from __future__ import annotations

import math
import secrets
from datetime import timedelta

from . import patient_360 as p360
from .claims_db import claims_db, now
from .claims_metrics import PREAUTH

# what the patient and the hospital see (no reasons: they could be used to learn how to avoid a check)
PUBLIC = {"cleared": "Approved", "approved": "Approved", "awaiting": "Being reviewed", "held": "Being reviewed: we will call the registered number", "rejected": "Not approved: contact your insurer"}
SENIOR_NOTE_MIN = 10


class PreAuth:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.requests: dict[str, dict] = {}
        self.standing: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.accepted: dict[str, list[dict]] = {}          # sessions already approved in this app, counted as history for the next check
        for r in p360.DATA["requests"]:
            self.submit(dict(r))

    # ---- submit -------------------------------------------------------------------------
    def _prior(self, policy: str) -> list[dict]:
        return [*p360.get_patient(policy)["episodes"], *self.accepted.get(policy, [])]

    def recommend(self, policy: str, procedure: str) -> dict | None:
        reg = next((r for r in p360.regular_treatments(self._prior(policy)) if r["procedure"] == procedure), None)
        p = p360.get_patient(policy)
        if not reg or not p:
            return None
        sessions = max(2, round(30 / reg["typicalGapDays"]))          # at least two, so monthly chemotherapy is worth a standing approval
        days = max(30, math.ceil(sessions * reg["typicalGapDays"]))
        return {"policy": policy, "procedure": procedure, "hospitals": reg["usualHospitals"], "perSessionCap": math.ceil(reg["typicalAmount"] * p360.AMOUNT_TOLERANCE / 100) * 100,
                "sessions": sessions, "validTo": min((p360.AS_OF + timedelta(days=days)).isoformat(), p["policyValidTo"])}

    def _active_auth(self, policy, procedure, hospital, service_date, amount):
        return next((a for a in self.standing.values() if a["policy"] == policy and a["procedure"] == procedure and not a["revoked"] and hospital in a["hospitals"]
                     and a["sessionsLeft"] > 0 and service_date <= a["validTo"] and amount <= a["perSessionCap"]), None)

    def submit(self, req: dict) -> dict:
        p = p360.get_patient(req["policy"])
        if not p:
            raise ValueError("unknown policy")
        req.setdefault("id", f"PA-{secrets.token_hex(3).upper()}")
        prior = self._prior(req["policy"])
        flags = p360.check_session(p, prior, req["hospital"], req["procedure"], req["serviceDate"], req["amount"], req["contactId"])
        if self.cover_left(req["policy"]) < req["amount"]:
            flags.append({"code": "COVER_LOW", "severity": "high", "text": "Remaining sum insured is lower than this session's amount."})
        worst = max((p360.SEV[f["severity"]] for f in flags), default=0)
        rec = self.recommend(req["policy"], req["procedure"])
        auth = self._active_auth(req["policy"], req["procedure"], req["hospital"], req["serviceDate"], req["amount"])
        if worst >= 3:
            status, why = "held", "Out of the patient's pattern"
        elif worst == 2:
            status, why = "awaiting", "Differs from the patient's pattern"
        elif auth:
            status, why = "cleared", f"Within standing authorisation {auth['id']}"
            auth["sessionsLeft"] -= 1
            self._accept(req)
        elif rec:
            status, why = "awaiting", "Regular treatment, no standing authorisation yet: one approval can cover the next sessions"
        else:
            status, why = "awaiting", "No treatment history to compare with: standard review"
        rec_ = {**req, "status": status, "why": why, "flags": flags, "recommendation": rec if status == "awaiting" and worst == 0 and rec else None,
                "standingAuthId": auth["id"] if status == "cleared" and auth else None, "submittedAt": now(), "resolution": None}
        self.requests[rec_["id"]] = rec_
        PREAUTH.labels(status).inc()
        return rec_

    def _accept(self, r: dict) -> None:
        p = p360.get_patient(r["policy"])
        self.accepted.setdefault(r["policy"], []).append({"id": r["id"], "date": r["serviceDate"], "hospital": r["hospital"], "city": p360.HOSPITALS[r["hospital"]]["city"],
                                                          "procedure": r["procedure"], "billed": r["amount"], "paid": r.get("paid", round(r["amount"] * 0.95)), "status": "settled",
                                                          "channel": "cashless", "submittedBy": r.get("submittedBy", "hospital"), "contactId": r["contactId"], "category": ""})

    def cover_left(self, policy: str) -> int:
        return p360.remaining_cover(p360.get_patient(policy), self.accepted.get(policy, []))

    def record_episode(self, r: dict) -> None:
        """A treatment settled elsewhere (claim pre-approval) becomes history for the next pattern check."""
        self._accept(r)

    # ---- adjudicator actions ----------------------------------------------------------------
    def _log(self, action: str, who: str, level: str, ref: str, note: str = "") -> None:
        self.audit.append({"at": now(), "action": action, "approver": who, "level": level, "ref": ref, "note": note})
        claims_db.log_decision(ref, action, who, level)

    def _open(self, rid: str) -> dict:
        r = self.requests.get(rid)
        if not r:
            raise KeyError("unknown request")
        if r["status"] not in ("awaiting", "held"):
            raise ValueError(f"request is already {r['status']}")
        return r

    def resolve(self, rid: str, action: str, approver: str, level: str, note: str = "") -> dict:
        r = self._open(rid)
        if not approver.strip():
            raise ValueError("a named approver is required")
        if action == "approve" and r["status"] == "held" and (level != "senior" or len(note.strip()) < SENIOR_NOTE_MIN):
            raise ValueError("a held request needs a senior approver and a note saying how the patient was verified on the registered number")
        if action == "approve":
            r["status"] = "approved"; self._accept(r)
        elif action == "reject":
            if not note.strip():
                raise ValueError("a reason is required to reject")
            r["status"] = "rejected"
        else:
            raise ValueError("action must be approve or reject")
        r["resolution"] = {"action": action, "by": approver, "level": level, "note": note, "at": now()}
        self._log(f"preauth_{action}", approver, level, rid, note)
        PREAUTH.labels(r["status"]).inc()
        return r

    def grant_standing(self, rid: str, approver: str, level: str, terms: dict | None = None) -> dict:
        r = self._open(rid)
        if not approver.strip():
            raise ValueError("a named approver is required")
        if r["status"] == "held" or any(f["severity"] == "high" for f in r["flags"]):
            raise ValueError("a standing authorisation cannot be granted from a request that is out of pattern")
        rec = self.recommend(r["policy"], r["procedure"])
        if not rec:
            raise ValueError("no regular treatment pattern to authorise")
        t = {**rec, **(terms or {})}
        if t["perSessionCap"] > rec["perSessionCap"] or t["validTo"] > rec["validTo"] or t["sessions"] > rec["sessions"] or not set(t["hospitals"]) <= set(rec["hospitals"]):
            raise ValueError("terms cannot be wider than the recommendation (cap, end date, sessions, hospitals)")
        if r["hospital"] not in t["hospitals"] or r["amount"] > t["perSessionCap"]:
            raise ValueError("the request that triggered this must fit inside the terms")
        a = {"id": f"SA-{secrets.token_hex(3).upper()}", "policy": r["policy"], "procedure": r["procedure"], "hospitals": t["hospitals"], "perSessionCap": t["perSessionCap"],
             "sessionsLeft": t["sessions"] - 1, "sessionsGranted": t["sessions"], "validTo": t["validTo"], "approver": approver, "level": level, "revoked": False, "grantedAt": now(),
             "token": claims_db.issue_token(r["policy"], f"{r['procedure']}|{t['perSessionCap']}|{t['validTo']}|{t['sessions']}", approver, level)}
        self.standing[a["id"]] = a
        r["status"] = "cleared"; r["standingAuthId"] = a["id"]; r["resolution"] = {"action": "grant_standing", "by": approver, "level": level, "note": "", "at": now()}
        self._accept(r)
        self._log("standing_granted", approver, level, a["id"])
        PREAUTH.labels("standing_granted").inc()
        return a

    def revoke(self, auth_id: str, approver: str, reason: str) -> dict:
        a = self.standing.get(auth_id)
        if not a:
            raise KeyError("unknown authorisation")
        if not approver.strip() or not reason.strip():
            raise ValueError("a named approver and a reason are required")
        a["revoked"] = True
        self._log("standing_revoked", approver, "any", auth_id, reason)
        return a

    # ---- views ----------------------------------------------------------------------------
    def public(self, r: dict) -> dict:
        return {"id": r["id"], "hospital": r["hospital"], "procedure": r["procedure"], "serviceDate": r["serviceDate"], "amount": r["amount"], "status": PUBLIC[r["status"]], "standingAuthId": r.get("standingAuthId")}

    def for_policy(self, policy: str) -> list[dict]:
        return [self.public(r) for r in self.requests.values() if r["policy"] == policy]

    def for_hospital(self, hospital: str) -> list[dict]:
        return [{**self.public(r), "policy": r["policy"]} for r in self.requests.values() if r["hospital"] == hospital]

    def queue(self) -> list[dict]:
        order = {"held": 0, "awaiting": 1}
        rows = [r for r in self.requests.values() if r["status"] in order]
        return sorted(rows, key=lambda r: (order[r["status"]], r["id"]))


preauth = PreAuth()
