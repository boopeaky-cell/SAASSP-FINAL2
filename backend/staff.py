"""
Academic Staff (lecturer) side of SAASSP.

A lecturer can only ever see and change the module(s) an administrator has
assigned to them (backend/models.py LecturerModule). Anything outside those
modules answers 404, as if it did not exist. From here a lecturer can:

  * set the module's assessment structure (weightings etc.) and change it at
    any time - every student's DP progress is recalculated from the new
    structure the next time it is shown;
  * upload a CSV of real marks for one assessment (e.g. "Test 1"); these
    replace the student's own entry for that assessment (correction);
  * search for a student, by student number, among their own modules and
    view that student's progress (read-only).
"""
import csv
import io

from flask import (
    Blueprint, render_template, redirect, url_for, request, flash, abort, current_app,
)
from flask_login import login_required, current_user

from backend import db
from backend.models import (
    User, StudentModule, Module, ModuleAssessmentConfig, ModuleAssessment, AssessmentMark, LecturerMark,
)
from backend.access_log import log_access
from backend.progress import (
    compute_progress, mark_rows, refresh_recorded_flags, next_sequence, sync_lecturer_marks,
)

import re
from datetime import datetime

STUDENT_NUMBER_RE = re.compile(r"^[A-Za-z0-9\-]{3,20}$")

staff_bp = Blueprint("staff", __name__, url_prefix="/staff")

MAX_CSV_ROWS = 3000


def _require_staff():
    if current_user.role != "academic_staff":
        abort(403)


def _assigned_module_codes():
    """Module codes this logged-in lecturer is assigned to teach."""
    return {lm.module_code for lm in current_user.taught_modules}


def _my_module_or_404(module_code):
    """The lecturer's own module, or 404 for anything else."""
    if module_code not in _assigned_module_codes():
        abort(404)
    return Module.query.get_or_404(module_code)


def _students_in_modules(codes):
    if not codes:
        return []
    return (
        User.query.join(StudentModule, StudentModule.student_id == User.id)
        .filter(User.role == "student", StudentModule.module_code.in_(codes))
        .distinct().order_by(User.name).all()
    )


# ---------------------------------------------------------------------
# Home: my modules + student search
# ---------------------------------------------------------------------

@staff_bp.route("/students")
@login_required
def student_list():
    _require_staff()
    codes = _assigned_module_codes()
    query = request.args.get("student_number", "").strip()

    modules = []
    for code in sorted(codes):
        module = Module.query.get(code)
        modules.append({
            "module": module,
            "configured": ModuleAssessmentConfig.query.filter_by(module_code=code).count() > 0,
            "student_count": StudentModule.query.filter_by(module_code=code).count(),
        })

    if query:
        # Search is limited to students enrolled in THIS lecturer's modules.
        students = [s for s in _students_in_modules(codes) if query.lower() in (s.student_number or "").lower()]
    else:
        students = _students_in_modules(codes)

    return render_template("staff_students.html", students=students, modules=modules,
                           query=query, no_modules_assigned=not codes)


@staff_bp.route("/students/<int:student_id>")
@login_required
def student_modules(student_id):
    _require_staff()
    codes = _assigned_module_codes()
    student = User.query.filter_by(id=student_id, role="student").first_or_404()
    mods = (
        StudentModule.query.filter_by(student_id=student_id)
        .filter(StudentModule.module_code.in_(codes) if codes else False)
        .all()
    )
    if not mods:
        abort(404)   # not a student of this lecturer: don't reveal they exist
    log_access(current_user, "view_student", student=student)
    summaries = [(sm, compute_progress(sm)[0]) for sm in mods]
    return render_template("student_overview.html", student=student, summaries=summaries,
                           progress_endpoint="staff.student_progress",
                           back_url=url_for("staff.student_list"), back_label="Back to students")


@staff_bp.route("/students/<int:student_id>/module/<int:sm_id>")
@login_required
def student_progress(student_id, sm_id):
    _require_staff()
    codes = _assigned_module_codes()
    sm = StudentModule.query.get_or_404(sm_id)
    if sm.student_id != student_id or sm.module_code not in codes:
        abort(404)

    log_access(current_user, "view_progress", student=sm.student, module_code=sm.module_code)
    result, _ = compute_progress(sm)
    rows = mark_rows(sm, result)
    own = [m for m, _s in rows if m.source == "student"]
    latest_feedback = own[0].feedback if own and own[0].feedback else None

    return render_template("progress_readonly.html", sm=sm, result=result, rows=rows,
                           categories=sm.categories, latest_feedback=latest_feedback,
                           back_url=url_for("staff.student_modules", student_id=student_id),
                           back_label="Back to modules")


# ---------------------------------------------------------------------
# One module: configuration, assessments, CSV upload
# ---------------------------------------------------------------------

@staff_bp.route("/module/<module_code>")
@login_required
def module_page(module_code):
    _require_staff()
    module = _my_module_or_404(module_code)
    configs = ModuleAssessmentConfig.query.filter_by(module_code=module_code).order_by(
        ModuleAssessmentConfig.id).all()
    assessments = ModuleAssessment.query.filter_by(module_code=module_code).order_by(
        ModuleAssessment.category_name, ModuleAssessment.label).all()
    sms = StudentModule.query.filter_by(module_code=module_code).all()
    for sm in sms:                      # make sure every enrolment shows the held marks
        sync_lecturer_marks(sm)
    db.session.commit()
    enrolled_numbers = {sm.student.student_number for sm in sms}
    counts, waiting_counts = {}, {}
    for a in assessments:
        rows = LecturerMark.query.filter_by(module_code=module_code, assessment_label=a.label).all()
        counts[a.id] = len(rows)
        waiting_counts[a.id] = sum(1 for r in rows if r.student_number not in enrolled_numbers)
    waiting_marks = sorted(
        [r for r in LecturerMark.query.filter_by(module_code=module_code).all()
         if r.student_number not in enrolled_numbers],
        key=lambda r: (r.student_number, r.assessment_label))
    students = sorted([(sm.student, compute_progress(sm)[0]) for sm in sms], key=lambda t: t[0].name)
    return render_template("staff_module.html", module=module, configs=configs, assessments=assessments,
                           counts=counts, waiting_counts=waiting_counts, waiting_marks=waiting_marks,
                           students=students,
                           categories=current_app.config["ASSESSMENT_CATEGORIES"])


@staff_bp.route("/module/<module_code>/config", methods=["GET", "POST"])
@login_required
def configure_module(module_code):
    _require_staff()
    module = _my_module_or_404(module_code)
    categories = current_app.config["ASSESSMENT_CATEGORIES"]
    existing = {c.category_name: c for c in
                ModuleAssessmentConfig.query.filter_by(module_code=module_code).all()}

    def render(form_values=None):
        return render_template("staff_module_config.html", module=module, categories=categories,
                               existing=existing, form_values=form_values)

    if request.method == "POST":
        rows = []
        total_weight = 0
        try:
            for cat_name in categories:
                weight = int(request.form.get(f"weight_{cat_name}", 0) or 0)
                total = int(request.form.get(f"total_{cat_name}", 0) or 0)
                recorded = int(request.form.get(f"recorded_{cat_name}", 0) or 0)
                if min(weight, total, recorded) < 0 or weight > 100:
                    raise ValueError
                rows.append((cat_name, weight, total, recorded))
                total_weight += weight
        except ValueError:
            flash("Enter whole numbers only (weighting 0-100, counts 0 or more).", "error")
            return render(request.form)

        if total_weight != 100:
            flash(f"Category weightings must sum to 100% (currently {total_weight}%).", "error")
            return render(request.form)

        for cat_name, weight, total, recorded in rows:
            if weight > 0 and recorded > total:
                flash(f"{cat_name}: recorded count cannot exceed total assessments.", "error")
                return render(request.form)
            if weight > 0 and recorded < 1:
                flash(f"{cat_name}: a category with a weighting needs at least 1 recorded assessment.", "error")
                return render(request.form)

        for cat_name, weight, total, recorded in rows:
            cfg = existing.get(cat_name)
            if cfg is None:
                cfg = ModuleAssessmentConfig(module_code=module_code, category_name=cat_name)
                db.session.add(cfg)
            cfg.dp_weighting, cfg.total_assessments, cfg.recorded_count = weight, total, recorded
            cfg.updated_by = current_user.id
        db.session.flush()

        # Recalculate: refresh each enrolled student's "recorded" flags against the new structure.
        sms = StudentModule.query.filter_by(module_code=module_code).all()
        for sm in sms:
            refresh_recorded_flags(sm)
        db.session.commit()
        flash(f"Assessment structure saved. Progress recalculated for {len(sms)} student(s).", "success")
        return redirect(url_for("staff.module_page", module_code=module_code))

    return render()


def _parse_mark(raw):
    raw = (raw or "").strip().replace("%", "").replace(",", ".")
    value = float(raw)          # ValueError if not a number
    if value != value or value < 0 or value > 100:   # NaN or out of range
        raise ValueError
    return value


@staff_bp.route("/module/<module_code>/upload", methods=["POST"])
@login_required
def upload_marks(module_code):
    _require_staff()
    module = _my_module_or_404(module_code)

    category = request.form.get("category_name", "")
    label = request.form.get("assessment_label", "").strip()
    file = request.files.get("csv_file")
    back = redirect(url_for("staff.module_page", module_code=module_code))

    if category not in current_app.config["ASSESSMENT_CATEGORIES"]:
        flash("Choose which category this assessment belongs to.", "error")
        return back
    if not label or len(label) > 60:
        flash("Give the assessment a name (e.g. Test 1), up to 60 characters.", "error")
        return back
    if not file or file.filename == "":
        flash("Choose a CSV file first.", "error")
        return back

    cfg = ModuleAssessmentConfig.query.filter_by(module_code=module_code, category_name=category).first()
    if cfg is None or cfg.dp_weighting <= 0:
        flash(f"{category} has no weighting in this module's structure. Set it under "
              f"\"Assessment structure\" first, otherwise these marks would not count.", "error")
        return back

    existing_label = ModuleAssessment.query.filter_by(module_code=module_code, label=label).first()
    if existing_label and existing_label.category_name != category:
        flash(f"\"{label}\" already exists under {existing_label.category_name}. Use a different name.", "error")
        return back

    try:
        text = file.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("That file isn't a plain CSV (UTF-8 text). Save it as CSV and try again.", "error")
        return back

    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
    number_col = headers.get("student_number") or headers.get("student number") or headers.get("studentnumber")
    mark_col = (headers.get("mark") or headers.get("percentage") or headers.get("percentage_achieved")
                or headers.get("score"))
    if not number_col or not mark_col:
        flash("The CSV needs two columns named student_number and mark.", "error")
        return back

    enrolled = {
        sm.student.student_number: sm
        for sm in StudentModule.query.filter_by(module_code=module_code).all()
        if sm.student.student_number
    }

    parsed, problems = {}, []
    for i, row in enumerate(reader, start=2):
        if i - 1 > MAX_CSV_ROWS:
            problems.append(f"Stopped after {MAX_CSV_ROWS} rows.")
            break
        number = (row.get(number_col) or "").strip()
        if not number and not (row.get(mark_col) or "").strip():
            continue   # blank line
        if not STUDENT_NUMBER_RE.match(number):
            problems.append(f"Row {i}: \"{number or '(blank)'}\" is not a valid student number.")
            continue
        try:
            parsed[number] = _parse_mark(row.get(mark_col))   # a repeated number: last row wins
        except ValueError:
            problems.append(f"Row {i}: \"{row.get(mark_col)}\" is not a mark between 0 and 100.")

    if not parsed:
        flash("No valid rows were found, so nothing was changed. " + " ".join(problems[:3]), "error")
        return back

    if existing_label is None:
        db.session.add(ModuleAssessment(module_code=module_code, category_name=category, label=label))

    # Every valid mark is kept against its student number - whether or not that
    # student is enrolled, registered, or later deletes their account.
    added = updated = waiting = 0
    for number, value in parsed.items():
        held = LecturerMark.query.filter_by(
            module_code=module_code, student_number=number, assessment_label=label).first()
        if held:
            held.percentage = value
            held.category_name = category
            held.uploaded_by = current_user.name
            held.uploaded_at = datetime.utcnow()
            updated += 1
        else:
            db.session.add(LecturerMark(
                module_code=module_code, student_number=number, category_name=category,
                assessment_label=label, percentage=value, uploaded_by=current_user.name))
            added += 1
        if number in enrolled:
            db.session.flush()
            sync_lecturer_marks(enrolled[number])
        else:
            waiting += 1
    db.session.commit()

    msg = f"{label}: {added} mark(s) added, {updated} updated, {len(problems)} row(s) skipped."
    if waiting:
        msg += (f" {waiting} belong to students not enrolled in {module_code} yet - they are saved "
                f"and will show as soon as that student enrols.")
    flash(msg, "success" if not problems else "info")
    for p in problems[:8]:
        flash(p, "info")
    if len(problems) > 8:
        flash(f"...and {len(problems) - 8} more skipped rows.", "info")
    return back


@staff_bp.route("/module/<module_code>/assessment/<int:assessment_id>/delete", methods=["POST"])
@login_required
def delete_assessment(module_code, assessment_id):
    """Undo an upload: removes the lecturer's marks for this assessment (the
    students' own entries stay and count again)."""
    _require_staff()
    _my_module_or_404(module_code)
    a = ModuleAssessment.query.filter_by(id=assessment_id, module_code=module_code).first_or_404()
    sms = StudentModule.query.filter_by(module_code=module_code).all()
    removed = 0
    for sm in sms:
        for m in list(sm.marks):
            if m.source == "lecturer" and m.assessment_label == a.label:
                db.session.delete(m)
                removed += 1
    LecturerMark.query.filter_by(module_code=module_code, assessment_label=a.label).delete()
    db.session.delete(a)
    db.session.flush()
    for sm in sms:
        refresh_recorded_flags(sm)
    db.session.commit()
    flash(f"Removed {a.label} ({removed} lecturer mark(s)). Students' own entries count again.", "success")
    return redirect(url_for("staff.module_page", module_code=module_code))
