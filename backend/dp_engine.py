"""
DP Progress Calculation Engine.

Implements SAASSP_SDD.docx Section 3.1, "DP Progress Calculation Engine":
deterministic, rule-based logic for the recorded-mark pool, the weighted
DP progress percentage, threshold-band mapping, and per-category trend
classification. This module never calls the AI Feedback Service and never
depends on it (SDD 3.1: "This engine never depends on the AI Feedback
Service").

All functions here are pure with respect to the ORM: they take plain
Python values in and return plain Python values out, which is what makes
them straightforward to unit test (see tests/test_dp_engine.py) against
the scenarios described in SAASSP_SRS.docx Section 3.3.1.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence


@dataclass
class CategoryConfig:
    name: str
    dp_weighting: float          # percentage points this category contributes, 0-100
    total_assessments: int
    recorded_count: int          # cap: how many of this category's marks count towards DP


@dataclass
class MarkRecord:
    id: int
    category_name: str
    percentage: float
    sequence: int                # upload order, ascending


@dataclass
class ProgressResult:
    progress_percentage: float
    threshold_band_key: str
    threshold_band_label: str
    recorded_mark_ids: List[int]          # marks currently counted towards DP
    displaced_mark_ids: List[int]         # marks no longer counted (bumped out)
    points_earned: float
    points_attempted: float


def threshold_band(progress_percentage: float, bands: Sequence[tuple]) -> tuple:
    """Map a DP progress percentage to one of the six fixed threshold bands.

    `bands` is the (low, high, key, label) list from config.THRESHOLD_BANDS.
    SRS 3.3.1 / SDD 3.1, CalculateProgress() step 5.
    """
    for low, high, key, label in bands:
        if low <= progress_percentage < high:
            return key, label
    # 100.0 falls in the top band by construction (high=100.0001), but guard anyway
    low, high, key, label = bands[0]
    return key, label


def recorded_pool(marks: Sequence[MarkRecord], cap: int) -> List[MarkRecord]:
    """Return the best `min(cap, len(marks))` marks for one category,
    i.e. the recorded-mark pool described in SRS 3.2.3/3.2.4 and SDD 3.1.

    Ties are broken by upload order (earlier upload wins a tie), so the
    result is deterministic.
    """
    if cap <= 0:
        return []
    ranked = sorted(marks, key=lambda m: (-m.percentage, m.sequence))
    return ranked[:cap]


def calculate_progress(
    categories: Sequence[CategoryConfig],
    marks_by_category: dict,   # category_name -> list[MarkRecord]
    bands: Sequence[tuple],
) -> ProgressResult:
    """SDD 3.1, DP Progress Calculation Engine.CalculateProgress().

    For each non-zero-weighted category, retrieve the recorded-mark pool
    (the best min(cap, marksWritten) marks). Sum the DP points earned
    across every recorded mark. Sum the DP points attempted (the
    weighting share represented by each recorded mark). Divide points
    earned by points attempted and express as a percentage. Map the
    result to one of the six fixed threshold bands.
    """
    points_earned = 0.0
    points_attempted = 0.0
    recorded_ids: List[int] = []
    displaced_ids: List[int] = []

    for cat in categories:
        if cat.dp_weighting <= 0:
            continue  # a category at 0% contribution is excluded entirely
        marks = marks_by_category.get(cat.name, [])
        if not marks or cat.recorded_count <= 0:
            continue
        cap = cat.recorded_count
        pool = recorded_pool(marks, cap)
        pool_ids = {m.id for m in pool}
        slot_weight = cat.dp_weighting / cap

        for m in pool:
            points_earned += (m.percentage / 100.0) * slot_weight
            points_attempted += slot_weight
            recorded_ids.append(m.id)

        for m in marks:
            if m.id not in pool_ids:
                displaced_ids.append(m.id)

    progress_percentage = (points_earned / points_attempted * 100.0) if points_attempted > 0 else 0.0
    key, label = threshold_band(progress_percentage, bands)

    return ProgressResult(
        progress_percentage=round(progress_percentage, 2),
        threshold_band_key=key,
        threshold_band_label=label,
        recorded_mark_ids=recorded_ids,
        displaced_mark_ids=displaced_ids,
        points_earned=round(points_earned, 4),
        points_attempted=round(points_attempted, 4),
    )


def classify_trend(marks: Sequence[MarkRecord]) -> str:
    """SDD 3.1, DP Progress Calculation Engine.ClassifyTrend().

    Retrieve the category's full, ordered mark-upload history. Fewer
    than three marks: "not_yet_available". Otherwise compare each
    consecutive pair for direction; a single reversal against an
    otherwise consistent direction still counts as that direction; two
    or more reversals is "fluctuating".
    """
    ordered = sorted(marks, key=lambda m: m.sequence)
    if len(ordered) < 3:
        return "not_yet_available"

    signs = []
    for a, b in zip(ordered, ordered[1:]):
        diff = b.percentage - a.percentage
        if diff > 0:
            signs.append(1)
        elif diff < 0:
            signs.append(-1)
        # ties (diff == 0) are not counted as a step in either direction

    if not signs:
        return "fluctuating"

    ups = signs.count(1)
    downs = signs.count(-1)
    dominant = 1 if ups >= downs else -1
    reversals = sum(1 for s in signs if s != dominant)

    if reversals <= 1:
        return "increasing" if dominant == 1 else "dropping"
    return "fluctuating"


def weighting_sum(categories: Sequence[CategoryConfig]) -> float:
    """Used by the real-time validation in Configure Assessment Structure
    (SRS 3.2.3, Alternative Paths): weightings must sum to 100%."""
    return sum(c.dp_weighting for c in categories)


def marks_by_category_from_student_module(student_module) -> dict:
    """Shape a StudentModule ORM object's marks into the
    {category_name: [MarkRecord, ...]} form calculate_progress() and
    classify_trend() expect. Shared by the student and staff blueprints."""
    result: dict = {}
    for m in student_module.marks:
        result.setdefault(m.category_name, []).append(
            MarkRecord(id=m.id, category_name=m.category_name,
                       percentage=float(m.percentage_achieved), sequence=m.sequence)
        )
    return result
