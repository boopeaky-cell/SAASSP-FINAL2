"""
See SAASSP_SRS.docx Section 3.2.1, Login (Student), Section 3.2.7 Login
(Academic Staff), and Section 3.2.9 Login (Administrator).

Students log in with their student number. Academic Staff and System
Administrator accounts continue to use email, since they are created by an
administrator (see backend/admin.py) rather than self-registered.
"""
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from backend import db
from backend.models import User
from backend.mailer import send_email
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

RESET_MAX_AGE = 30 * 60   # reset links work for 30 minutes

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
    """Self-service sign-up for Students and Lecturers (Academic Staff).

    A self-registered lecturer can see NOTHING until an administrator assigns
    them their module(s), so signing up as a lecturer gives no access to any
    student data by itself. System Administrator accounts can never be
    self-created - only an existing administrator can create them."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        account_type = request.form.get("account_type", "student")
        name = request.form.get("name", "").strip()
        student_number = request.form.get("student_number", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        def again(message):
            flash(message, "error")
            return render_template("register.html", form=request.form)

        if account_type not in ("student", "academic_staff"):
            return again("Choose whether you are a student or a lecturer.")
        if not name or not password:
            return again("Please fill in your name and password.")
        if len(password) < 6:
            return again("The password must be at least 6 characters.")

        if account_type == "student":
            if not student_number:
                return again("Students need a student number.")
            if User.query.filter_by(student_number=student_number).first():
                return again("An account with that student number already exists.")
        else:
            student_number = ""
            if not email or "@" not in email:
                return again("Lecturers log in with an e-mail address, so it is required.")

        if email and User.query.filter_by(email=email).first():
            return again("An account with that email address already exists.")

        user = User(name=name, student_number=student_number or None, email=email or None,
                    role=account_type)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        if account_type == "student":
            flash("Account created. Let's select your programme and modules.", "success")
            return redirect(url_for("student.select_programme"))
        flash("Lecturer account created. An administrator must now assign you your module(s) "
              "before you can see anything.", "info")
        return redirect(url_for("index"))

    return render_template("register.html", form=None)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


def _reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="saassp-password-reset")


def _make_reset_token(user):
    # Includes a slice of the current password hash, so the link stops working
    # the moment the password changes (single use).
    return _reset_serializer().dumps({"id": user.id, "h": user.password_hash[-16:]})


def _user_from_reset_token(token):
    try:
        data = _reset_serializer().loads(token, max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("id"))
    if user is None or user.password_hash[-16:] != data.get("h"):
        return None
    return user


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Student number / email in, reset link out - sent to the e-mail address on the account."""
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        user = None
        if identifier:
            user = User.query.filter_by(student_number=identifier).first() or \
                   User.query.filter_by(email=identifier.lower()).first()
        if user and user.email:
            link = url_for("auth.reset_password", token=_make_reset_token(user), _external=True)
            send_email(
                user.email, "SAASSP password reset",
                f"Hello {user.name},\n\nSomeone asked to reset the SAASSP password for this account.\n"
                f"Open this link within 30 minutes to choose a new password:\n\n{link}\n\n"
                f"If you did not ask for this, ignore this message - your password has not changed.\n")
        # Same answer whether or not the account exists, so nobody can use this page
        # to find out which student numbers are registered.
        flash("If that account has an e-mail address on file, a reset link has been sent to it. "
              "The link works for 30 minutes. No e-mail on file? Ask your administrator to reset it.", "info")
        return redirect(url_for("auth.login"))
    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    user = _user_from_reset_token(token)
    if user is None:
        flash("This reset link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new) < 6:
            flash("The new password must be at least 6 characters.", "error")
        elif new != confirm:
            flash("The two passwords don't match.", "error")
        else:
            user.set_password(new)
            db.session.commit()
            flash("Password changed. You can log in with it now.", "success")
            return redirect(url_for("auth.login"))
    return render_template("reset_password.html", token=token)


@auth_bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    """Any logged-in user can set their own password and the e-mail used for resets."""
    if request.method == "POST":
        if request.form.get("action") == "email":
            email = request.form.get("email", "").strip().lower()
            other = User.query.filter_by(email=email).first() if email else None
            if not email or "@" not in email:
                flash("Enter a valid e-mail address.", "error")
            elif other and other.id != current_user.id:
                flash("That e-mail address is already used by another account.", "error")
            else:
                current_user.email = email
                db.session.commit()
                flash("E-mail saved. Password reset links will be sent there.", "success")
            return redirect(url_for("auth.change_password"))

        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not current_user.check_password(current):
            flash("Your current password is not correct.", "error")
        elif len(new) < 6:
            flash("The new password must be at least 6 characters.", "error")
        elif new != confirm:
            flash("The two new passwords don't match.", "error")
        else:
            current_user.set_password(new)
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("index"))
    return render_template("change_password.html")
