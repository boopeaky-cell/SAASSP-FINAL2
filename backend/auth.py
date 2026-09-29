"""
See SAASSP_SRS.docx Section 3.2.1, Login (Student), Section 3.2.7 Login
(Academic Staff), and Section 3.2.9 Login (Administrator).

Students log in with their student number. Academic Staff and System
Administrator accounts continue to use email, since they are created by an
administrator (see backend/admin.py) rather than self-registered.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from backend import db
from backend.models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password = request.form.get("password", "")

        user = None
        if identifier:
            user = User.query.filter_by(student_number=identifier).first()
            if user is None:
                user = User.query.filter_by(email=identifier.lower()).first()

        if user is None or not user.check_password(password):
            flash("Invalid student number/email or password.", "error")
            return render_template("login.html")

        login_user(user)
        flash(f"Welcome back, {user.name.split(' ')[0]}.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Self-service sign-up. Always creates a Student account - see SRS
    3.2.1 Alternative Paths: "Academic Staff and System Administrator
    accounts cannot be self-created." """
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        student_number = request.form.get("student_number", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not student_number or not password:
            flash("Please fill in your name, student number and password.", "error")
            return render_template("register.html")

        if User.query.filter_by(student_number=student_number).first():
            flash("An account with that student number already exists.", "error")
            return render_template("register.html")

        if email and User.query.filter_by(email=email).first():
            flash("An account with that email address already exists.", "error")
            return render_template("register.html")

        user = User(name=name, student_number=student_number, email=email or None, role="student")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Account created. Let's select your programme and modules.", "success")
        return redirect(url_for("student.select_programme"))

    return render_template("register.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
