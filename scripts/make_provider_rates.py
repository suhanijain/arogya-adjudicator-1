"""Builds arogya/data/provider_rates.json: what each network hospital has contracted for packages and rooms.
Stands in for the insurer's linked provider database (a live version would call each provider's rate API).
Run: python scripts/make_provider_rates.py"""
import json
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).parent.parent / "arogya/data"
tariffs = {t["procedureName"]: t["benchmarks"] for t in json.loads((root / "tariffs.json").read_text())}
hist = json.loads((root / "patient_history.json").read_text())
ROOM = {"general_ward": 2000, "semi_private": 3500, "private": 5500, "deluxe": 9000}
TIER_FACTOR = {"tier1": 1.0, "tier2": 0.8, "tier3": 0.6}
MULT = {("Sterling Hospital", "Haemodialysis session"): 2.1}      # same planted outlier as the history
r50 = lambda x: int(round(x / 50) * 50)

out = {"syncedAt": "2026-10-26T02:00:00Z", "source": "network provider rate files (simulated sync)", "hospitals": {}}
for name, h in hist["hospitals"].items():
    out["hospitals"][name] = {
        "city": h["city"], "tier": h["tier"],
        "packages": {proc: r50(b[h["tier"]]["standardAvg"] * MULT.get((name, proc), 1.0)) for proc, b in tariffs.items() if proc in
                     ("Haemodialysis session", "Chemotherapy cycle (day care)", "Physiotherapy session")},
        "rooms": {k: r50(v * TIER_FACTOR[h["tier"]]) for k, v in ROOM.items()}}
(root / "provider_rates.json").write_text(json.dumps(out, indent=1))
print(len(out["hospitals"]))
