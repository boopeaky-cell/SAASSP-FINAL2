"""
Configuration for SAASSP.

All settings are read from the .env file in the project root (loaded
below with python-dotenv), falling back to the defaults shown here.

By default the app runs against a local SQLite file so it can be tried
out with zero setup. Set DATABASE_URL in .env to point it at MySQL instead, e.g.

    DATABASE_URL=mysql+pymysql://saassp_user:password@localhost/saassp_app

matching the MySQL deployment described in SAASSP_SDD.docx Section 2
(Deployment Diagram) and Section 5.4 of the proposal's technology table.
"""
import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Load .env from the project root into os.environ
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'saassp.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # AI Feedback Service (see SAASSP_SDD.docx Section 3.1, AI Feedback Service).
    # Groq is tried first (fast, generous free-tier limits); Gemini is only a
    # fallback for when Groq is unavailable or its call fails.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
    
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

    # E-mail (password reset links). Leave MAIL_SERVER empty to just print the
    # e-mail in the terminal instead of sending it. See backend/mailer.py.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587") or 587)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "")

    # Six fixed threshold bands - see SAASSP_SRS.docx Section 3.3.1
    THRESHOLD_BANDS = [
        (75, 100.0001, "very_safe", "Very safe / excellent"),
        (70, 75, "safe", "Safe"),
        (60, 70, "generally_safe", "Generally safe"),
        (50, 60, "pass_some_risk", "Pass, some risk"),
        (40, 50, "danger", "Danger, at risk of failing"),
        (0, 40, "critical", "Critical / failing"),
    ]

    ASSESSMENT_CATEGORIES = ["Tests", "Assignments", "Quizzes", "Practicals", "Tutorials"]
