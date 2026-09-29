"""
Data models. Field names and roles follow SAASSP_SDD.docx Section 4.1
(Data Field Types and Sizes) and Section 3.1's per-component "Resources"
lists as closely as SQLAlchemy allows.
"""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from backend import db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)          # UserID*#^
    name = db.Column(db.String(100), nullable=False)      # Name*
    # Students log in with their student number; Academic Staff and System
    # Administrator accounts (which are not self-registered) continue to use
    # email as their login identifier. Exactly one of the two is required,
    # enforced in backend/auth.py and backend/admin.py rather than at the schema
    # level, since the column is shared across all three roles.
    student_number = db.Column(db.String(20), unique=True, nullable=True)  # StudentNumber*#
    email = db.Column(db.String(100), unique=True, nullable=True)  # Email*#
    role = db.Column(db.String(20), nullable=False)       # Role*# : student|academic_staff|admin
    password_hash = db.Column(db.String(255), nullable=False)  # PasswordHash*^

    student_modules = db.relationship("StudentModule", backref="student", lazy=True,
                                       foreign_keys="StudentModule.student_id")
    taught_modules = db.relationship("LecturerModule", backref="lecturer", lazy=True,
                                      foreign_keys="LecturerModule.lecturer_id",
                                      cascade="all, delete-orphan")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    @property
    def login_identifier(self):
        """Whichever identifier this account actually logs in with."""
        return self.student_number if self.role == "student" else self.email

    def __repr__(self):
        return f"<User {self.login_identifier} ({self.role})>"


class Programme(db.Model):
    __tablename__ = "programmes"

    programme_code = db.Column(db.String(10), primary_key=True)  # ProgrammeCode*#
    programme_name = db.Column(db.String(150), nullable=False)   # ProgrammeName*

    modules = db.relationship("ProgrammeModule", backref="programme", lazy=True)


class Module(db.Model):
    __tablename__ = "modules"

    module_code = db.Column(db.String(10), primary_key=True)  # ModuleCode*#
    module_name = db.Column(db.String(150), nullable=False)   # ModuleName*


class ProgrammeModule(db.Model):
    """Link table sourced from programme_modules.csv."""
    __tablename__ = "programme_modules"

    id = db.Column(db.Integer, primary_key=True)
    programme_code = db.Column(db.String(10), db.ForeignKey("programmes.programme_code"), nullable=False)
    module_code = db.Column(db.String(10), db.ForeignKey("modules.module_code"), nullable=False)

    module = db.relationship("Module")

    __table_args__ = (db.UniqueConstraint("programme_code", "module_code", name="uq_programme_module"),)


class LecturerModule(db.Model):
    """Which module(s) an Academic Staff account teaches. See SRS 3.2.7,
    View Student Progress - Academic Staff must only see students in the
    module(s) assigned to them here (assigned by a System Administrator in
    Manage User Accounts)."""
    __tablename__ = "lecturer_modules"

    id = db.Column(db.Integer, primary_key=True)
    lecturer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    module_code = db.Column(db.String(10), db.ForeignKey("modules.module_code"), nullable=False)

    module = db.relationship("Module")

    __table_args__ = (db.UniqueConstraint("lecturer_id", "module_code", name="uq_lecturer_module"),)


class StudentModule(db.Model):
    """A module a Student has selected. See SRS 3.2.2, Register and Select
    Programme and Modules."""
    __tablename__ = "student_modules"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    programme_code = db.Column(db.String(10), db.ForeignKey("programmes.programme_code"), nullable=False)
    module_code = db.Column(db.String(10), db.ForeignKey("modules.module_code"), nullable=False)

    module = db.relationship("Module")
    programme = db.relationship("Programme")
    categories = db.relationship("AssessmentCategoryConfig", backref="student_module",
                                  lazy=True, cascade="all, delete-orphan")
    marks = db.relationship("AssessmentMark", backref="student_module",
                             lazy=True, cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("student_id", "module_code", name="uq_student_module"),)


class AssessmentCategoryConfig(db.Model):
    """See SRS 3.2.3, Configure Assessment Structure."""
    __tablename__ = "assessment_category_configs"

    id = db.Column(db.Integer, primary_key=True)
    student_module_id = db.Column(db.Integer, db.ForeignKey("student_modules.id"), nullable=False)
    category_name = db.Column(db.String(20), nullable=False)   # CategoryName*#
    dp_weighting = db.Column(db.Integer, nullable=False, default=0)     # DPWeighting*  (0-100)
    total_assessments = db.Column(db.Integer, nullable=False, default=0)  # TotalAssessments*
    recorded_count = db.Column(db.Integer, nullable=False, default=0)     # RecordedCount*

    __table_args__ = (db.UniqueConstraint("student_module_id", "category_name", name="uq_sm_category"),)


class AssessmentMark(db.Model):
    """See SRS 3.2.4, Upload Assessment Mark."""
    __tablename__ = "assessment_marks"

    id = db.Column(db.Integer, primary_key=True)                    # MarkID#^
    student_module_id = db.Column(db.Integer, db.ForeignKey("student_modules.id"), nullable=False)
    category_name = db.Column(db.String(20), nullable=False)
    percentage_achieved = db.Column(db.Numeric(5, 2), nullable=False)  # PercentageAchieved*
    date_uploaded = db.Column(db.DateTime, default=datetime.utcnow)    # DateUploaded^
    recorded_towards_dp = db.Column(db.Boolean, default=False)         # RecordedTowardsDP^
    sequence = db.Column(db.Integer, nullable=False, default=0)  # upload order, for trend history

    feedback = db.relationship("FeedbackMessage", backref="mark", uselist=False,
                                cascade="all, delete-orphan")


class FeedbackMessage(db.Model):
    """See SRS 3.2.5, Receive AI-Assisted Feedback."""
    __tablename__ = "feedback_messages"

    id = db.Column(db.Integer, primary_key=True)                       # FeedbackID#^
    mark_id = db.Column(db.Integer, db.ForeignKey("assessment_marks.id"), nullable=False)
    dp_progress_percentage = db.Column(db.Numeric(5, 2))                # DPProgressPercentage^
    threshold_band = db.Column(db.String(30))                           # ThresholdBand^
    trend_classification = db.Column(db.String(20))                    # TrendClassification^
    generated_text = db.Column(db.Text)                                 # GeneratedText^
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
