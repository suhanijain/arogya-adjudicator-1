"""Claims ledger, bill register, approval tokens and the gated settle_claim tool.

Mirrors src/lib/claimsDb.ts. In-memory; replace the dicts with a database for production.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from decimal import Decimal

SECRET = os.getenv("CLAIMS_APPROVAL_SECRET") or secrets.token_hex(16)   # set it, or tokens die with the process


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClaimsDatabase:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.history: dict[str, list[dict]] = {}
        self.seen_bills: set[str] = set()
        self.settled: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.decisions: list[dict] = []          # every adjudicator action, for edit-rate metrics
        for rec in SEED_HISTORY:
            self.record(rec)

    # ---- ledger -------------------------------------------------------------------------
    def record(self, rec: dict) -> None:
        self.history.setdefault(rec["policyNumber"], []).append(rec)
        self.seen_bills.add(rec["billNumber"].strip().upper())

    def policy_history(self, policy_no: str) -> list[dict]:
        return self.history.get(policy_no, [])

    def is_bill_duplicate(self, bill_no: str) -> bool:
        return bool(bill_no) and bill_no.strip().upper() in self.seen_bills

    def used_sublimits(self, policy_no: str) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for r in self.policy_history(policy_no):
            if r["status"] == "settled":
                for k, v in r["sublimitsUtilized"].items():
                    totals[k] = totals.get(k, Decimal(0)) + Decimal(str(v))
        return totals

    # ---- approval tokens ----------------------------------------------------------------
    def issue_token(self, claim_id: str, ws_hash: str, approver: str, level: str) -> str:
        sig = hmac.new(SECRET.encode(), f"{claim_id}|{ws_hash}|{approver}|{level}".encode(), hashlib.sha256).hexdigest()[:20]
        token = f"TOKEN-APP-{level[:3].upper()}-{sig}"
        self.tokens[token] = {"claim_id": claim_id, "ws_hash": ws_hash, "approver": approver, "level": level, "at": now()}
        return token

    def token_valid(self, token: str | None, claim_id: str, ws_hash: str) -> bool:
        rec = self.tokens.get(token or "")
        return bool(rec) and rec["claim_id"] == claim_id and rec["ws_hash"] == ws_hash

    # ---- the gated tool -------------------------------------------------------------------
    def settle_claim(self, *, caller: str, claim_id: str, policy_no: str, claimant: str, expected_name: str,
                     token: str | None, ws_hash: str, amount: Decimal, bill_no: str, sublimits_used: dict | None = None) -> dict:
        """Only the Settlement agent may call this, with a token bound to this exact worksheet hash."""
        def deny(why: str) -> dict:
            return {"success": False, "error": why}
        if caller != "Settlement":
            return deny(f"ACCESS DENIED: settle_claim is gated to the Settlement agent (caller: {caller}).")
        if not self.token_valid(token, claim_id, ws_hash):
            return deny("INVALID APPROVAL: no valid approval token for this worksheet version (an edit voids earlier approvals).")
        if claimant.strip().lower() != expected_name.strip().lower():
            return deny(f"GUARDRAIL: claimant '{claimant}' does not match policy record '{expected_name}'.")
        if claim_id in self.settled:
            return deny("ALREADY SETTLED: this claim was settled earlier.")
        sid = f"SETTLE-{secrets.token_hex(4).upper()}"
        self.settled[claim_id] = {"settlement_id": sid, "token": token, "ws_hash": ws_hash, "at": now()}
        self.record({"claimId": claim_id, "policyNumber": policy_no, "insuredName": claimant, "billNumber": bill_no, "settledAmount": float(amount),
                     "sublimitsUtilized": sublimits_used or {}, "settledDate": now()[:10], "status": "settled"})
        return {"success": True, "settlementId": sid}

    def log_decision(self, claim_id: str, action: str, approver: str, level: str, lines_edited: int = 0) -> None:
        self.decisions.append({"claim_id": claim_id, "action": action, "approver": approver, "level": level, "lines_edited": lines_edited, "at": now()})


SEED_HISTORY = [
    {"claimId": "CLM-HIST-101", "policyNumber": "NIA-AS-2024-88410", "insuredName": "Rajesh Sharma", "billNumber": "INV-MAX-9921",
     "settledAmount": 25000, "sublimitsUtilized": {"cataract": 25000}, "settledDate": "2024-03-20", "status": "settled"},
    {"claimId": "CLM-HIST-102", "policyNumber": "NIA-AS-2024-55192", "insuredName": "Pooja Iyer", "billNumber": "BILL-APOLLO-44810",
     "settledAmount": 38000, "sublimitsUtilized": {"ambulance": 1500}, "settledDate": "2024-05-15", "status": "settled"},
    {"claimId": "CLM-HIST-103", "policyNumber": "NIA-AS-2024-11823", "insuredName": "Sunil Verma", "billNumber": "FORTIS-DUP-7711",
     "settledAmount": 60000, "sublimitsUtilized": {}, "settledDate": "2024-06-25", "status": "settled"},
]

claims_db = ClaimsDatabase()
