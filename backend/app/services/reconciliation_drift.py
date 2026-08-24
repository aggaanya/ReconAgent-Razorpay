"""Deterministic drift analysis ("What Changed?") between two reconciliation runs."""

from app.schemas.reconciliation import (
    CategoryDrift,
    PriorityDrift,
    ReconciliationDriftAnalysis,
    ReconciliationReport,
)


def compare_runs(
    previous: ReconciliationReport,
    current: ReconciliationReport,
    previous_seed: int | None = None,
    current_seed: int | None = None,
) -> ReconciliationDriftAnalysis:
    """Deterministically compare two reconciliation reports.

    Calculates:
    - Match rate change (in percentage points)
    - Exception count change
    - Total financial exposure change
    - Category-by-category count and exposure shifts
    - Severity priority band shifts
    - Identifies top major drivers of change
    """
    prev_mr = previous.summary.match_rate
    curr_mr = current.summary.match_rate
    mr_change_pp = (
        round(curr_mr - prev_mr, 2)
        if (prev_mr is not None and curr_mr is not None)
        else None
    )

    prev_exc_cnt = previous.summary.exception_count
    curr_exc_cnt = current.summary.exception_count
    exc_cnt_change = curr_exc_cnt - prev_exc_cnt

    prev_sum = previous.exception_summary
    curr_sum = current.exception_summary

    prev_exp = prev_sum.total_financial_exposure_minor if prev_sum else 0
    curr_exp = curr_sum.total_financial_exposure_minor if curr_sum else 0
    exp_change = curr_exp - prev_exp

    # Category drift
    prev_cat_counts = previous.summary.exception_breakdown
    curr_cat_counts = current.summary.exception_breakdown

    prev_cat_exp: dict[str, int] = {}
    if prev_sum:
        for c in prev_sum.top_exception_categories:
            prev_cat_exp[c["category"]] = c["exposure_minor"]

    curr_cat_exp: dict[str, int] = {}
    if curr_sum:
        for c in curr_sum.top_exception_categories:
            curr_cat_exp[c["category"]] = c["exposure_minor"]

    all_cats = sorted(set(prev_cat_counts.keys()).union(curr_cat_counts.keys()))
    category_drifts: list[CategoryDrift] = []

    for cat in all_cats:
        p_c = prev_cat_counts.get(cat, 0)
        c_c = curr_cat_counts.get(cat, 0)
        p_e = prev_cat_exp.get(cat, 0)
        c_e = curr_cat_exp.get(cat, 0)
        category_drifts.append(
            CategoryDrift(
                category=cat,
                previous_count=p_c,
                current_count=c_c,
                count_change=c_c - p_c,
                previous_exposure_minor=p_e,
                current_exposure_minor=c_e,
                exposure_change_minor=c_e - p_e,
            )
        )

    # Priority / Severity drift
    severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    priority_drifts: list[PriorityDrift] = []

    for sev in severities:
        p_cnt = getattr(prev_sum, f"{sev.lower()}_count", 0) if prev_sum else 0
        c_cnt = getattr(curr_sum, f"{sev.lower()}_count", 0) if curr_sum else 0
        p_exp = getattr(prev_sum, f"{sev.lower()}_exposure_minor", 0) if prev_sum else 0
        c_exp = getattr(curr_sum, f"{sev.lower()}_exposure_minor", 0) if curr_sum else 0
        priority_drifts.append(
            PriorityDrift(
                severity=sev,
                previous_count=p_cnt,
                current_count=c_cnt,
                count_change=c_cnt - p_cnt,
                previous_exposure_minor=p_exp,
                current_exposure_minor=c_exp,
                exposure_change_minor=c_exp - p_exp,
            )
        )

    # Major drivers (top 3 category shifts by absolute exposure change or count change)
    sorted_drivers = sorted(
        category_drifts,
        key=lambda cd: (abs(cd.exposure_change_minor), abs(cd.count_change)),
        reverse=True,
    )

    major_drivers: list[str] = []
    for cd in sorted_drivers[:3]:
        if cd.count_change != 0 or cd.exposure_change_minor != 0:
            direction = "increased" if cd.count_change >= 0 else "decreased"
            exp_dir = "+" if cd.exposure_change_minor >= 0 else ""
            major_drivers.append(
                f"{cd.category} exceptions {direction} from {cd.previous_count} to {cd.current_count} "
                f"(exposure change: {exp_dir}₹{cd.exposure_change_minor / 100:,.2f})"
            )

    if not major_drivers:
        major_drivers.append("No significant exception distribution changes detected between the two runs.")

    return ReconciliationDriftAnalysis(
        previous_seed=previous_seed,
        current_seed=current_seed,
        previous_match_rate=prev_mr,
        current_match_rate=curr_mr,
        match_rate_change_pp=mr_change_pp,
        previous_exception_count=prev_exc_cnt,
        current_exception_count=curr_exc_cnt,
        exception_count_change=exc_cnt_change,
        previous_financial_exposure_minor=prev_exp,
        current_financial_exposure_minor=curr_exp,
        financial_exposure_change_minor=exp_change,
        category_drifts=category_drifts,
        priority_drifts=priority_drifts,
        major_drivers=major_drivers,
    )
