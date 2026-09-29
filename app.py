"""
SAASSP - Smart Academic Advisory & Student Success Platform
Main application file.

This is the single entry point of the system. It:
  1. Loads settings from the .env file (via config.py / python-dotenv)
  2. Creates the Flask web application
  3. Connects the FRONTEND  -> static/            (HTML pages, CSS, images)
     to the     BACKEND   -> backend/           (routes, models, DP engine, AI feedback)
  4. Sets up the database (Flask-SQLAlchemy) and logins (Flask-Login)
  5. Starts the web server

Start the app with either of:
    python app.py
    flask run            (Flask finds create_app() in this file automatically)

Frameworks / libraries used:
    Flask              - web framework (routing, request handling, Jinja2 templates)
    Flask-SQLAlchemy   - database ORM (SQLite by default, MySQL via DATABASE_URL)
    Flask-Login        - user sessions / login_required
    python-dotenv      - reads the .env file
    google-genai       - Gemini API client used by backend/feedback_service.py
"""
import os

from flask import Flask, abort, redirect, request, url_for
from flask_login import current_user

from backend import db, login_manager

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# ---------------------------------------------------------------------------
# Frontend location
#   static/templates/  -> HTML pages (Jinja2), rendered by the backend routes
#   static/css/        -> stylesheets     (served at /static/css/...)
#   static/img/        -> images          (served at /static/img/...)
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(STATIC_DIR, "templates")


def create_app(config_object=None):
    app = Flask(
        __name__,
        static_folder=STATIC_DIR,        # CSS / images / JS the browser downloads
        static_url_path="/static",
        template_folder=TEMPLATES_DIR,   # HTML pages the backend renders
    )

    # ---- Configuration (values come from .env, see config.py) -------------
    if config_object is None:
        from config import Config
        config_object = Config
    app.config.from_object(config_object)

    # ---- Framework extensions --------------------------------------------
    db.init_app(app)
    login_manager.init_app(app)

    from backend.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ---- Backend routes (each blueprint renders pages from static/templates)
    from backend.auth import auth_bp
    from backend.student import student_bp
    from backend.staff import staff_bp
    from backend.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(admin_bp)

    # The HTML templates live inside static/, but they are server-side pages,
    # not files to download - so don't serve their raw source over /static.
    @app.before_request
    def block_raw_templates():
        if request.path.startswith("/static/templates"):
            abort(404)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            if current_user.role == "student":
                return redirect(url_for("student.dashboard"))
            if current_user.role == "academic_staff":
                return redirect(url_for("staff.student_list"))
            if current_user.role == "admin":
                return redirect(url_for("admin.programmes"))
        return redirect(url_for("auth.login"))

    # ---- Database tables -------------------------------------------------
    with app.app_context():
        db.create_all()

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(
        host=os.environ.get("FLASK_RUN_HOST", "0.0.0.0"),
        port=int(os.environ.get("FLASK_RUN_PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
    )
