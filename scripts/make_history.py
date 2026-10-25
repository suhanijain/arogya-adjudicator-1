"""Builds arogya/data/patient_history.json: synthetic past treatments for 12 patients plus pending pre-auth requests.
Seeded, so the file is reproducible. Run: python scripts/make_history.py
All people, contacts and amounts are invented."""
import json
import random
from datetime import date, timedelta
from pathlib import Path

R = random.Random(2026)
AS_OF = date(2026, 10, 26)
claims = json.loads((Path(__file__).parent.parent / "arogya/data/synthetic_claims.json").read_text())
HOSP = {}
for c in claims:
    HOSP[c["hospitalName"]] = {"city": c["hospitalCity"], "tier": c["cityTier"]}

PROC = {"dialysis": ("Haemodialysis session", "Nephrology", 2800, 2200, 1800),
        "chemo": ("Chemotherapy cycle (day care)", "Oncology", 30000, 25000, 20000),
        "physio": ("Physiotherapy session", "Rehabilitation", 1000, 800, 600),
        "cataract": ("Cataract Phacoemulsification with Foldable IOL", "Ophthalmology", 35000, 26000, 20000),
        "hernia": ("Inguinal Hernioplasty (Mesh Repair)", "General Surgery", 58000, 42000, 32000),
        "gastro": ("Inpatient Medical Management & IV Rehydration", "Internal Medicine", 30000, 22000, 16000)}
PRICE = {"Sterling Hospital": {"dialysis": 2.1}}      # the planted network outlier


def price(kind, hosp):
    base = PROC[kind][2 + ["tier1", "tier2", "tier3"].index(HOSP[hosp]["tier"])]
    return round(base * PRICE.get(hosp, {}).get(kind, 1.0) * R.uniform(0.95, 1.05) / 10) * 10


def days(start, end, weekdays):
    d = start
    while d <= end:
        if d.weekday() in weekdays:
            yield d
        d += timedelta(days=1)


def ep(n, d, hosp, kind, contact, by="hospital", amount=None):
    amt = amount or price(kind, hosp)
    return {"id": f"EP-{n:04d}", "date": d.isoformat(), "hospital": hosp, "city": HOSP[hosp]["city"], "procedure": PROC[kind][0], "category": PROC[kind][1],
            "billed": amt, "paid": round(amt * 0.95), "status": "settled", "channel": "cashless", "submittedBy": by, "contactId": contact}


patients, requests, n = [], [], 0
SCEN = [  # index in claims, kind, usual hospital, pending request overrides
    (0, "dialysis", "Apollo Hospitals", {}),
    (1, "dialysis", "Fortis Memorial Research Institute", {"hospital": "Apex District Hospital"}),
    (2, "dialysis", "Max Super Speciality Hospital", {"mult": 2.4}),
    (3, "dialysis", "Manipal Hospital", {"contact": "C-NEW1"}),
    (4, "dialysis", "Ruby Hall Clinic", {"date": date(2026, 10, 26), "extra_same_day": True}),
    (5, "dialysis", "Sterling Hospital", {}),
    (6, "chemo", "Narayana Multispeciality Hospital", {"date": date(2026, 11, 2)}),
    (7, "physio", "Apollo Reach Hospital", {"hospital": "Apollo Reach Hospital"}),
    (8, "dialysis", "Apex District Hospital", {}),
    (9, "dialysis", "Sterling Hospital", {}),
    (10, "occasional", "Apollo Hospitals", {"kind": "dialysis"}),
    (11, "occasional", "Fortis Memorial Research Institute", {}),
]
rid = 1000
for idx, kind, usual, ov in SCEN:
    c = claims[idx]
    contact = f"C-{idx + 1:02d}"
    eps = []
    if kind == "dialysis":
        for d in days(date(2026, 7, 20), date(2026, 10, 23), {0, 2, 4}):
            n += 1; eps.append(ep(n, d, usual, "dialysis", contact))
    elif kind == "chemo":
        d = date(2026, 5, 11)
        while d <= date(2026, 10, 12):
            n += 1; eps.append(ep(n, d, usual, "chemo", contact)); d += timedelta(days=21)
    elif kind == "physio":
        for d in days(date(2026, 8, 18), date(2026, 10, 22), {1, 3}):
            n += 1
            odd = d in (date(2026, 10, 6), date(2026, 10, 8))       # two sessions at another hospital, from a new phone
            eps.append(ep(n, d, "Narayana Multispeciality Hospital" if odd else usual, "physio", "C-NEW9" if odd else contact, "patient" if odd else "hospital"))
    else:
        for k, (when, what) in enumerate([(date(2025, 11, 4), "cataract"), (date(2026, 3, 18), "gastro")] if idx == 10 else [(date(2026, 2, 9), "hernia")]):
            n += 1; eps.append(ep(n, when, usual, what, contact))
    rkind = ov.get("kind", kind if kind in PROC else "dialysis")
    rhosp = ov.get("hospital", usual)
    amt = price(rkind, rhosp)
    if "mult" in ov:
        amt = round(amt * ov["mult"] / 10) * 10
    rdate = ov.get("date", AS_OF)
    if ov.get("extra_same_day"):
        n += 1; eps.append(ep(n, AS_OF, usual, "dialysis", contact))     # already dialysed this morning
    rid += 1
    requests.append({"id": f"PA-{rid}", "policy": c["policyNumber"], "hospital": rhosp, "procedure": PROC[rkind][0], "serviceDate": rdate.isoformat(),
                     "amount": amt, "contactId": ov.get("contact", contact), "submittedBy": "hospital"})
    patients.append({"policy": c["policyNumber"], "name": c["insuredName"], "age": c["patientAge"], "gender": c["patientGender"], "sumInsured": c["sumInsured"],
                     "cumulativeBonus": c["cumulativeBonus"], "homeCity": HOSP[usual]["city"], "claimId": c["claimId"], "policyValidTo": "2027-03-31",
                     "registeredContactId": contact, "registeredContactMasked": f"XXXXXX{R.randint(1000, 9999)}", "episodes": eps})
out = {"asOf": AS_OF.isoformat(), "hospitals": HOSP, "patients": patients, "requests": requests}
Path(__file__).parent.parent.joinpath("arogya/data/patient_history.json").write_text(json.dumps(out, indent=1))
print(len(patients), sum(len(p["episodes"]) for p in patients), len(requests))
