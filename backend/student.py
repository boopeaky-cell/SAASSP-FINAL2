"""
Implements SAASSP_SRS.docx use cases:
3.2.2 Register and Select Programme and Modules
3.2.3 Configure Assessment Structure  (now done by the LECTURER - see backend/staff.py)
3.2.4 Upload Assessment Mark
3.2.5 Receive AI-Assisted Feedback (invoked from upload_mark)
3.2.6 View DP Progress Dashboard
"""
from flask import (
    Blueprint, render_template, redirect, url_for, request, flash,
    session, current_app, abort
)
from flask_login import login_required, current_user

from backend import db
from backend.models import (
    Programme, Module, ProgrammeModule, StudentModule,
    ModuleAssessment, AssessmentMark, FeedbackMessage,
)
from backend.dp_engine import classify_trend
from backend.progress import (
    compute_progress, mark_rows, refresh_recorded_flags, next_sequence,
)
from backend.feedback_service import generate_feedback

student_bp = Blueprint("student", __name__, url_prefix="/student")


def _require_student():
    if current_user.role != "student":
        abort(403)


# ---------------------------------------------------------------------
# 3.2.2 Register and Select Programme and Modules
# ---------------------------------------------------------------------

@student_bp.route("/select-programme", methods=["GET", "POST"])
@login_required
def select_programme():
    _require_student()
    if request.method == "POST":
        programme_code = request.form.get("programme_code")
        if not Programme.query.get(programme_code):
            flash("Please choose a valid programme.", "error")
            return redirect(url_for("student.select_programme"))
        session["pending_programme_code"] = programme_code
        return redirect(url_for("student.select_modules"))

    programmes = Programme.query.order_by(Programme.programme_name).all()
    return render_template("select_programme.html", programmes=programmes)


@student_bp.route("/select-modules", methods=["GET", "POST"])
@login_required
def select_modules():
    _require_student()
    programme_code = session.get("pending_programme_code")
    if not programme_code:
        flash("Choose your programme first.", "info")
        return redirect(url_for("student.select_programme"))

    programme = Programme.query.get_or_404(programme_code)
    links = ProgrammeModule.query.filter_by(programme_code=programme_code).all()

    if request.method == "POST":
        chosen = request.form.getlist("module_code")
        if not chosen:
            flash("Select at least one module.", "error")
            return render_template("select_modules.html", programme=programme, links=links)

        for module_code in chosen:
            exists = StudentModule.query.filter_by(
                student_id=current_user.id, module_code=module_code
            ).first()
            if exists:
                continue
            db.session.add(StudentModule(
                student_id=current_user.id,
                programme_code=programme_code,
                module_code=module_code,
            ))
        db.session.commit()
        session.pop("pending_programme_code", None)
        flash("Modules saved. Your lecturer sets each module's assessment structure; once they have, enter your marks to track DP progress.", "success")
        return redirect(url_for("student.dashboard"))

    return render_template("select_modules.html", programme=programme, links=links)


# ---------------------------------------------------------------------
# 3.2.6 View DP Progress Dashboard  (home screen: list of modules)
# ---------------------------------------------------------------------

@student_bp.route("/dashboard")
@login_required
def dashboard():
    _require_student()
    student_modules = StudentModule.query.filter_by(student_id=current_user.id).all()

    summaries = []
    for sm in student_modules:
        result, _ = compute_progress(sm)   # always from the lecturer's CURRENT configuration
        summaries.append((sm, result, result is not None))

    return render_template("dashboard.html", summaries=summaries)


# ---------------------------------------------------------------------
# 3.2.4 Upload Assessment Mark + 3.2.5 Receive AI-Assisted Feedback
# ---------------------------------------------------------------------

@student_bp.route("/module/<int:sm_id>")
@login_required
def module_detail(sm_id):
    _require_student()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != current_user.id:
        abort(403)

    categories = sm.categories
    result, _ = compute_progress(sm)

    rows = mark_rows(sm, result)
    marks_sorted = [m for m, _status in rows]
    latest_feedback = None
    own = [m for m in marks_sorted if m.source == "student"]
    if own and own[0].feedback:
        latest_feedback = own[0].feedback

    assessments = (ModuleAssessment.query.filter_by(module_code=sm.module_code)
                   .order_by(ModuleAssessment.category_name, ModuleAssessment.label).all())
    active_categories = [c.category_name for c in categories if c.dp_weighting > 0]

    return render_template(
        "module_detail.html", sm=sm, result=result, categories=categories,
        rows=rows, latest_feedback=latest_feedback,
        assessment_categories=active_categories,
        assessments=[a for a in assessments if a.category_name in active_categories],
    )


@student_bp.route("/module/<int:sm_id>/upload", methods=["POST"])
@login_required
def upload_mark(sm_id):
    _require_student()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != current_user.id:
        abort(403)

    if not sm.categories:
        flash("Your lecturer hasn't set up this module's assessment structure yet.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    # The form sends "Category::Assessment label" (label may be empty = "other").
    choice = request.form.get("assessment", "")
    category_name, _sep, label = choice.partition("::")
    label = label.strip() or None
    try:
        percentage = float(request.form.get("percentage", ""))
    except ValueError:
        flash("Enter a valid percentage.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    if percentage < 0 or percentage > 100:
        flash("Mark must be between 0 and 100.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    cat_config = next((c for c in sm.categories if c.category_name == category_name), None)
    if cat_config is None or cat_config.dp_weighting <= 0:
        flash("Unknown assessment category.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    if label:
        known = ModuleAssessment.query.filter_by(
            module_code=sm.module_code, category_name=category_name, label=label).first()
        if not known:
            label = None   # ignore a label the lecturer never defined

    mark = AssessmentMark(
        student_module_id=sm.id, category_name=category_name,
        percentage_achieved=percentage, sequence=next_sequence(sm.id),
        source="student", assessment_label=label,
    )
    db.session.add(mark)
    db.session.commit()

    # Recalculate DP progress and refresh each mark's recorded flag.
    result = refresh_recorded_flags(sm)
    db.session.commit()
    result, marks_by_category = compute_progress(sm)
    recorded_ids = set(result.recorded_mark_ids)

    # Classify trend for the updated category (SDD 3.1, ClassifyTrend)
    category_history = marks_by_category.get(category_name, [])
    trend = classify_trend(category_history)

    # Invoke AI Feedback Service (SRS 3.2.5)
    module = sm.module
    history_payload = [
        {"percentage": float(h.percentage), "recorded": h.id in recorded_ids}
        for h in sorted(category_history, key=lambda m: m.sequence)
    ]
    feedback_text = generate_feedback(
        context={
            "module_code": module.module_code,
            "module_name": module.module_name,
            "category": category_name,
            "mark_percentage": percentage,
            "progress_percentage": result.progress_percentage,
            "threshold_band_label": result.threshold_band_label,
            "trend": trend,
            "category_history": history_payload,
        },
        groq_api_key=current_app.config["GROQ_API_KEY"],
        groq_model=current_app.config["GROQ_MODEL"],
        gemini_api_key=current_app.config["GEMINI_API_KEY"],
        gemini_model=current_app.config["GEMINI_MODEL"],
    )

    db.session.add(FeedbackMessage(
        mark_id=mark.id,
        dp_progress_percentage=result.progress_percentage,
        threshold_band=result.threshold_band_label,
        trend_classification=trend,
        generated_text=feedback_text,
    ))
    db.session.commit()

    if feedback_text:
        flash("Mark uploaded. New feedback is ready below.", "success")
    else:
        flash("Mark uploaded and DP progress updated. AI feedback wasn't available this time.", "info")
    return redirect(url_for("student.module_detail", sm_id=sm.id))
