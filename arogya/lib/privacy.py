"""Masking of health details in traces (the original's Requirement 8), with the regex-escaping bug fixed."""
import re
from contextvars import ContextVar

from ..types.adjudication import ExtractedClaim

_PHI: ContextVar[list[tuple[str, str]]] = ContextVar("phi", default=[])


def set_phi(c: ExtractedClaim) -> None:
    pairs = [(c.insured_name, "[PATIENT]"), (c.policy_number, "[POLICY_NO]"), (c.diagnosis, "[DIAGNOSIS]"), (c.icd10_code, "[ICD]"), (c.procedure_name, "[PROCEDURE]")]
    _PHI.set([(v, tag) for v, tag in pairs if v])


def mask(text: str) -> str:
    for value, tag in _PHI.get():
        text = re.sub(re.escape(value), tag, text, flags=re.I)
    return text
