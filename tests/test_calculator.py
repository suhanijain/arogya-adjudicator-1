"""Unit tests for the calculator, plus a differential test against the original TypeScript output."""
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from arogya.data.synthetic_claims import load_claims
from arogya.lib.adjudication_calculator import calculate_worksheet, check_waiting_period, rupee
from arogya.types.adjudication import BillItem, ExtractedClaim

REF = {r["claimId"]: r for r in json.loads((Path(__file__).parent / "fixtures" / "ts_reference.json").read_text())}


def make_claim(**kw) -> ExtractedClaim:
    def item(i, desc, cat, rate, units=1):
        return BillItem(id=f"L{i}", raw_description=desc, category=cat, claimed_amount=D(rate * units), days_or_units=D(units), unit_rate=D(rate))
    lines = [item(1, "Room Rent (Deluxe)", "room", 8000, 3), item(2, "Surgeon Fee", "surgeon_fee", 30000),
             item(3, "Anaesthetist Fee", "surgeon_fee", 6000), item(4, "OT Charges", "operation_theatre", 12000),
             item(5, "Pharmacy medicines", "medicines", 10000), item(6, "Pathology tests", "investigation_diagnostics", 3000),
             item(7, "Surgical gloves", "consumables", 1400)]
    base = dict(claim_id="T-1", policy_number="P-1", insured_name="Test Patient", patient_age=40, patient_gender="Male",
                sum_insured=D(500000), total_coverage_limit=D(500000), policy_start="2024-01-01", policy_end="2024-12-31",
                continuous_months_active=D(40), hospital_name="H", hospital_city="C", city_tier="tier1",
                actual_room_rent_per_day=D(8000), hospitalization_days=3, admission_date="2024-06-01", discharge_date="2024-06-04",
                diagnosis="Gallstones", procedure_name="Surgery", bill_number="B-1", claimed_total=D(sum(l.claimed_amount for l in lines)),
                line_items=lines)
    base.update(kw)
    return ExtractedClaim(**base)


def test_headline_room_breach_proportional_cut_exempts_medicines():
    ws = calculate_worksheet(make_claim())
    assert ws.room_rent_cap_per_day == 5000 and ws.room_rent_breach
    assert ws.proportional_cut_ratio == D("0.625")                          # 5000 / 8000
    by = {l.raw_description: l for l in ws.lines}
    assert by["Room Rent (Deluxe)"].admissible_amount == 15000               # 5000 x 3 nights
    assert by["Surgeon Fee"].admissible_amount == 18750                      # 30000 x 0.625
    assert by["Pharmacy medicines"].admissible_amount == 10000               # exempt (Note b)
    assert by["Surgical gloves"].admissible_amount == 0                      # Annexure-A List I
    assert ws.total_deductions == 9000 + 11250 + 2250 + 4500 + 1125 + 1400 and ws.is_admissible
    assert ws.admissible_before_copay + ws.total_deductions == ws.claimed_total
    assert ws.copay_amount == rupee(ws.admissible_before_copay * D("0.05"))
    assert ws.final_payable_amount == ws.admissible_before_copay - ws.copay_amount


def test_every_deduction_cites_a_clause():
    for c in load_claims():
        for l in calculate_worksheet(c).lines:
            if l.deduction_amount > 0:
                assert l.clause_cited and l.deduction_reason


def test_totals_identity_on_all_claims():
    for c in load_claims():
        ws = calculate_worksheet(c)
        assert ws.admissible_before_copay == max(D(0), c.claimed_total - ws.total_deductions)
        assert ws.final_payable_amount <= c.total_coverage_limit


def test_waiting_periods_are_checked_independently():
    knee = make_claim(procedure_name="Total Knee Replacement", diagnosis="Osteoarthritis", continuous_months_active=D(14))
    assert check_waiting_period(knee)[0].startswith("Clause 6.2(ii)")        # escaped the original's if/else chain
    assert check_waiting_period(make_claim(is_pre_existing=True, continuous_months_active=D(18), diagnosis="Hypertension crisis", procedure_name="Medical management"))[0].startswith("Clause 6.1")
    assert check_waiting_period(make_claim(continuous_months_active=D("0.5")))[0].startswith("Clause 6.3")
    assert check_waiting_period(make_claim(continuous_months_active=D("0.5"), is_accident=True))[0] == ""   # accident exempt


def test_cataract_sublimit_uses_prior_utilisation():
    c = make_claim(procedure_name="Cataract surgery", diagnosis="Cataract", continuous_months_active=D(30), actual_room_rent_per_day=D(0),
                   sum_insured=D(400000), total_coverage_limit=D(400000))
    full = calculate_worksheet(c)
    capped = calculate_worksheet(c, cataract_previously_used=D(25000))
    assert capped.sublimits_applied[0].admissible == 15000                    # 40,000 cap - 25,000 used
    assert capped.final_payable_amount < full.final_payable_amount


def test_policy_lapse_repudiates_everything():
    ws = calculate_worksheet(make_claim(admission_date="2025-03-01"))
    assert not ws.is_admissible and ws.final_payable_amount == 0 and ws.copay_amount == 0


# Claims where the Python port deliberately differs from the TypeScript original (see README).
DELIBERATE = {"CLM-004", "CLM-044"}    # knee replacement at 14 months: the original admitted it


def test_matches_typescript_original_exactly_on_all_other_claims():
    diffs = []
    for c in load_claims():
        ws, ref = calculate_worksheet(c), REF[c.claim_id]
        if c.claim_id in DELIBERATE:
            assert ref["isAdmissible"] and not ws.is_admissible
            continue
        got = (ws.is_admissible, int(ws.total_deductions), int(ws.copay_amount), int(ws.final_payable_amount))
        want = (ref["isAdmissible"], ref["totalDeductions"], ref["copayAmount"], ref["finalPayableAmount"])
        if got != want:
            diffs.append((c.claim_id, got, want))
    assert not diffs, diffs
