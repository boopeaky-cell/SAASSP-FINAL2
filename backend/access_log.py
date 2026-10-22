"""Writes AccessLog rows (who viewed which student, and when)."""
from backend import db
from backend.models import AccessLog


def log_access(viewer, action, student=None, module_code=None, student_number=None):
    db.session.add(AccessLog(
        viewer_id=viewer.id, viewer_name=viewer.name, viewer_role=viewer.role,
        student_id=student.id if student else None,
        student_number=student.student_number if student else student_number,
        module_code=module_code, action=action,
    ))
    db.session.commit()
