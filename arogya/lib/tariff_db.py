"""Procedure tariff benchmarks per city tier. Mirrors src/lib/tariffDb.ts.

Illustrative ranges from the original app, not a published rate card.
"""
import json
from decimal import Decimal
from pathlib import Path

DATA = json.loads((Path(__file__).parent.parent / "data" / "tariffs.json").read_text())
KEYS = {"cataract": "cataract", "hernia": "hernia", "cholecyst": "cholecyst", "knee": "knee", "gastro": "gastro",
        "append": "append", "tympan": "tympan", "robotic": "robotic", "ayush": "ayush"}
DEFAULT = {"min": 25000, "max": 65000, "standardAvg": 42000}
EXCESS_PCT = 30      # flag when the claim exceeds the tier ceiling by more than this


def lookup_benchmark(procedure: str, tier: str) -> dict:
    p = procedure.lower()
    for row in DATA:
        name = row["procedureName"].lower()
        if p in name or name in p or any(k in p and k in name for k in KEYS):
            return {**row["benchmarks"][tier], "found": True}
    return {**DEFAULT, "found": False}


def evaluate_tariff(procedure: str, tier: str, claimed_procedure_total: Decimal) -> dict:
    b = lookup_benchmark(procedure, tier)
    over = int(((claimed_procedure_total - b["max"]) / b["max"] * 100).to_integral_value()) if claimed_procedure_total > b["max"] else 0
    return {"benchmarkMin": b["min"], "benchmarkMax": b["max"], "claimedProcedureAmount": claimed_procedure_total,
            "variancePercent": over, "isExcessive": over > EXCESS_PCT, "benchmarkFound": b["found"]}
