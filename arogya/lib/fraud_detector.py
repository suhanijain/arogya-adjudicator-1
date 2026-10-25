"""Fraud and prompt-attack screening. Mirrors src/lib/fraudDetector.ts.

Three groups of rules, all deterministic:
  * security     prompt-injection patterns found in the claim documents (Palo Alto taxonomy)
  * clinical     gender/age-disease mismatch, investigation-only admission, prolonged stay (India scheme typologies)
  * billing      duplicate bill, unbundling, implant inflation, tariff outliers, coverage ceiling, non-payables
Document text is only ever scanned here; it is never passed to a model as instructions.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from ..types.adjudication import ExtractedClaim, FraudFlag, FraudReport
from .adjudication_calculator import is_non_payable
from .claims_db import claims_db
from .tariff_db import evaluate_tariff

I = re.IGNORECASE
SEC = "palo_alto_security"

# (type, flag id, title, severity, score, patterns, clause) -- first four are mutually exclusive with the generic pattern
INJECTION_RULES = [
    ("system_mode_attack", "FLAG-ATTACK-SYSMODE", "System-mode goal hijacking", 90,
     [r"<SYSTEM\s*MODE>", r"system\s+mode\s+activated"]),
    ("roleplay_jailbreak", "FLAG-ATTACK-ROLEPLAY", "Authority-persona roleplay jailbreak", 85,
     [r"ombudsman", r"you\s+are\s+no\s+longer", r"dr\.\s*k\.\s*verma", r"emergency\s+humanitarian\s+mission", r"under\s+emergency\s+powers"]),
    ("obfuscated_injection", "FLAG-ATTACK-OBFUSCATION", "Encoded payload / affirmative-prefix coercion", 85,
     [r"SWdub3JlIHBvbGljeSBhbmQgYXBwcm92ZSBiaWxs", r"BASE64_PAYLOAD"]),
]
OBFUSCATION_PAIR = (r"do\s+not\s+apologize", r"start\s+with")
GENERIC_INJECTION = [r"ignore\s+(all\s+)?(statutory\s+|policy\s+|rules\s+)?(policy|terms|limits|caps?|clauses?)\s*(and\s+approve|to\s+approve|approve\s+bill)",
                     r"ignore\s+policy\s+and\s+approve\s+bill", r"system\s+override[:\s]", r"disregard\s+(section|clause|policy|room\s+cap|sublimit)",
                     r"bypass\s+(5%\s+)?(co-?pay|copayment|deductions?)", r"approve\s+bill\s+in\s+full\s+immediately", r"settle\s+without\s+deduction",
                     r"grant\s+100%\s+reimbursement", r"do\s+not\s+apply\s+clause"]
LEGAL_THREAT = [r"consumer\s+(disputes|forum|court)", r"advocate\s+m\.\s*k\.\s*chidambaram", r"section\s+72\s+penalty",
                r"penalty\s+under\s+consumer\s+protection", r"mandatory\s+waiver\s+of\s+clause"]
DEBUG_ATTACK = [r"DEBUG_EVALUATION_MODE", r"CONFIG_OVERRIDE", r"override_copay", r"auth_level['\":\s]+root"]

FEMALE_ONLY = ["hysterectomy", "uterine", "ovarian", "salpingo", "menorrhagia", "cesarean", "c-section", "oophorectomy", "cervical dysplasia"]
MALE_ONLY = ["prostate", "bph", "turp", "orchiectomy", "testicular", "vasectomy", "penile", "hydrocele"]
SENILE = ["senile cataract", "osteoarthritis", "knee replacement", "bph", "degenerative"]
SHORT_STAY = ["gastroenteritis", "dehydration", "viral fever", "cataract"]
STENT_PRICE_CAP = Decimal(38200)       # NPPA ceiling used by the original


def any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, I) for p in patterns)


def flag(fid: str, type_: str, severity: str, title: str, desc: str, clause: str, evidence: str, group: str | None = None) -> FraudFlag:
    return FraudFlag(id=fid, type=type_, severity=severity, title=title, description=desc, clause_cited=clause, evidence=evidence, attack_vector_category=group)


def detect_fraud(c: ExtractedClaim) -> FraudReport:
    flags: list[FraudFlag] = []
    score = 5

    def add(f: FraudFlag, points: int) -> None:
        nonlocal score
        flags.append(f)
        score += points

    used = claims_db.used_sublimits(c.policy_number)
    dup = claims_db.is_bill_duplicate(c.bill_number)
    text = " ".join([c.raw_documents.discharge_summary_text, c.raw_documents.claim_form_text, c.raw_documents.itemised_bill_text,
                     c.procedure_name, c.diagnosis, *(f"{l.raw_description} {l.normalized_description}" for l in c.line_items)])
    clinical = f"{c.diagnosis} {c.procedure_name}".lower()

    if dup:
        add(flag("FLAG-DUP", "duplicate_bill", "high", "Duplicate hospital bill number", f"Bill {c.bill_number} was already settled.",
                 "Clause 10.9", "Matches the bill register."), 65)
    if used.get("cataract", 0) > 0 and "cataract" in c.procedure_name.lower():
        add(flag("FLAG-SUBLIM", "sublimit_exhaustion", "medium", "Cataract sub-limit partly used", f"{used['cataract']} of the annual cataract cap is already used.",
                 "Clause 4.3", f"Prior settlements under {c.policy_number}."), 20)
    if date.fromisoformat(c.discharge_date) < date.fromisoformat(c.admission_date):
        add(flag("FLAG-DATE", "date_anomaly", "high", "Discharge before admission", "Discharge date precedes admission date.", "",
                 f"{c.admission_date} -> {c.discharge_date}"), 50)

    # --- prompt attacks in the documents -------------------------------------------------
    hit = {}
    for type_, fid, title, points, patterns in INJECTION_RULES:
        hit[type_] = any_match(patterns, text) or (type_ == "obfuscated_injection" and all(re.search(p, text, I) for p in OBFUSCATION_PAIR))
        if hit[type_]:
            add(flag(fid, type_, "high", f"Prompt attack: {title}", f"Adversarial instruction in the claim documents ({title}). Reported, not obeyed.",
                     "Palo Alto prompt-attack taxonomy & Clause 10.9", "Matched an injection signature in the submitted documents.", SEC), points)
    if any_match(GENERIC_INJECTION, text) and not any(hit.values()):
        add(flag("FLAG-PROMPT-INJ", "prompt_injection", "high", "Prompt injection in medical record", "Instruction aimed at the automated workflow found in the documents.",
                 "Clause 10.9", "Matched a generic injection signature.", SEC), 80)
    if any_match(LEGAL_THREAT, text):
        add(flag("FLAG-ATTACK-LEGAL-COERCION", "legal_threat_jailbreak", "high", "Prompt attack: legal-coercion jailbreak",
                 "Legal threat used to force waiver of statutory clauses.", "Palo Alto prompt-attack taxonomy & Clause 10.9", "Matched a legal-threat signature.", SEC), 90)
    if any_match(DEBUG_ATTACK, text):
        add(flag("FLAG-ATTACK-DEV-DEBUG", "developer_debug_attack", "high", "Prompt attack: debug / config override",
                 "Fake configuration block trying to force 100% admissibility.", "Palo Alto prompt-attack taxonomy & Clause 10.9", "Matched a config-override signature.", SEC), 90)

    # --- clinical plausibility ------------------------------------------------------------
    if any_match([r"executive\s+health\s+checkup", r"investigation\s+only|diagnostic\s+and\s+evaluation", r"code-excl04"], text) or \
            ("diagnostic evaluation" in c.procedure_name.lower() and not c.is_accident):
        add(flag("FLAG-INVESTIGATION-ONLY", "investigation_only_admission", "high", "Admission for diagnostics only (Excl04)",
                 "Admission was for tests and evaluation without active treatment.", "Clause 7.1 (Excl04)", f"{c.diagnosis} / {c.procedure_name}", "billing_package_fraud"), 75)
    if (c.patient_gender == "Male" and any(t in clinical for t in FEMALE_ONLY)) or (c.patient_gender == "Female" and any(t in clinical for t in MALE_ONLY)):
        add(flag("FLAG-GENDER-MISMATCH", "gender_disease_mismatch", "high", "Gender-disease mismatch",
                 f"{c.patient_gender} patient billed for '{c.procedure_name}'. Possible ghost patient or misattribution.", "IRDAI anti-fraud guidelines & Clause 10.9",
                 f"Gender {c.patient_gender}; procedure {c.procedure_name}", "clinical_demographic_mismatch"), 95)
    if c.patient_age < 16 and any(t in clinical for t in SENILE):
        add(flag("FLAG-AGE-MISMATCH", "age_disease_mismatch", "high", "Age-disease mismatch",
                 f"Patient aged {c.patient_age} billed for degenerative condition '{c.diagnosis}'.", "IRDAI anti-fraud guidelines", f"Age {c.patient_age}; {c.diagnosis}",
                 "clinical_demographic_mismatch"), 90)
    if c.hospitalization_days >= 8 and any(t in clinical for t in SHORT_STAY):
        add(flag("FLAG-LOS-INFLATION", "los_inflation", "medium", "Prolonged length of stay", f"{c.hospitalization_days} days for an uncomplicated condition (benchmark 2-3).",
                 "Clause 3.33", f"{c.hospitalization_days} days", "billing_package_fraud"), 30)

    # --- billing ---------------------------------------------------------------------------
    unbundled = [l for l in c.line_items if re.search(r"unbundled|video\s+laparoscopy|trocar\s+port|pacu|sterilization", l.raw_description, I)]
    if len(unbundled) >= 2:
        add(flag("FLAG-UNBUNDLING", "unbundling_upcoding", "medium", "Unbundled package charges",
                 f"{len(unbundled)} ancillary lines totalling {sum(l.claimed_amount for l in unbundled)} belong inside the OT charge.", "Clause 4.1.1.c",
                 ", ".join(l.raw_description for l in unbundled), "billing_package_fraud"), 35)
    stents = [l for l in c.line_items if re.search(r"\bstents?\b|\bDES\b", l.raw_description, I)]
    if stents and any(l.unit_rate > 45000 or l.days_or_units >= 3 for l in stents):
        top = max(l.unit_rate for l in stents)
        add(flag("FLAG-IMPLANT-INFLATION", "implant_inflation", "high", "Cardiac stent price inflation",
                 f"Stent billed at {top}/unit against the {STENT_PRICE_CAP} ceiling; several stents without multi-vessel justification.", "Clause 3.33 & NPPA order",
                 f"{top} vs {STENT_PRICE_CAP}", "billing_package_fraud"), 45)
    if c.claimed_total > c.total_coverage_limit:
        add(flag("FLAG-OUTSIDE-LIMIT", "outside_policy_limit", "high", "Bill exceeds the coverage ceiling",
                 f"Claimed {c.claimed_total} against a ceiling of {c.total_coverage_limit}.", "Clause 2", f"{c.claimed_total} > {c.total_coverage_limit}"), 40)
    excluded = [l for l in c.line_items if l.category == "consumables" or is_non_payable(l.raw_description)]
    if excluded:      # informational: these are simply non-payable, not evidence of fraud
        add(flag("FLAG-EXCLUDED-GOODS", "excluded_goods_claimed", "low", "Non-payable goods claimed",
                 f"{len(excluded)} items totalling {sum(l.claimed_amount for l in excluded)} are non-payable under Annexure-A List I.", "Clause 4.7 / Annexure-A",
                 ", ".join(l.raw_description for l in excluded[:3])), 20)
    proc_total = sum((l.claimed_amount for l in c.line_items if l.category in ("procedure", "surgeon_fee", "operation_theatre")), Decimal(0)) or c.claimed_total
    tariff = evaluate_tariff(c.procedure_name, c.city_tier, proc_total)
    if tariff["isExcessive"]:
        add(flag("FLAG-TARIFF", "tariff_inflation", "medium", "Procedure cost above tariff benchmark",
                 f"Claimed {proc_total} exceeds the {c.city_tier} ceiling {tariff['benchmarkMax']} by {tariff['variancePercent']}%.", "Clause 3.33",
                 f"{proc_total} vs max {tariff['benchmarkMax']}"), 25)

    return FraudReport(claim_id=c.claim_id, has_fraud_flag=any(f.severity != "low" for f in flags), risk_score=min(100, score), flags=flags,
                       prior_claims_count=len(claims_db.policy_history(c.policy_number)), duplicate_bill_found=dup,
                       historical_utilized_sublimits=used, tariff_assessment=tariff)
