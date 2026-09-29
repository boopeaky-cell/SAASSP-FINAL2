"""
SAASSP - Smart Academic Advisory & Student Success Platform
Backend package.

Holds the shared framework extensions (database + login manager) that the
route modules, models and engines import. The Flask application itself is
created and wired up in ../app.py - see that file for how the frontend
(static/) and this backend are connected.

See SAASSP_SDD.docx Section 3 (Architecture Design) for the component
breakdown this package follows, and SAASSP_SRS.docx Section 3.2 for the
use cases each route implements.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "info"
