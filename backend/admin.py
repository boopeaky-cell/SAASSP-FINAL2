"""
See SAASSP_SRS.docx Section 3.2.8, Manage Programme and Module Data, and
Section 3.2.9, Manage User Accounts.
"""
import csv
import io
from flask import Blueprint, render_template, redirect, url_for, request, flash, abort
from flask_login import login_required, current_user

from backend import db
from backend.models import User, Programme, Module, ProgrammeModule, LecturerModule, AccessLog

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin():
    if current_user.role != "admin":
        abort(403)


@admin_bp.route("/programmes", methods=["GET", "POST"])
@login_required
def programmes():
    _require_admin()
    if request.method == "POST":
        kind = request.form.get("kind")
        file = request.files.get("csv_file")
        if not file or file.filename == "":
            flash("Choose a CSV file first.", "error")
            return redirect(url_for("admin.programmes"))

        reader = csv.DictReader(io.StringIO(file.stream.read().decode("utf-8-sig")))
        added, skipped = 0, 0

        if kind == "programmes":
            for row in reader:
                code = row.get("programme_code", "").strip()
                name = row.get("programme_name", "").strip()
                if not code or Programme.query.get(code):
                    skipped += 1
                    continue
                db.session.add(Programme(programme_code=code, programme_name=name))
                added += 1
        elif kind == "modules":
            for row in reader:
                code = row.get("module_code", "").strip()
                name = row.get("module_name", "").strip()
                if not code or Module.query.get(code):
                    skipped += 1
                    continue
                db.session.add(Module(module_code=code, module_name=name))
                added += 1
        elif kind == "links":
            for row in reader:
                p_code = row.get("programme_code", "").strip()
                m_code = row.get("module_code", "").strip()
                if not Programme.query.get(p_code) or not Module.query.get(m_code):
                    skipped += 1
                    continue  # SRS 3.2.8 Alternative Paths: reject unresolvable references
                exists = ProgrammeModule.query.filter_by(
                    programme_code=p_code, module_code=m_code
                ).first()
                if exists:
                    skipped += 1
                    continue
                db.session.add(ProgrammeModule(programme_code=p_code, module_code=m_code))
                added += 1
        else:
            flash("Unknown import type.", "error")
            return redirect(url_for("admin.programmes"))

        db.session.commit()
        flash(f"Import complete: {added} added, {skipped} skipped.", "success")
        return redirect(url_for("admin.programmes"))

    programmes = Programme.query.order_by(Programme.programme_code).all()
    modules_count = Module.query.count()
    links_count = ProgrammeModule.query.count()
    return render_template("admin_programmes.html", programmes=programmes,
                            modules_count=modules_count, links_count=links_count)


@admin_bp.route("/users", methods=["GET", "POST"])
@login_required
def users():
    _require_admin()
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form.get("name", "").strip()
            role = request.form.get("role")
            password = request.form.get("password", "")
            student_number = request.form.get("student_number", "").strip()
            email = request.form.get("email", "").strip().lower()

            if role not in ("student", "academic_staff", "admin"):
                flash("Choose a valid role.", "error")
            elif role == "student" and not student_number:
                flash("A Student account needs a student number.", "error")
            elif role != "student" and not email:
                flash("Academic Staff and System Administrator accounts need an email address.", "error")
            elif student_number and User.query.filter_by(student_number=student_number).first():
                flash("A user with that student number already exists.", "error")
            elif email and User.query.filter_by(email=email).first():
                flash("A user with that email already exists.", "error")
            else:
                u = User(name=name, role=role,
                         student_number=student_number or None,
                         email=email or None)
                u.set_password(password)
                db.session.add(u)
                db.session.commit()
                flash(f"Account created for {name}.", "success")

        elif action == "update_role":
            user_id = request.form.get("user_id")
            new_role = request.form.get("role")
            u = User.query.get(user_id)
            if u and new_role in ("student", "academic_staff", "admin"):
                u.role = new_role
                db.session.commit()
                flash(f"Updated {u.name}'s role to {new_role}.", "success")

        elif action == "assign_modules":
            user_id = request.form.get("user_id")
            module_codes = request.form.getlist("module_codes")
            u = User.query.get(user_id)
            if u and u.role == "academic_staff":
                LecturerModule.query.filter_by(lecturer_id=u.id).delete()
                for code in module_codes:
                    if Module.query.get(code):
                        db.session.add(LecturerModule(lecturer_id=u.id, module_code=code))
                db.session.commit()
                flash(f"Updated the module(s) {u.name} teaches.", "success")

        elif action == "reset_password":
            u = User.query.get(request.form.get("user_id"))
            new_pw = request.form.get("new_password", "")
            if not u:
                flash("User not found.", "error")
            elif len(new_pw) < 6:
                flash("Temporary password must be at least 6 characters.", "error")
            else:
                u.set_password(new_pw)
                db.session.commit()
                flash(f"Password reset for {u.name}. Give them the temporary password and ask them to change it "
                      f"(Change password, bottom of the menu).", "success")

        elif action == "delete":
            user_id = request.form.get("user_id")
            u = User.query.get(user_id)
            if u:
                if u.id == current_user.id:
                    flash("You can't remove your own account while logged in.", "error")
                else:
                    try:
                        db.session.delete(u)
                        db.session.commit()
                        flash(f"Deactivated (removed) {u.name}.", "success")
                    except Exception:
                        db.session.rollback()
                        flash(f"Could not remove {u.name} — related records are blocking it.", "error")

        return redirect(url_for("admin.users"))

    all_users = User.query.order_by(User.role, User.name).all()
    all_modules = Module.query.order_by(Module.module_code).all()
    assigned_by_lecturer = {
        u.id: {lm.module_code for lm in u.taught_modules}
        for u in all_users if u.role == "academic_staff"
    }
    return render_template("admin_users.html", users=all_users, all_modules=all_modules,
                            assigned_by_lecturer=assigned_by_lecturer)


@admin_bp.route("/access-log")
@login_required
def access_log():
    """Who looked at which student's record, and when (lecturers and advisors)."""
    _require_admin()
    q = request.args.get("student_number", "").strip()
    query = AccessLog.query
    if q:
        query = query.filter(AccessLog.student_number.contains(q))
    entries = query.order_by(AccessLog.created_at.desc()).limit(300).all()
    return render_template("admin_access_log.html", entries=entries, q=q)


@admin_bp.route("/users/<int:user_id>/modules", methods=["GET", "POST"])
@login_required
def lecturer_modules(user_id):
    """Assign the module(s) a lecturer teaches, on a page of its own with a search box."""
    _require_admin()
    lecturer = User.query.filter_by(id=user_id, role="academic_staff").first_or_404()
    if request.method == "POST":
        codes = set(request.form.getlist("module_codes"))
        LecturerModule.query.filter_by(lecturer_id=lecturer.id).delete()
        for code in codes:
            if Module.query.get(code):
                db.session.add(LecturerModule(lecturer_id=lecturer.id, module_code=code))
        db.session.commit()
        flash(f"Saved: {lecturer.name} now teaches {len(codes)} module(s).", "success")
        return redirect(url_for("admin.users"))
    assigned = {lm.module_code for lm in lecturer.taught_modules}
    all_modules = Module.query.order_by(Module.module_code).all()
    return render_template("admin_lecturer_modules.html", lecturer=lecturer,
                           all_modules=all_modules, assigned=assigned)
