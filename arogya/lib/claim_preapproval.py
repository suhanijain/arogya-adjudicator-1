"""Claim pre-approval for regular treatment, and the light reconciliation after it.

Before treatment: a network hospital asks for approval. If the patient is eligible, the amount is assessed from the
hospital's contracted rates (linked provider database) and guaranteed at once.
After treatment: the itemised bill and discharge summary are used for ONE job: move the amount up or down.
  * room days are limited to what the discharge summary documents, and the room rate to the contracted and policy limits
  * non-payable items come off; the rest of the bill must stay close to the assessed package
  * a 5% co-payment applies (Clause 9.5)
Anything that cannot be settled by that simple check (arithmetic errors, duplicate bill, room above the policy cap,
bill far above the package, upward move over 25%) goes to an adjudicator with the proposed amount. It is never rejected automatically.
Eligibility: a regular treatment pattern, usual network hospital with no review flag, no fraud history, no open hold, enough cover.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from ..data.synthetic_claims import get_claim
from . import patient_360 as p360
from . import provider_db
from .adjudication_calculator import COPAY_RATE, ROOM_CAP_MAX, ROOM_CAP_PCT, is_non_payable, rupee
from .claims_db import claims_db, now
from .claims_metrics import ADJUSTMENT, PREAPPROVAL
from .fraud_detector import detect_fraud
from .network_360 import network_overview
from .preauth import preauth

NONROOM_TOLERANCE = 0.10      # bill (excluding room) may exceed the assessed package by this much
UPWARD_LIMIT = 0.25           # final payable may exceed the pre-approved payable by this much without a person
COVER_BUFFER = 1.2            # cover left must be at least this multiple of the assessed amount
WINDOW_DAYS = 7               # treatment must start within this many days of the planned date
POLICY = "fast-track-policy-v1"


def r(x) -> int:
    return int(rupee(Decimal(str(x))))


def room_cap(p: dict) -> int:
    return r(min(ROOM_CAP_PCT * Decimal(p["sumInsured"]), ROOM_CAP_MAX))


def copay(total: int) -> int:
    return r(Decimal(total) * COPAY_RATE)


class ClaimPreApprovals:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.items: dict[str, dict] = {}

    # ---- before treatment ---------------------------------------------------------------------
    def assess(self, p: dict, hospital: str, procedure: str, room_category: str, room_days: int) -> dict | None:
        pkg, rate = provider_db.package_rate(hospital, procedure), provider_db.room_rate(hospital, room_category) if room_days else 0
        if pkg is None or rate is None:
            return None
        rate_used = min(rate, room_cap(p))
        total = pkg + rate_used * room_days
        return {"package": pkg, "roomCategory": room_category, "roomDays": room_days, "roomRateContracted": rate, "roomRateAssessed": rate_used, "roomTotal": rate_used * room_days,
                "total": total, "copay": copay(total), "payable": total - copay(total), "ratesSyncedAt": provider_db.synced_at()}

    def eligibility(self, p: dict, hospital: str, procedure: str, service_date: str, contact_id: str, assessed: dict | None, room_cap_ok: bool) -> list[dict]:
        policy, checks = p["policy"], []

        def chk(code, ok, text):
            checks.append({"code": code, "ok": bool(ok), "text": text})

        prior = [*p["episodes"], *preauth.accepted.get(policy, [])]
        reg = next((x for x in p360.regular_treatments(prior) if x["procedure"] == procedure), None)
        chk("REGULAR_TREATMENT", reg, f"Regular {procedure}: {reg['episodes']} sessions on record." if reg else f"No regular {procedure} pattern in the history (needs at least {p360.MIN_EPISODES} steady sessions).")
        chk("USUAL_HOSPITAL", reg and hospital in reg["usualHospitals"], f"{hospital} is the patient's usual hospital for this treatment." if reg and hospital in reg["usualHospitals"] else f"{hospital} is not the patient's usual hospital for this treatment.")
        net = next((n for n in network_overview() if n["hospital"] == hospital), None)
        # pricing and claim-flag reasons only: one patient's odd sessions are not the hospital's fault
        why = [t for t in (net["reasons"] if net else []) if "own pattern" not in t]
        chk("NETWORK_HOSPITAL", net and not why, f"{hospital} is a network hospital with no pricing or claim-flag review." if net and not why else f"{hospital} is under review or not in the network: {'; '.join(why) or 'not in the network'}.")
        bad = [t for t in p360.patient_360(policy)["timeline"] if t["flags"]]
        chk("NO_PAST_FLAGS", not bad, "No past session was out of the patient's pattern." if not bad else f"{len(bad)} past session(s) were out of the patient's pattern.")
        claim = get_claim(p["claimId"])
        fl = [f for f in detect_fraud(claim).flags if f.severity != "low"] if claim else []
        chk("NO_FRAUD_HISTORY", not fl, "No fraud flag on the patient's claim record." if not fl else f"Claim record carries flag(s): {', '.join(f.type.replace('_', ' ') for f in fl)}.")
        held = [x for x in preauth.requests.values() if x["policy"] == policy and x["status"] in ("held", "awaiting") and x["flags"]]
        rej = [x for x in [*preauth.requests.values(), *self.items.values()] if x["policy"] == policy and x["status"] == "rejected"]
        chk("NO_OPEN_CASE", not held and not rej, "No open hold and no rejected request." if not held and not rej else "There is an open hold or a rejected request on this policy.")
        flags = [f for f in p360.check_session(p, prior, hospital, procedure, service_date, assessed["package"] if assessed else 0, contact_id) if f["severity"] != "low"]
        if assessed and reg:
            chk("AMOUNT_USUAL", assessed["package"] <= reg["typicalAmount"] * p360.AMOUNT_TOLERANCE, f"Assessed package ₹{assessed['package']:,} is within 1.25x of the patient's usual ₹{int(reg['typicalAmount']):,}." if assessed["package"] <= reg["typicalAmount"] * p360.AMOUNT_TOLERANCE else f"Assessed package ₹{assessed['package']:,} is above 1.25x the patient's usual ₹{int(reg['typicalAmount']):,}.")
        chk("SESSION_IN_PATTERN", not flags, "Timing, contact and price match the patient's pattern." if not flags else " ".join(f["text"] for f in flags))
        chk("RATES_FOUND", assessed, "Contracted rates found in the provider database." if assessed else "No contracted rate for this treatment or room category.")
        chk("ROOM_WITHIN_CAP", room_cap_ok, "Room rate is within the policy room-rent cap." if room_cap_ok else "Contracted room rate is above the policy room-rent cap (a proportional cut would apply).")
        need = int((assessed["total"] if assessed else 0) * COVER_BUFFER)
        left = preauth.cover_left(policy)
        chk("COVER", assessed and left >= need and p360.d(service_date).isoformat() <= p["policyValidTo"], f"Cover left ₹{left:,} is at least {COVER_BUFFER}x the assessed amount (₹{need:,} needed) and the policy runs to {p['policyValidTo']}." if assessed and left >= need else f"Cover left ₹{left:,} is below the ₹{need:,} needed, or the policy ends before the treatment.")
        return checks

    def request(self, hospital: str, policy: str, procedure: str, service_date: str, room_category: str, room_days: int, contact_last4: str) -> dict:
        p = p360.get_patient(policy)
        if not p or hospital not in p360.HOSPITALS:
            raise ValueError("unknown policy or hospital")
        if room_days < 0 or room_days > 7:
            raise ValueError("room days must be between 0 and 7")
        contact = p["registeredContactId"] if contact_last4 == p["registeredContactMasked"][-4:] else f"C-{contact_last4}"
        provider_rate = provider_db.room_rate(hospital, room_category) if room_days else 0
        cap_ok = provider_rate is None or provider_rate <= room_cap(p)
        assessed = self.assess(p, hospital, procedure, room_category, room_days) if cap_ok or provider_rate is not None else None
        checks = self.eligibility(p, hospital, procedure, service_date, contact, assessed, cap_ok)
        ok = all(c["ok"] for c in checks)
        rid = f"CPA-{secrets.token_hex(3).upper()}"
        rec = {"id": rid, "policy": policy, "patient": p["name"], "hospital": hospital, "procedure": procedure, "serviceDate": service_date, "roomCategory": room_category,
               "expectedRoomDays": room_days, "contactId": contact, "status": "preapproved" if ok else "standard_review", "eligibility": checks, "assessed": assessed,
               "token": None, "termsHash": None, "reconciliation": None, "createdAt": now(), "resolution": None}
        if ok:
            rec["termsHash"] = hashlib.sha256(f"{rid}|{policy}|{hospital}|{procedure}|{service_date}|{assessed['total']}|{assessed['payable']}".encode()).hexdigest()[:24]
            rec["token"] = claims_db.issue_token(rid, rec["termsHash"], POLICY, "system")
        self.items[rid] = rec
        PREAPPROVAL.labels(rec["status"]).inc()
        return rec

    # ---- after treatment ----------------------------------------------------------------------
    def reconcile(self, rid: str, bill: dict, discharge: dict) -> dict:
        rec = self.items.get(rid)
        if not rec:
            raise KeyError("unknown pre-approval")
        if rec["status"] != "preapproved":
            raise ValueError(f"pre-approval is {rec['status']}; a bill can only be reconciled against an active pre-approval")
        p, a = p360.get_patient(rec["policy"]), rec["assessed"]
        adj: list[dict] = []
        exits: list[str] = []

        def note(kind, code, amount, text, clause=""):
            adj.append({"kind": kind, "code": code, "amount": int(amount), "text": text, "clause": clause})

        lines = bill["lines"]
        if any(r(Decimal(str(l["qty"])) * Decimal(str(l["rate"]))) != r(l["amount"]) for l in lines):
            exits.append("A bill line's quantity x rate does not equal its amount.")
        stated, line_sum = r(bill["totalStated"]), sum(r(l["amount"]) for l in lines)
        if stated != line_sum:
            exits.append(f"Bill total ₹{stated:,} differs from the sum of its lines ₹{line_sum:,}.")
        if claims_db.is_bill_duplicate(bill["billNumber"]):
            exits.append(f"Bill number {bill['billNumber']} was already settled.")
        if not (p360.d(rec["serviceDate"]) - timedelta(days=WINDOW_DAYS) <= p360.d(discharge["admissionDate"]) <= p360.d(rec["serviceDate"]) + timedelta(days=WINDOW_DAYS)):
            exits.append(f"Admission {discharge['admissionDate']} is more than {WINDOW_DAYS} days from the planned date {rec['serviceDate']}.")
        ds_days, nights = int(discharge["roomDays"]), (p360.d(discharge["dischargeDate"]) - p360.d(discharge["admissionDate"])).days
        if ds_days > 0 and ds_days != max(1, nights):
            exits.append(f"Discharge summary says {ds_days} room day(s) but its dates show {max(1, nights)}.")

        # room charges: days from the discharge summary, rate limited by the contract and the policy cap
        room_lines = [l for l in lines if l["category"] == "room"]
        billed_days = sum(Decimal(str(l["qty"])) for l in room_lines)
        billed_rate = max((r(l["rate"]) for l in room_lines), default=0)
        contract = provider_db.room_rate(rec["hospital"], discharge["roomCategory"]) or 0
        cap = room_cap(p)
        if billed_rate > cap:
            exits.append(f"Room rent ₹{billed_rate:,}/day is above the policy cap ₹{cap:,}/day: the other charges must be reduced proportionally (Clause 4.1 Note b).")
        days_ok = int(min(billed_days, ds_days))
        if billed_days > ds_days:
            note("down", "ROOM_DAYS", (int(billed_days) - ds_days) * billed_rate, f"Bill charges {int(billed_days)} room day(s); the discharge summary documents {ds_days}.", "Clause 4.1.a")
        rate_ok = min(billed_rate, contract, cap) if room_lines else 0
        if room_lines and billed_rate > rate_ok and billed_rate <= cap:
            note("down", "ROOM_RATE", (billed_rate - rate_ok) * days_ok, f"Billed ₹{billed_rate:,}/day; the hospital's contracted {discharge['roomCategory'].replace('_', ' ')} rate is ₹{contract:,}/day.", "Network agreement")
        room_allowed = days_ok * rate_ok
        if days_ok > rec["expectedRoomDays"]:
            note("up", "EXTRA_ROOM_DAYS", (days_ok - rec["expectedRoomDays"]) * rate_ok, f"{days_ok} room day(s) documented in the discharge summary against {rec['expectedRoomDays']} pre-approved.", "Clause 4.1.a")

        # everything else: payable items only, and close to the assessed package
        other = [l for l in lines if l["category"] != "room"]
        np_amt = sum(r(l["amount"]) for l in other if is_non_payable(l["description"]))
        if np_amt:
            note("down", "NON_PAYABLE", np_amt, "Non-payable items removed: " + ", ".join(l["description"] for l in other if is_non_payable(l["description"])) + ".", "Clause 4.7")
        other_ok = sum(r(l["amount"]) for l in other if not is_non_payable(l["description"]))
        ceiling = r(a["package"] * (1 + NONROOM_TOLERANCE))
        if other_ok > ceiling:
            exits.append(f"Charges other than room are ₹{other_ok:,}, more than {int(NONROOM_TOLERANCE * 100)}% above the assessed package ₹{a['package']:,}.")
        if other_ok < a["package"]:
            note("down", "BELOW_PACKAGE", a["package"] - other_ok, f"Charges other than room are ₹{other_ok:,}, below the assessed package ₹{a['package']:,}.")
        elif other_ok > a["package"]:
            note("up", "ABOVE_PACKAGE", other_ok - a["package"], f"Charges other than room are ₹{other_ok:,}, above the assessed package ₹{a['package']:,} but within {int(NONROOM_TOLERANCE * 100)}%.")

        total = room_allowed + other_ok
        payable = min(total - copay(total), preauth.cover_left(rec["policy"]))
        delta = payable - a["payable"]
        if payable > a["payable"] * (1 + UPWARD_LIMIT):
            exits.append(f"Payable ₹{payable:,} is more than {int(UPWARD_LIMIT * 100)}% above the pre-approved ₹{a['payable']:,}.")
        direction = "up" if delta > 0 else "down" if delta < 0 else "none"
        rec["reconciliation"] = {"billNumber": bill["billNumber"], "billedTotal": stated, "roomAllowed": room_allowed, "otherAllowed": other_ok, "total": total, "copay": copay(total),
                                 "payable": payable, "preapprovedPayable": a["payable"], "delta": delta, "direction": direction, "adjustments": adj, "exits": exits, "at": now(),
                                 "discharge": discharge}
        if exits:
            rec["status"] = "adjudication"
            PREAPPROVAL.labels("adjudication").inc()
        else:
            self._settle(rec, POLICY, "system")
        return rec

    def _settle(self, rec: dict, approver: str, level: str) -> None:
        rc, p = rec["reconciliation"], p360.get_patient(rec["policy"])
        h = hashlib.sha256(f"{rec['id']}|{rc['payable']}|{rc['billNumber']}".encode()).hexdigest()[:24]
        token = claims_db.issue_token(rec["id"], h, approver, level)
        res = claims_db.settle_claim(caller="Settlement", claim_id=rec["id"], policy_no=rec["policy"], claimant=p["name"], expected_name=p["name"], token=token, ws_hash=h,
                                     amount=Decimal(rc["payable"]), bill_no=rc["billNumber"])
        if not res["success"]:
            raise ValueError(res["error"])
        rec["status"] = "settled"
        rc["settlementId"], rc["settledBy"], rc["level"] = res["settlementId"], approver, level
        preauth.record_episode({"id": rec["id"], "policy": rec["policy"], "serviceDate": rc["discharge"]["admissionDate"], "hospital": rec["hospital"], "procedure": rec["procedure"],
                                "amount": rc["billedTotal"], "paid": rc["payable"], "contactId": rec["contactId"]})
        if rc["delta"]:
            ADJUSTMENT.labels(rc["direction"]).inc(abs(rc["delta"]))
        PREAPPROVAL.labels("settled").inc()

    # ---- adjudicator ----------------------------------------------------------------------------
    def resolve(self, rid: str, action: str, approver: str, level: str, note: str) -> dict:
        rec = self.items.get(rid)
        if not rec:
            raise KeyError("unknown pre-approval")
        if rec["status"] != "adjudication":
            raise ValueError(f"nothing to decide: status is {rec['status']}")
        if not approver.strip():
            raise ValueError("a named approver is required")
        if action == "approve":
            if level != "senior" or len(note.strip()) < 10:
                raise ValueError("a senior approver and a note (at least 10 characters) explaining the decision are required")
            self._settle(rec, approver, level)
        elif action == "reject":
            if not note.strip():
                raise ValueError("a reason is required to reject")
            rec["status"] = "rejected"
        else:
            raise ValueError("action must be approve or reject")
        rec["resolution"] = {"action": action, "by": approver, "level": level, "note": note, "at": now()}
        claims_db.log_decision(rid, f"fast_track_{action}", approver, level)
        return rec

    # ---- views ----------------------------------------------------------------------------------
    def public(self, rec: dict) -> dict:
        a, rc = rec["assessed"], rec["reconciliation"]
        status = {"preapproved": "Pre-approved", "standard_review": "Standard review", "adjudication": "Final amount being checked", "settled": "Settled", "rejected": "Not approved: contact your insurer"}[rec["status"]]
        out = {"id": rec["id"], "hospital": rec["hospital"], "procedure": rec["procedure"], "serviceDate": rec["serviceDate"], "status": status, "state": rec["status"]}
        if rec["status"] in ("preapproved", "settled", "adjudication"):
            out["guaranteed"] = a["payable"]
        if rec["status"] == "settled":
            out.update(settled=rc["payable"], change=[{"direction": x["kind"], "amount": x["amount"], "text": x["text"]} for x in rc["adjustments"]])
        return out

    def for_policy(self, policy):
        return [self.public(x) for x in self.items.values() if x["policy"] == policy]

    def for_hospital(self, hospital):
        return [{**self.public(x), "patient": x["patient"], "policy": x["policy"]} for x in self.items.values() if x["hospital"] == hospital]


claim_preapprovals = ClaimPreApprovals()
