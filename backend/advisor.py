"""
Academic Advisor side of SAASSP. READ-ONLY.

An advisor can look up ANY student by student number (for in-person
counselling) and see how they are progressing in every module. There is no
browse-all list: the student number has to be typed in, one student at a time.
Advisors cannot change any data, and every lookup is written to the access
log (backend/access_log.py) which the administrator can review.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from backend.models import User, StudentModule
from backend.access_log import log_access
from backend.progress import compute_progress, mark_rows

advisor_bp = Blueprint("advisor", __name__, url_prefix="/advisor")


def _require_advisor():
    if current_user.role != "advisor":
        abort(403)


@advisor_bp.route("/", methods=["GET"])
@login_required
def search():
    _require_advisor()
    number = request.args.get("student_number", "").strip()
    if number:
        student = User.query.filter_by(student_number=number, role="student").first()
        log_access(current_user, "search", student=student, student_number=number)
        if student:
            return redirect(url_for("advisor.student_overview", student_id=student.id))
        flash(f"No student found with student number {number}.", "error")
    return render_template("advisor_search.html", number=number)


@advisor_bp.route("/student/<int:student_id>")
@login_required
def student_overview(student_id):
    _require_advisor()
    student = User.query.filter_by(id=student_id, role="student").first_or_404()
    log_access(current_user, "view_student", student=student)
    mods = StudentModule.query.filter_by(student_id=student_id).all()
    summaries = [(sm, compute_progress(sm)[0]) for sm in mods]
    return render_template("student_overview.html", student=student, summaries=summaries,
                           progress_endpoint="advisor.student_progress",
                           back_url=url_for("advisor.search"), back_label="New search")


@advisor_bp.route("/student/<int:student_id>/module/<int:sm_id>")
@login_required
def student_progress(student_id, sm_id):
    _require_advisor()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != student_id:
        abort(404)
    log_access(current_user, "view_progress", student=sm.student, module_code=sm.module_code)
    result, _ = compute_progress(sm)
    rows = mark_rows(sm, result)
    own = [m for m, _s in rows if m.source == "student"]
    latest_feedback = own[0].feedback if own and own[0].feedback else None
    return render_template("progress_readonly.html", sm=sm, result=result, rows=rows,
                           categories=sm.categories, latest_feedback=latest_feedback,
                           back_url=url_for("advisor.student_overview", student_id=student_id),
                           back_label="Back to student")
