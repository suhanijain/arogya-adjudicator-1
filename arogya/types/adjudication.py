"""Data model. Mirrors src/types/adjudication.ts.

Fields are snake_case in Python and camelCase on the wire (same JSON as the TypeScript app),
so the data files and API responses stay compatible with the original React front end.
Money is Decimal; the calculator rounds to whole rupees like the original.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

CityTier = Literal["tier1", "tier2", "tier3"]
BillCategory = Literal[
    "room", "icu", "doctor_consultation", "surgeon_fee", "operation_theatre", "medicines",
    "investigation_diagnostics", "consumables", "ambulance", "nursing", "procedure", "other",
]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DocumentSourceSpan(Model):
    document: Literal["claim_form", "discharge_summary", "itemised_bill", "policy_schedule"]
    line_number: int
    snippet: str
    confidence: float = 1.0


class BillItem(Model):
    id: str
    raw_description: str
    normalized_description: str = ""
    category: BillCategory
    claimed_amount: Decimal
    days_or_units: Decimal = Decimal(1)
    unit_rate: Decimal
    source_span: Optional[DocumentSourceSpan] = None


class RawDocuments(Model):
    claim_form_text: str = ""
    discharge_summary_text: str = ""
    itemised_bill_text: str = ""
    policy_schedule_text: str = ""


class ExtractedClaim(Model):
    claim_id: str
    policy_number: str
    insured_name: str
    patient_age: int
    patient_gender: Literal["Male", "Female", "Other"]
    sum_insured: Decimal
    cumulative_bonus: Decimal = Decimal(0)
    total_coverage_limit: Decimal
    policy_start: str
    policy_end: str
    policy_inception_date: str = ""
    continuous_months_active: Decimal
    hospital_name: str
    hospital_city: str
    city_tier: CityTier
    room_category_admitted: str = ""
    actual_room_rent_per_day: Decimal = Decimal(0)
    actual_icu_rent_per_day: Decimal = Decimal(0)
    hospitalization_days: int = 1
    icu_days: int = 0
    admission_date: str
    discharge_date: str
    diagnosis: str
    icd10_code: str = ""
    procedure_name: str
    is_day_care: bool = False
    is_accident: bool = False
    is_pre_existing: bool = False
    pre_existing_condition_name: Optional[str] = None
    bill_number: str
    claimed_total: Decimal
    line_items: list[BillItem]
    raw_documents: RawDocuments = Field(default_factory=RawDocuments)
    split_type: Literal["dev", "held_out"] = "dev"


class AdjudicationLine(Model):
    line_id: str
    raw_description: str
    category: BillCategory
    claimed_amount: Decimal
    admissible_amount: Decimal
    deduction_amount: Decimal
    deduction_reason: str = ""
    clause_cited: str = ""
    proportional_cut_applied: bool = False
    proportional_ratio: Optional[Decimal] = None
    audit_explanation: str = ""
    edited_by_adjudicator: bool = False
    adjudicator_comment: str = ""


class SublimitRecord(Model):
    sublimit_key: str
    name: str
    clause: str
    max_cap: Decimal
    claimed: Decimal
    previously_utilized: Decimal
    deduction: Decimal
    admissible: Decimal


def _zero_reasons() -> dict[str, Decimal]:
    keys = ["room_rent_cap", "proportional_cut", "icu_cap", "non_payable_consumables", "waiting_period",
            "sublimits", "policy_lapse", "copay"]
    return {k: Decimal(0) for k in keys}


class AdjudicationWorksheet(Model):
    claim_id: str
    worksheet_id: str
    policy_number: str
    insured_name: str
    claimed_total: Decimal
    line_sum_total: Decimal
    line_sum_matches_bill: bool
    is_admissible: bool
    inadmissibility_reason: Optional[str] = None
    inadmissibility_clause: Optional[str] = None

    room_rent_cap_per_day: Decimal
    actual_room_rent_per_day: Decimal
    room_rent_breach: bool
    proportional_cut_ratio: Decimal
    icu_rent_cap_per_day: Decimal
    actual_icu_rent_per_day: Decimal
    icu_rent_breach: bool

    lines: list[AdjudicationLine]
    total_deductions: Decimal
    admissible_before_copay: Decimal
    copay_percent: int = 5
    copay_amount: Decimal
    final_payable_amount: Decimal
    sublimits_applied: list[SublimitRecord] = Field(default_factory=list)
    deductions_by_reason: dict[str, Decimal] = Field(default_factory=_zero_reasons)

    worksheet_hash: str
    revisions_count: int = 0

    critic_status: Literal["passed", "revision_requested", "not_rerun"] = "passed"
    critic_notes: str = ""
    critic_discrepancies: list[str] = Field(default_factory=list)

    requires_senior_approval: bool = False
    senior_approval_reasons: list[str] = Field(default_factory=list)
    approval_state: Literal["pending_adjudication", "junior_approved", "senior_approved", "rejected", "invalidated"] = "pending_adjudication"
    junior_adjudicator_name: Optional[str] = None
    senior_adjudicator_name: Optional[str] = None
    rejection_reason: Optional[str] = None
    approval_token: Optional[str] = None

    settlement_issued: bool = False
    settlement_letter: str = ""

    is_complex_case: bool = False
    complex_case_triggers: list[str] = Field(default_factory=list)
    llm_adjudication: Optional[dict] = None


class FraudFlag(Model):
    id: str
    type: str
    severity: Literal["high", "medium", "low"]
    title: str
    description: str
    clause_cited: str = ""
    evidence: str = ""
    attack_vector_category: Optional[Literal["palo_alto_security", "clinical_demographic_mismatch", "billing_package_fraud"]] = None


class FraudReport(Model):
    claim_id: str
    has_fraud_flag: bool
    risk_score: int
    flags: list[FraudFlag]
    prior_claims_count: int = 0
    duplicate_bill_found: bool = False
    historical_utilized_sublimits: dict[str, Decimal] = Field(default_factory=dict)
    tariff_assessment: dict = Field(default_factory=dict)


class PolicyClause(Model):
    id: str
    clause_number: str
    title: str
    section: str
    text: str
    rule_type: str
    tags: list[str] = Field(default_factory=list)


class ReflexionLesson(Model):
    id: str
    title: str
    clause_cited: str
    trigger_condition: str
    lesson_rule: str
    rationale: str
    source_claim_id: str
    status: Literal["pending_review", "approved", "rejected", "withdrawn"]
    created_at: str
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    impact_note: str = ""
    applies_when: dict = Field(default_factory=dict)   # e.g. {"room_breach": true} or {"line_category": "medicines", "description_contains": "file"}
    fired: int = 0                                      # claims where the lesson was retrieved
    edited: int = 0                                     # of those, claims an adjudicator then edited


class AgentTrace(Model):
    id: str
    agent_name: Literal["Supervisor", "Extraction", "Coverage", "FraudAnalyst", "Critic", "Adjudicator", "Settlement", "Memory"]
    timestamp: str
    step: str
    action: str
    rationale: str
    sanitized_input: str = ""   # health details masked
    sanitized_output: str = ""
    duration_ms: int = 0
    status: Literal["success", "warning", "error", "pending"] = "success"
