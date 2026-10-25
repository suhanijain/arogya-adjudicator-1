"""80 synthetic claims (40 dev, 40 held-out). Mirrors src/data/syntheticClaims.ts.

The TypeScript file generates them; here the generator's output is stored as JSON (same camelCase fields),
so the dataset is fixed and reviewable. Each claim carries its four raw OCR-style documents.
"""
import json
from functools import lru_cache
from pathlib import Path

from ..types.adjudication import ExtractedClaim

DATA = Path(__file__).parent


@lru_cache
def load_claims() -> list[ExtractedClaim]:
    return [ExtractedClaim.model_validate(c) for c in json.loads((DATA / "synthetic_claims.json").read_text())]


def get_claim(claim_id: str) -> ExtractedClaim | None:
    return next((c for c in load_claims() if c.claim_id == claim_id), None)
