"""
Shared helpers that turn a StudentModule into a DP progress result.

Because the assessment structure now belongs to the MODULE (set by the
lecturer and changeable at any time), progress is always computed from the
CURRENT configuration at the moment it is shown - nothing is stored that could
go stale when a weighting changes.
"""
from datetime import datetime

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


def sync_lecturer_marks(student_module):
    """Make this enrolment show every mark a lecturer has uploaded for its
    student number. Safe to call repeatedly. Caller commits.

    Lecturer marks are stored by student number (LecturerMark), so they exist
    before the student enrols or registers and survive account deletion; this
    copies them onto the enrolment so they count towards DP progress."""
    from backend.models import AssessmentMark, LecturerMark
    number = (student_module.student.student_number or "").strip()
    if not number:
        return False
    code = student_module.module_code
    held = {h.assessment_label: h for h in
            LecturerMark.query.filter_by(module_code=code, student_number=number).all()}
    mirrors = {m.assessment_label: m for m in student_module.marks
               if m.source == "lecturer" and m.assessment_label}
    changed = False
    # Older uploads (before LecturerMark existed) only live as mirrors: keep them.
    for label, m in mirrors.items():
        if label not in held:
            db.session.add(LecturerMark(
                module_code=code, student_number=number, category_name=m.category_name,
                assessment_label=label, percentage=m.percentage_achieved))
    for label, h in held.items():
        m = mirrors.get(label)
        if m is None:
            db.session.add(AssessmentMark(
                student_module_id=student_module.id, category_name=h.category_name,
                percentage_achieved=h.percentage, sequence=next_sequence(student_module.id),
                source="lecturer", assessment_label=label))
            db.session.flush()
            changed = True
        elif float(m.percentage_achieved) != float(h.percentage) or m.category_name != h.category_name:
            m.percentage_achieved = h.percentage
            m.category_name = h.category_name
            m.date_uploaded = datetime.utcnow()
            changed = True
    if changed:
        db.session.flush()
        db.session.expire(student_module, ["marks"])
        refresh_recorded_flags(student_module)
    return changed
