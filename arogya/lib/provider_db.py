"""Linked network-provider database: contracted package and room rates per hospital.
Reads a synced file here; a live system would call each provider's rate API and keep the sync time."""
import json
from pathlib import Path

DB = json.loads((Path(__file__).parent.parent / "data" / "provider_rates.json").read_text())
ROOM_LABEL = {"general_ward": "General ward", "semi_private": "Semi-private", "private": "Private", "deluxe": "Deluxe"}


def provider(hospital: str) -> dict | None:
    return DB["hospitals"].get(hospital)


def package_rate(hospital: str, procedure: str) -> int | None:
    p = provider(hospital)
    return p["packages"].get(procedure) if p else None


def room_rate(hospital: str, category: str) -> int | None:
    p = provider(hospital)
    return p["rooms"].get(category) if p else None


def synced_at() -> str:
    return DB["syncedAt"]
