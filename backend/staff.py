"""
See SAASSP_SRS.docx Section 3.2.7, View Student Progress. Read-only.

An Academic Staff account only sees students enrolled in the module(s)
assigned to them (backend/models.py LecturerModule, assigned by a System
Administrator in Manage User Accounts - backend/admin.py). A lecturer with no
assigned modules sees an empty list rather than every student.
"""
from flask import Blueprint, render_template, abort, current_app
from flask_login import login_required, current_user

from backend.models import User, StudentModule
from backend.dp_engine import CategoryConfig, calculate_progress, marks_by_category_from_student_module

staff_bp = Blueprint("staff", __name__, url_prefix="/staff")


def _require_staff():
    if current_user.role != "academic_staff":
        abort(403)


def _assigned_module_codes():
    """Module codes this logged-in lecturer is assigned to teach."""
    return {lm.module_code for lm in current_user.taught_modules}


@staff_bp.route("/students")
@login_required
def student_list():
    _require_staff()
    codes = _assigned_module_codes()
    if codes:
        students = (
            User.query.join(StudentModule, StudentModule.student_id == User.id)
            .filter(User.role == "student", StudentModule.module_code.in_(codes))
            .distinct()
            .order_by(User.name)
            .all()
        )
    else:
        students = []
    return render_template("staff_students.html", students=students, no_modules_assigned=not codes)


@staff_bp.route("/students/<int:student_id>")
@login_required
def student_modules(student_id):
    _require_staff()
    codes = _assigned_module_codes()
    student = User.query.filter_by(id=student_id, role="student").first_or_404()
    modules = (
        StudentModule.query.filter_by(student_id=student_id)
        .filter(StudentModule.module_code.in_(codes) if codes else False)
        .all()
    )
    return render_template("staff_student_modules.html", student=student, modules=modules)


@staff_bp.route("/students/<int:student_id>/module/<int:sm_id>")
@login_required
def student_progress(student_id, sm_id):
    _require_staff()
    codes = _assigned_module_codes()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != student_id or sm.module_code not in codes:
        abort(404)

    result = None
    if sm.categories:
        categories = [
            CategoryConfig(c.category_name, c.dp_weighting, c.total_assessments, c.recorded_count)
            for c in sm.categories
        ]
        marks_by_category = marks_by_category_from_student_module(sm)
        result = calculate_progress(categories, marks_by_category, current_app.config["THRESHOLD_BANDS"])

    marks_sorted = sorted(sm.marks, key=lambda m: m.sequence, reverse=True)
    latest_feedback = marks_sorted[0].feedback if marks_sorted and marks_sorted[0].feedback else None

    return render_template("staff_progress.html", sm=sm, result=result,
                            marks=marks_sorted, latest_feedback=latest_feedback)
