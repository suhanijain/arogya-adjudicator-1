"""Network 360: how each hospital behaves, from its claims and treatment history. Flags a hospital for review, never a verdict."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from statistics import median

from ..data.synthetic_claims import load_claims
from .adjudication_calculator import calculate_worksheet
from .fraud_detector import detect_fraud
from .patient_360 import AS_OF, HOSPITALS, check_session, d, patients
from .tariff_db import lookup_benchmark


def _episodes():
    for p in patients():
        eps = sorted(p["episodes"], key=lambda e: e["date"])
        for i, e in enumerate(eps):
            yield p, e, (check_session(p, eps[:i], e["hospital"], e["procedure"], e["date"], e["billed"], e["contactId"], tariff=False) if i else [])


def network_360(hospital: str) -> dict | None:
    if hospital not in HOSPITALS:
        return None
    h = HOSPITALS[hospital]
    mine = [(p, e, f) for p, e, f in _episodes() if e["hospital"] == hospital]
    ratios = [e["billed"] / lookup_benchmark(e["procedure"], h["tier"])["standardAvg"] for _, e, _ in mine if lookup_benchmark(e["procedure"], h["tier"])["found"]]
    claims = [c for c in load_claims() if c.hospital_name == hospital]
    reports = [detect_fraud(c) for c in claims]
    sheets = [calculate_worksheet(c) for c in claims]
    months = Counter(e["date"][:7] for _, e, _ in mine)
    out_of_city = sum(p["homeCity"] != h["city"] for p, _, _ in mine)
    flagged = sum(bool(f) for _, _, f in mine)
    return {"hospital": hospital, "city": h["city"], "tier": h["tier"],
            "claims": len(claims), "claimedTotal": sum((c.claimed_total for c in claims), Decimal(0)),
            "flagRate": round(sum(r.has_fraud_flag for r in reports) / len(claims), 2) if claims else 0,
            "repudiationRate": round(sum(not w.is_admissible for w in sheets) / len(claims), 2) if claims else 0,
            "flagTypes": dict(Counter(f.type for r in reports for f in r.flags)),
            "treatments": len(mine), "patients": len({p["policy"] for p, _, _ in mine}),
            "billingVsBenchmark": round(median(ratios), 2) if ratios else None,
            "outOfCityShare": round(out_of_city / len(mine), 2) if mine else 0,
            "outOfPatternShare": round(flagged / len(mine), 2) if mine else 0,
            "topProcedures": Counter(e["procedure"] for _, e, _ in mine).most_common(4),
            "monthly": sorted(months.items())}


def network_overview() -> list[dict]:
    rows = [network_360(h) for h in HOSPITALS]
    peer_flag = median(r["flagRate"] for r in rows)
    for r in rows:
        why = []
        if r["billingVsBenchmark"] and r["billingVsBenchmark"] > 1.3:
            why.append(f"Bills {r['billingVsBenchmark']}x the benchmark for the same treatments")
        if r["flagRate"] - peer_flag >= 0.3:
            why.append(f"{int(r['flagRate'] * 100)}% of its {r['claims']} claims carry a flag (peers: {int(peer_flag * 100)}%)")
        if r["outOfPatternShare"] >= 0.15:
            why.append(f"{int(r['outOfPatternShare'] * 100)}% of treatments differ from the patient's own pattern")
        r["status"] = "review" if why else "normal"
        r["reasons"] = why
    return sorted(rows, key=lambda r: (r["status"] != "review", -(r["billingVsBenchmark"] or 0)))
