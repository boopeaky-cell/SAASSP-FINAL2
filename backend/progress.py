"""
Shared helpers that turn a StudentModule into a DP progress result.

Because the assessment structure now belongs to the MODULE (set by the
lecturer and changeable at any time), progress is always computed from the
CURRENT configuration at the moment it is shown - nothing is stored that could
go stale when a weighting changes.
"""
from flask import current_app

from backend import db
from backend.dp_engine import (
    CategoryConfig, calculate_progress, marks_by_category_from_student_module,
    split_effective_marks,
)


def module_category_configs(student_module):
    return [
        CategoryConfig(c.category_name, c.dp_weighting, c.total_assessments, c.recorded_count)
        for c in student_module.categories
    ]


def compute_progress(student_module):
    """Return (result, marks_by_category). result is None if the lecturer has
    not configured the module yet."""
    categories = module_category_configs(student_module)
    if not categories:
        return None, {}
    marks_by_category = marks_by_category_from_student_module(student_module)
    result = calculate_progress(categories, marks_by_category, current_app.config["THRESHOLD_BANDS"])
    return result, marks_by_category


def mark_rows(student_module, result):
    """Marks for display, newest first, each tagged with a status:
    recorded | displaced | replaced (a student mark superseded by the lecturer's)."""
    effective, replaced = split_effective_marks(list(student_module.marks))
    replaced_ids = {m.id for m in replaced}
    recorded_ids = set(result.recorded_mark_ids) if result else set()
    rows = []
    for m in sorted(student_module.marks, key=lambda m: m.sequence, reverse=True):
        if m.id in replaced_ids:
            status = "replaced"
        elif m.id in recorded_ids:
            status = "recorded"
        else:
            status = "displaced"
        rows.append((m, status))
    return rows


def refresh_recorded_flags(student_module):
    """Keep AssessmentMark.recorded_towards_dp in step with the current
    configuration. Caller commits."""
    db.session.flush()
    db.session.expire(student_module, ["marks"])   # pick up marks added/removed this request
    result, _ = compute_progress(student_module)
    recorded_ids = set(result.recorded_mark_ids) if result else set()
    for m in student_module.marks:
        m.recorded_towards_dp = m.id in recorded_ids
    return result


def next_sequence(student_module_id):
    from backend.models import AssessmentMark
    return (db.session.query(db.func.max(AssessmentMark.sequence))
            .filter_by(student_module_id=student_module_id).scalar() or 0) + 1
