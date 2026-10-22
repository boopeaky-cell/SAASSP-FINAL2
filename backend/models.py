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
                                       foreign_keys="StudentModule.student_id",
                                       cascade="all, delete-orphan")
    chat_messages = db.relationship("ChatMessage", backref="student", lazy=True,
                                    foreign_keys="ChatMessage.student_id",
                                    cascade="all, delete-orphan")
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
    marks = db.relationship("AssessmentMark", backref="student_module",
                             lazy=True, cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("student_id", "module_code", name="uq_student_module"),)

    @property
    def categories(self):
        """The module's assessment structure. It belongs to the MODULE (set by
        the lecturer), not to the individual student, so every student in the
        module is calculated against the same, current configuration."""
        return (ModuleAssessmentConfig.query
                .filter_by(module_code=self.module_code)
                .order_by(ModuleAssessmentConfig.id).all())


class ModuleAssessmentConfig(db.Model):
    """Assessment structure for a MODULE, set (and changeable at any time) by
    the lecturer who teaches it. Replaces the old per-student configuration.
    DP progress is recalculated from this table every time it is displayed,
    so changing a weighting updates every student in the module at once."""
    __tablename__ = "module_assessment_configs"

    id = db.Column(db.Integer, primary_key=True)
    module_code = db.Column(db.String(10), db.ForeignKey("modules.module_code"), nullable=False)
    category_name = db.Column(db.String(20), nullable=False)
    dp_weighting = db.Column(db.Integer, nullable=False, default=0)       # 0-100
    total_assessments = db.Column(db.Integer, nullable=False, default=0)
    recorded_count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_by = db.Column(db.Integer, nullable=True)  # user id of the lecturer (no FK: survives account removal)

    __table_args__ = (db.UniqueConstraint("module_code", "category_name", name="uq_module_category"),)


class ModuleAssessment(db.Model):
    """A named assessment in a module, e.g. "Test 1" (category Tests). Created
    when a lecturer uploads marks for it; students can then pick it when they
    enter their own mark, so the lecturer's mark can correct the right one."""
    __tablename__ = "module_assessments"

    id = db.Column(db.Integer, primary_key=True)
    module_code = db.Column(db.String(10), db.ForeignKey("modules.module_code"), nullable=False)
    category_name = db.Column(db.String(20), nullable=False)
    label = db.Column(db.String(60), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("module_code", "label", name="uq_module_assessment_label"),)


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
    # "student" = entered by the student; "lecturer" = uploaded by the lecturer
    # (CSV). A lecturer mark for the same assessment_label replaces the student's.
    source = db.Column(db.String(10), nullable=False, default="student")
    assessment_label = db.Column(db.String(60), nullable=True)   # e.g. "Test 1"

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


class AccessLog(db.Model):
    """Who looked at which student's record, and when (POPIA accountability).
    Names/numbers are copied in as plain text and there are no foreign keys,
    so the log survives even if an account is later removed."""
    __tablename__ = "access_logs"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    viewer_id = db.Column(db.Integer, nullable=True)
    viewer_name = db.Column(db.String(100))
    viewer_role = db.Column(db.String(20))
    student_id = db.Column(db.Integer, nullable=True)
    student_number = db.Column(db.String(20))
    module_code = db.Column(db.String(10), nullable=True)
    action = db.Column(db.String(30))      # search | view_student | view_progress


class ChatMessage(db.Model):
    """One message in a student's conversation with the Advisor chatbot."""
    __tablename__ = "chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    role = db.Column(db.String(10), nullable=False)       # "user" | "assistant"
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class LecturerMark(db.Model):
    """A mark uploaded by a lecturer, kept against the STUDENT NUMBER (plain
    text, no link to a user account). It therefore survives the student not
    being enrolled yet, not having registered yet, or deleting their account:
    whenever that student number is enrolled in the module, the mark is shown."""
    __tablename__ = "lecturer_marks"

    id = db.Column(db.Integer, primary_key=True)
    module_code = db.Column(db.String(10), nullable=False, index=True)
    student_number = db.Column(db.String(20), nullable=False, index=True)
    category_name = db.Column(db.String(20), nullable=False)
    assessment_label = db.Column(db.String(60), nullable=False)
    percentage = db.Column(db.Numeric(5, 2), nullable=False)
    uploaded_by = db.Column(db.String(100))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("module_code", "student_number", "assessment_label",
                                          name="uq_lecturer_mark"),)
