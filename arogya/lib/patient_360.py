"""Patient 360: past treatments and payments, regular treatments, and out-of-pattern checks.

A regular treatment (dialysis, chemotherapy, physiotherapy) is found from the history itself: at least 4 episodes
of one procedure with a steady gap. A new session is then compared with that pattern: same hospital, same city,
similar amount, sensible gap, known contact. Each difference is a reason a person can read, not a score.
A difference is a prompt to check with the patient, not an accusation: people do change hospitals and phones.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import median

from .tariff_db import evaluate_tariff
from decimal import Decimal

DATA = json.loads((Path(__file__).parent.parent / "data" / "patient_history.json").read_text())
AS_OF = date.fromisoformat(DATA["asOf"])             # the demo's "today", so results are reproducible
HOSPITALS: dict[str, dict] = DATA["hospitals"]
MIN_EPISODES, AMOUNT_TOLERANCE = 4, 1.25
SEV = {"high": 3, "medium": 2, "low": 1}


def patients() -> list[dict]:
    return DATA["patients"]


def get_patient(policy: str) -> dict | None:
    return next((p for p in patients() if p["policy"] == policy), None)


def d(s: str) -> date:
    return date.fromisoformat(s)


def regular_treatments(episodes: list[dict]) -> list[dict]:
    """Procedures repeated often enough, at a steady rhythm, to count as ongoing treatment."""
    out = []
    for proc in sorted({e["procedure"] for e in episodes}):
        es = sorted((e for e in episodes if e["procedure"] == proc), key=lambda e: e["date"])
        if len(es) < MIN_EPISODES:
            continue
        gaps = [(d(b["date"]) - d(a["date"])).days for a, b in zip(es, es[1:]) if (d(b["date"]) - d(a["date"])).days > 0]
        if not gaps:
            continue
        mg = round(sum(gaps) / len(gaps), 1)       # mean, so Mon/Wed/Fri (2,2,3) and Tue/Thu (2,5) both read as steady
        steady = sum(abs(g - mg) <= max(1, mg * 0.6) for g in gaps) / len(gaps) >= 0.8
        if mg > 35 or not steady:
            continue
        recent = es[-12:]
        hosp_share = {h: sum(e["hospital"] == h for e in es) / len(es) for h in {e["hospital"] for e in es}}
        out.append({"procedure": proc, "episodes": len(es), "typicalGapDays": mg, "typicalAmount": median(e["billed"] for e in recent),
                    "usualHospitals": sorted((h for h, s in hosp_share.items() if s >= 0.3), key=lambda h: -hosp_share[h]),
                    "hospitalShare": {h: round(s, 2) for h, s in hosp_share.items()}, "usualCities": sorted({e["city"] for e in es if hosp_share[e["hospital"]] >= 0.3}),
                    "firstDate": es[0]["date"], "lastDate": es[-1]["date"], "lastHospital": es[-1]["hospital"]})
    return out


def check_session(p: dict, prior: list[dict], hospital: str, procedure: str, service_date: str, amount: int, contact_id: str, *, tariff: bool = True) -> list[dict]:
    """Reasons this session differs from the patient's own pattern. Empty list means it looks like their usual treatment."""
    reg = next((r for r in regular_treatments(prior) if r["procedure"] == procedure), None)
    flags: list[dict] = []

    def add(code, sev, text):
        flags.append({"code": code, "severity": sev, "text": text})

    if contact_id != p["registeredContactId"]:
        add("NEW_CONTACT", "high", f"Submitted from a contact that is not the registered number ({p['registeredContactMasked']}). Confirm with the patient on the registered number: this could be a changed phone or someone else using the account.")
    if hospital not in HOSPITALS:
        add("NOT_IN_NETWORK", "high", f"{hospital} is not a network hospital.")
    if reg:
        if hospital not in reg["usualHospitals"]:
            city = HOSPITALS.get(hospital, {}).get("city", "")
            add("NEW_HOSPITAL", "medium", f"Usually treated at {', '.join(reg['usualHospitals'])}; this request is from {hospital}.")
            if city and city not in reg["usualCities"]:
                add("OTHER_CITY", "high", f"{hospital} is in {city}; the patient's regular sessions are in {', '.join(reg['usualCities'])}.")
        if amount > reg["typicalAmount"] * AMOUNT_TOLERANCE:
            sev = "high" if amount > reg["typicalAmount"] * 2 else "medium"
            add("AMOUNT_HIGH", sev, f"₹{amount:,} is {amount / reg['typicalAmount']:.1f}x the patient's usual ₹{int(reg['typicalAmount']):,} per session.")
        last = max(e["date"] for e in prior if e["procedure"] == procedure)
        gap = (d(service_date) - d(last)).days
        if gap < max(1, reg["typicalGapDays"] * 0.5):
            when = f"on the same day as the last session ({last})" if gap == 0 else f"only {gap} day(s) after the last session on {last}"
            add("TOO_SOON", "high", f"Requested {when}; the usual gap is {reg['typicalGapDays']:g} days.")
        elif gap > reg["typicalGapDays"] * 3:
            add("LONG_GAP", "low", f"{gap} days since the last session; usual gap is {reg['typicalGapDays']:g}. The patient may have been unwell or treated elsewhere.")
    tier = HOSPITALS.get(hospital, {}).get("tier")
    if tier and tariff:      # hospital pricing is judged in Network 360 for past sessions; it matters for new requests
        t = evaluate_tariff(procedure, tier, Decimal(amount))
        if t["isExcessive"] and t["benchmarkFound"]:
            add("TARIFF_HIGH", "medium", f"₹{amount:,} is {t['variancePercent']}% above the {tier} benchmark ceiling of ₹{t['benchmarkMax']:,}.")
    return flags


def remaining_cover(p: dict, extra: list[dict] | None = None) -> int:
    start = AS_OF.replace(month=4, day=1) if AS_OF.month >= 4 else AS_OF.replace(year=AS_OF.year - 1, month=4, day=1)
    used = sum(e["paid"] for e in [*p["episodes"], *(extra or [])] if d(e["date"]) >= start and e["status"] == "settled")
    return int(p["sumInsured"]) + int(p["cumulativeBonus"]) - used


def patient_360(policy: str) -> dict | None:
    p = get_patient(policy)
    if not p:
        return None
    eps = sorted(p["episodes"], key=lambda e: e["date"])
    timeline = []
    for i, e in enumerate(eps):
        flags = check_session(p, eps[:i], e["hospital"], e["procedure"], e["date"], e["billed"], e["contactId"], tariff=False) if i else []
        timeline.append({**e, "flags": flags})
    regs = regular_treatments(eps)
    by_hosp: dict[str, dict] = {}
    for e in eps:
        h = by_hosp.setdefault(e["hospital"], {"hospital": e["hospital"], "city": e["city"], "episodes": 0, "billed": 0, "paid": 0})
        h["episodes"] += 1; h["billed"] += e["billed"]; h["paid"] += e["paid"]
    return {"policy": p["policy"], "name": p["name"], "age": p["age"], "gender": p["gender"], "homeCity": p["homeCity"], "claimId": p["claimId"],
            "registeredContact": p["registeredContactMasked"], "policyValidTo": p["policyValidTo"], "sumInsured": p["sumInsured"] + p["cumulativeBonus"],
            "remainingCover": remaining_cover(p), "totalBilled": sum(e["billed"] for e in eps), "totalPaid": sum(e["paid"] for e in eps),
            "regularTreatments": regs, "hospitals": sorted(by_hosp.values(), key=lambda h: -h["episodes"]), "timeline": timeline,
            "pastFlags": sum(bool(t["flags"]) for t in timeline)}
