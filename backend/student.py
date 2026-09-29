"""
Implements SAASSP_SRS.docx use cases:
3.2.2 Register and Select Programme and Modules
3.2.3 Configure Assessment Structure
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
    AssessmentCategoryConfig, AssessmentMark, FeedbackMessage,
)
from backend.dp_engine import (
    CategoryConfig, MarkRecord, calculate_progress, classify_trend, weighting_sum,
    marks_by_category_from_student_module,
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
        flash("Modules saved. Configure each module's assessment structure to start tracking DP progress.", "success")
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
        categories = [
            CategoryConfig(c.category_name, c.dp_weighting, c.total_assessments, c.recorded_count)
            for c in sm.categories
        ]
        marks_by_category = _marks_by_category(sm)
        configured = len(categories) > 0
        if configured:
            result = calculate_progress(categories, marks_by_category, current_app.config["THRESHOLD_BANDS"])
        else:
            result = None
        summaries.append((sm, result, configured))

    return render_template("dashboard.html", summaries=summaries)


def _marks_by_category(student_module):
    return marks_by_category_from_student_module(student_module)


# ---------------------------------------------------------------------
# 3.2.3 Configure Assessment Structure
# ---------------------------------------------------------------------

@student_bp.route("/module/<int:sm_id>/configure", methods=["GET", "POST"])
@login_required
def configure_assessment(sm_id):
    _require_student()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != current_user.id:
        abort(403)

    categories = current_app.config["ASSESSMENT_CATEGORIES"]
    existing = {c.category_name: c for c in sm.categories}

    if request.method == "POST":
        rows = []
        total_weight = 0
        for cat_name in categories:
            weight = int(request.form.get(f"weight_{cat_name}", 0) or 0)
            total = int(request.form.get(f"total_{cat_name}", 0) or 0)
            recorded = int(request.form.get(f"recorded_{cat_name}", 0) or 0)
            rows.append((cat_name, weight, total, recorded))
            total_weight += weight

        if total_weight != 100:
            flash(f"Category weightings must sum to 100% (currently {total_weight}%).", "error")
            return render_template("configure_assessment.html", sm=sm, categories=categories,
                                    existing=existing, form_values=request.form)

        for cat_name, weight, total, recorded in rows:
            if recorded > total and weight > 0:
                flash(f"{cat_name}: recorded count cannot exceed total assessments.", "error")
                return render_template("configure_assessment.html", sm=sm, categories=categories,
                                        existing=existing, form_values=request.form)

        # replace configuration
        AssessmentCategoryConfig.query.filter_by(student_module_id=sm.id).delete()
        for cat_name, weight, total, recorded in rows:
            db.session.add(AssessmentCategoryConfig(
                student_module_id=sm.id, category_name=cat_name,
                dp_weighting=weight, total_assessments=total, recorded_count=recorded,
            ))
        db.session.commit()
        flash("Assessment structure saved.", "success")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    return render_template("configure_assessment.html", sm=sm, categories=categories,
                            existing=existing, form_values=None)


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

    if not sm.categories:
        return redirect(url_for("student.configure_assessment", sm_id=sm.id))

    categories = [
        CategoryConfig(c.category_name, c.dp_weighting, c.total_assessments, c.recorded_count)
        for c in sm.categories
    ]
    marks_by_category = _marks_by_category(sm)
    result = calculate_progress(categories, marks_by_category, current_app.config["THRESHOLD_BANDS"])

    recorded_ids = set(result.recorded_mark_ids)
    marks_sorted = sorted(sm.marks, key=lambda m: m.sequence, reverse=True)
    latest_feedback = None
    if marks_sorted and marks_sorted[0].feedback:
        latest_feedback = marks_sorted[0].feedback

    return render_template(
        "module_detail.html", sm=sm, result=result, categories=sm.categories,
        marks=marks_sorted, recorded_ids=recorded_ids, latest_feedback=latest_feedback,
        assessment_categories=[c.category_name for c in sm.categories if c.dp_weighting > 0],
    )


@student_bp.route("/module/<int:sm_id>/upload", methods=["POST"])
@login_required
def upload_mark(sm_id):
    _require_student()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != current_user.id:
        abort(403)

    category_name = request.form.get("category_name")
    try:
        percentage = float(request.form.get("percentage", ""))
    except ValueError:
        flash("Enter a valid percentage.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    if percentage < 0 or percentage > 100:
        flash("Mark must be between 0 and 100.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    cat_config = next((c for c in sm.categories if c.category_name == category_name), None)
    if cat_config is None:
        flash("Unknown assessment category.", "error")
        return redirect(url_for("student.module_detail", sm_id=sm.id))

    next_sequence = (db.session.query(db.func.max(AssessmentMark.sequence))
                      .filter_by(student_module_id=sm.id).scalar() or 0) + 1

    mark = AssessmentMark(
        student_module_id=sm.id, category_name=category_name,
        percentage_achieved=percentage, sequence=next_sequence,
    )
    db.session.add(mark)
    db.session.commit()

    # Recalculate DP progress (SDD 3.1, CalculateProgress) and refresh
    # each mark's recorded_towards_dp flag.
    categories = [
        CategoryConfig(c.category_name, c.dp_weighting, c.total_assessments, c.recorded_count)
        for c in sm.categories
    ]
    marks_by_category = _marks_by_category(sm)
    result = calculate_progress(categories, marks_by_category, current_app.config["THRESHOLD_BANDS"])
    recorded_ids = set(result.recorded_mark_ids)
    for m in sm.marks:
        m.recorded_towards_dp = m.id in recorded_ids
    db.session.commit()

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
        api_key=current_app.config["GEMINI_API_KEY"],
        model_name=current_app.config["GEMINI_MODEL"],
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
