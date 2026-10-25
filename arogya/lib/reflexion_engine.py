"""Reflexion playbook. Mirrors src/lib/reflexionEngine.ts.

An adjudicator's correction becomes a *candidate* lesson that names its clause and says when it applies
(`applies_when`, checked by code). Only lessons a named reviewer has approved are used, and any can be
withdrawn. The original reported hard-coded before/after numbers; here each lesson keeps live counters
(claims it fired on, claims an adjudicator then edited), so a lesson that makes results worse shows up as a
high edit rate and can be withdrawn.
"""
from __future__ import annotations

import secrets

from ..types.adjudication import AdjudicationLine, AdjudicationWorksheet, ExtractedClaim, ReflexionLesson
from .claims_db import now

SEED = [
    ReflexionLesson(id="LESSON-001", title="Medicines are exempt from the room-rent proportional cut", clause_cited="Clause 4.1 Note b",
                    trigger_condition="Room rent exceeds the cap", lesson_rule="Reimburse medicines at 100% even when other lines are cut proportionally.",
                    rationale="Avoids wrongly cutting medicines when room rent is breached.", source_claim_id="DEV-CLM-003", status="approved", created_at="2024-09-15T10:00:00Z",
                    reviewed_by="Chief Adjudicator Dr. V. Menon", applies_when={"room_breach": True}),
    ReflexionLesson(id="LESSON-002", title="Cataract sub-limit accumulates across claims", clause_cited="Clause 4.3",
                    trigger_condition="Cataract claim under a policy with earlier cataract claims", lesson_rule="Subtract earlier cataract payments in the policy year from the 40,000 / 25% of SI limit.",
                    rationale="Prevents overpayment on a second cataract claim.", source_claim_id="DEV-CLM-011", status="approved", created_at="2024-09-18T14:15:00Z",
                    reviewed_by="Senior Auditor S. Kulkarni", applies_when={"procedure_contains": "cataract"}),
    ReflexionLesson(id="LESSON-003", title="Accidents are exempt from the 30-day and specific waiting periods", clause_cited="Clause 3.1 & Clause 6.3",
                    trigger_condition="Hospitalisation caused by an accident in the first months", lesson_rule="Do not repudiate on a waiting period when the discharge summary confirms an accident.",
                    rationale="Prevents wrongly rejecting emergency trauma claims.", source_claim_id="DEV-CLM-019", status="approved", created_at="2024-09-22T09:30:00Z",
                    reviewed_by="Chief Medical Adjudicator", applies_when={"accident": True}),
    ReflexionLesson(id="LESSON-004", title="Day-care procedures need no 24-hour stay", clause_cited="Clause 3.23 & Annexure-C",
                    trigger_condition="Day-care procedure with stay under 24 hours", lesson_rule="Check Annexure-C before repudiating on stay duration.",
                    rationale="Regulatory exception for day-care treatment.", source_claim_id="DEV-CLM-027", status="approved", created_at="2024-09-25T11:20:00Z",
                    reviewed_by="TPA Supervisor", applies_when={"day_care": True}),
]


def applies(l: ReflexionLesson, c: ExtractedClaim, ws: AdjudicationWorksheet | None) -> bool:
    w, text = l.applies_when, f"{c.diagnosis} {c.procedure_name}".lower()
    checks = [
        w.get("room_breach") is None or bool(ws and ws.room_rent_breach) == w["room_breach"],
        w.get("procedure_contains") is None or w["procedure_contains"] in text,
        w.get("accident") is None or c.is_accident == w["accident"],
        w.get("day_care") is None or c.is_day_care == w["day_care"],
        w.get("line_category") is None or any(i.category == w["line_category"] and w.get("description_contains", "") in i.raw_description.lower() for i in c.line_items),
    ]
    return all(checks)


class ReflexionEngine:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.lessons: dict[str, ReflexionLesson] = {l.id: l.model_copy(deep=True) for l in SEED}

    def all(self) -> list[ReflexionLesson]:
        return list(self.lessons.values())

    def approved(self) -> list[ReflexionLesson]:
        return [l for l in self.lessons.values() if l.status == "approved"]

    def retrieve(self, c: ExtractedClaim, ws: AdjudicationWorksheet | None = None) -> list[ReflexionLesson]:
        """Only approved lessons whose trigger matches this claim; each retrieval is counted."""
        hits = [l for l in self.approved() if applies(l, c, ws)]
        for l in hits:
            l.fired += 1
        return hits

    def record_outcome(self, lesson_ids: list[str], edited: bool) -> None:
        for lid in lesson_ids:
            if lid in self.lessons and edited:
                self.lessons[lid].edited += 1

    def propose_from_edit(self, c: ExtractedClaim, line: AdjudicationLine, original_deduction, comment: str) -> ReflexionLesson:
        lesson = ReflexionLesson(
            id=f"LESSON-{secrets.token_hex(2).upper()}", title=f"Adjudicator override on '{line.raw_description}'", clause_cited=line.clause_cited or "Clause 4.1",
            trigger_condition=f"{line.category} line containing '{line.raw_description.lower()}'",
            lesson_rule=f"Deduction was changed from {original_deduction} to {line.deduction_amount}. Reason: {comment or 'adjudicator judgement'}.",
            rationale=comment or "Correction by adjudicator.", source_claim_id=c.claim_id, status="pending_review", created_at=now(),
            applies_when={"line_category": line.category, "description_contains": line.raw_description.lower()})
        self.lessons[lesson.id] = lesson
        return lesson

    def review(self, lid: str, action: str, reviewer: str, note: str = "") -> ReflexionLesson:
        l = self.lessons[lid]
        if not reviewer.strip():
            raise ValueError("a named reviewer is required")
        allowed = {"approve": ("pending_review", "approved"), "reject": ("pending_review", "rejected"), "withdraw": ("approved", "withdrawn")}[action]
        if l.status != allowed[0]:
            raise ValueError(f"cannot {action} a lesson that is {l.status}")
        l.status, l.reviewed_by, l.reviewed_at = allowed[1], reviewer, now()
        l.impact_note = note or l.impact_note
        return l

    def stats(self) -> list[dict]:
        return [{"id": l.id, "title": l.title, "status": l.status, "fired": l.fired, "edited": l.edited,
                 "edit_rate": round(l.edited / l.fired, 3) if l.fired else None} for l in self.lessons.values()]


reflexion_engine = ReflexionEngine()
