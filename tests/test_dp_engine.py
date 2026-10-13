"""
Unit tests for backend.dp_engine, matching the scenarios worked through in
SAASSP's design conversation and documented in the SRS/SDD:

- weighted progress = points earned / points attempted
- best-N-of-M recorded-mark pool with displacement
- six fixed threshold bands
- trend classification tolerant of a single reversal
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.dp_engine import (
    CategoryConfig, MarkRecord, calculate_progress, classify_trend,
    threshold_band, recorded_pool, weighting_sum,
)

BANDS = [
    (75, 100.0001, "very_safe", "Very safe / excellent"),
    (70, 75, "safe", "Safe"),
    (60, 70, "generally_safe", "Generally safe"),
    (50, 60, "pass_some_risk", "Pass, some risk"),
    (40, 50, "danger", "Danger, at risk of failing"),
    (0, 40, "critical", "Critical / failing"),
]


def test_progress_matches_worked_example_quiz_and_test():
    # Quiz1 80% of a 15%-weighted, 1-of-1 recorded category
    # Test1 72% of a 25%-weighted, 1-of-1 recorded category
    # progress = (12 + 18) / (15 + 25) * 100 = 75%
    categories = [
        CategoryConfig("Quizzes", dp_weighting=15, total_assessments=1, recorded_count=1),
        CategoryConfig("Tests", dp_weighting=25, total_assessments=1, recorded_count=1),
    ]
    marks = {
        "Quizzes": [MarkRecord(id=1, category_name="Quizzes", percentage=80, sequence=1)],
        "Tests": [MarkRecord(id=2, category_name="Tests", percentage=72, sequence=2)],
    }
    result = calculate_progress(categories, marks, BANDS)
    assert result.progress_percentage == 75.0
    assert result.threshold_band_key == "very_safe"  # 75% falls in the 75-100 band


def test_zero_weighted_category_is_excluded():
    categories = [
        CategoryConfig("Tests", dp_weighting=50, total_assessments=3, recorded_count=2),
        CategoryConfig("Tutorials", dp_weighting=0, total_assessments=2, recorded_count=0),
    ]
    marks = {
        "Tests": [
            MarkRecord(id=1, category_name="Tests", percentage=70, sequence=1),
            MarkRecord(id=2, category_name="Tests", percentage=80, sequence=2),
        ],
        "Tutorials": [
            MarkRecord(id=3, category_name="Tutorials", percentage=10, sequence=1),
        ],
    }
    result = calculate_progress(categories, marks, BANDS)
    # Only Tests counts: (0.70*25 + 0.80*25) / (25+25) * 100 = 75%
    assert result.progress_percentage == 75.0
    assert 3 not in result.recorded_mark_ids
    assert 3 not in result.displaced_mark_ids  # never attempted at all, category excluded


def test_best_n_of_m_displacement():
    # cap=2 of 3 tests: t1=70, t2=50 -> recorded {t1,t2}; t3=80 arrives ->
    # lowest (t2=50) is displaced, recorded becomes {t1,t3}
    categories = [CategoryConfig("Tests", dp_weighting=50, total_assessments=3, recorded_count=2)]
    marks = {
        "Tests": [
            MarkRecord(id=1, category_name="Tests", percentage=70, sequence=1),
            MarkRecord(id=2, category_name="Tests", percentage=50, sequence=2),
            MarkRecord(id=3, category_name="Tests", percentage=80, sequence=3),
        ]
    }
    result = calculate_progress(categories, marks, BANDS)
    assert set(result.recorded_mark_ids) == {1, 3}
    assert result.displaced_mark_ids == [2]
    # (0.70*25 + 0.80*25) / 50 * 100 = 75%
    assert result.progress_percentage == 75.0


def test_recorded_pool_tie_break_by_upload_order():
    marks = [
        MarkRecord(id=1, category_name="Tests", percentage=60, sequence=1),
        MarkRecord(id=2, category_name="Tests", percentage=60, sequence=2),
        MarkRecord(id=3, category_name="Tests", percentage=90, sequence=3),
    ]
    pool = recorded_pool(marks, cap=2)
    assert [m.id for m in pool] == [3, 1]  # highest first, tie broken by earlier upload


def test_threshold_bands_cover_all_six_ranges():
    cases = [
        (100, "very_safe"), (75, "very_safe"), (74.99, "safe"),
        (70, "safe"), (69.99, "generally_safe"),
        (60, "generally_safe"), (59.99, "pass_some_risk"),
        (50, "pass_some_risk"), (49.99, "danger"),
        (40, "danger"), (39.99, "critical"), (0, "critical"),
    ]
    for pct, expected_key in cases:
        key, _label = threshold_band(pct, BANDS)
        assert key == expected_key, f"{pct}% should be {expected_key}, got {key}"


def test_trend_not_yet_available_below_three_marks():
    marks = [MarkRecord(id=1, category_name="Tests", percentage=60, sequence=1)]
    assert classify_trend(marks) == "not_yet_available"
    marks.append(MarkRecord(id=2, category_name="Tests", percentage=70, sequence=2))
    assert classify_trend(marks) == "not_yet_available"


def test_trend_strictly_increasing():
    marks = [
        MarkRecord(id=1, category_name="Tests", percentage=50, sequence=1),
        MarkRecord(id=2, category_name="Tests", percentage=60, sequence=2),
        MarkRecord(id=3, category_name="Tests", percentage=70, sequence=3),
    ]
    assert classify_trend(marks) == "increasing"


def test_trend_tolerates_a_single_reversal():
    # 60 -> 75 -> 70 -> 85 : one dip (75->70) against an otherwise rising run
    marks = [
        MarkRecord(id=1, category_name="Tests", percentage=60, sequence=1),
        MarkRecord(id=2, category_name="Tests", percentage=75, sequence=2),
        MarkRecord(id=3, category_name="Tests", percentage=70, sequence=3),
        MarkRecord(id=4, category_name="Tests", percentage=85, sequence=4),
    ]
    assert classify_trend(marks) == "increasing"


def test_trend_recovery_story_from_conversation():
    # Test1=90, Test2=40, Test3=95 : one reversal (90->40), then recovers (40->95)
    marks = [
        MarkRecord(id=1, category_name="Tests", percentage=90, sequence=1),
        MarkRecord(id=2, category_name="Tests", percentage=40, sequence=2),
        MarkRecord(id=3, category_name="Tests", percentage=95, sequence=3),
    ]
    assert classify_trend(marks) == "increasing"


def test_trend_two_or_more_reversals_is_fluctuating():
    # up, down, up, down against the dominant direction = 2 reversals
    marks = [
        MarkRecord(id=1, category_name="Tests", percentage=60, sequence=1),
        MarkRecord(id=2, category_name="Tests", percentage=80, sequence=2),
        MarkRecord(id=3, category_name="Tests", percentage=55, sequence=3),
        MarkRecord(id=4, category_name="Tests", percentage=90, sequence=4),
        MarkRecord(id=5, category_name="Tests", percentage=40, sequence=5),
    ]
    assert classify_trend(marks) == "fluctuating"


def test_trend_dropping():
    marks = [
        MarkRecord(id=1, category_name="Tests", percentage=90, sequence=1),
        MarkRecord(id=2, category_name="Tests", percentage=70, sequence=2),
        MarkRecord(id=3, category_name="Tests", percentage=50, sequence=3),
    ]
    assert classify_trend(marks) == "dropping"


def test_weighting_sum_for_realtime_validation():
    categories = [
        CategoryConfig("Tests", 50, 3, 2),
        CategoryConfig("Quizzes", 20, 3, 2),
        CategoryConfig("Assignments", 20, 2, 2),
        CategoryConfig("Practicals", 10, 2, 1),
        CategoryConfig("Tutorials", 0, 2, 0),
    ]
    assert weighting_sum(categories) == 100


def test_no_marks_uploaded_yet_gives_zero_progress():
    categories = [CategoryConfig("Tests", 50, 3, 2)]
    result = calculate_progress(categories, {}, BANDS)
    assert result.progress_percentage == 0.0
    assert result.threshold_band_key == "critical"


# ---------------------------------------------------------------------
# Lecturer marks replace student-entered marks for the same assessment
# ---------------------------------------------------------------------
from types import SimpleNamespace
from backend.dp_engine import split_effective_marks


def _m(id, cat, label, source, pct=0):
    return SimpleNamespace(id=id, category_name=cat, assessment_label=label, source=source,
                           percentage_achieved=pct)


def test_lecturer_mark_replaces_student_mark_for_same_assessment():
    marks = [_m(1, "Tests", "Test 1", "student", 90), _m(2, "Tests", "Test 1", "lecturer", 60),
             _m(3, "Tests", "Test 2", "student", 70)]
    effective, replaced = split_effective_marks(marks)
    assert [m.id for m in effective] == [2, 3]
    assert [m.id for m in replaced] == [1]


def test_unlabelled_student_marks_are_never_replaced():
    marks = [_m(1, "Tests", None, "student", 90), _m(2, "Tests", "Test 1", "lecturer", 60)]
    effective, replaced = split_effective_marks(marks)
    assert len(effective) == 2 and replaced == []


def test_label_match_is_case_insensitive_and_category_specific():
    marks = [_m(1, "Tests", "test 1", "student"), _m(2, "Tests", "Test 1", "lecturer"),
             _m(3, "Quizzes", "Test 1", "student")]
    effective, replaced = split_effective_marks(marks)
    assert [m.id for m in replaced] == [1] and 3 in [m.id for m in effective]
