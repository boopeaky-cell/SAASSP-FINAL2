"""
Integration test: exercises the real Flask routes end to end (register,
login, select programme, select modules, configure assessment structure,
upload marks) against an in-memory SQLite database, and checks the DP
progress shown matches backend.dp_engine's own calculation - i.e. that the
web layer is wiring the engine up correctly, not just that the engine
is correct in isolation (that's tests/test_dp_engine.py).
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from app import create_app, db
from backend.models import Programme, Module, ProgrammeModule, User, LecturerModule


class TestConfig:
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEMINI_API_KEY = ""  # force the "no feedback available" path - no network in tests
    GEMINI_MODEL = "gemini-2.0-flash"
    THRESHOLD_BANDS = [
        (75, 100.0001, "very_safe", "Very safe / excellent"),
        (70, 75, "safe", "Safe"),
        (60, 70, "generally_safe", "Generally safe"),
        (50, 60, "pass_some_risk", "Pass, some risk"),
        (40, 50, "danger", "Danger, at risk of failing"),
        (0, 40, "critical", "Critical / failing"),
    ]
    ASSESSMENT_CATEGORIES = ["Tests", "Assignments", "Quizzes", "Practicals", "Tutorials"]


@pytest.fixture
def client():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        db.session.add(Programme(programme_code="4BSC01", programme_name="Applied Mathematics and Computer Science"))
        db.session.add(Module(module_code="4MTH111", module_name="Calculus I"))
        db.session.add(ProgrammeModule(programme_code="4BSC01", module_code="4MTH111"))
        db.session.commit()
    with app.test_client() as c:
        yield c


def register_and_login(client, student_number="20211234"):
    client.post("/register", data={"name": "Test Student", "student_number": student_number,
                                    "password": "password123"},
                follow_redirects=True)


def test_full_student_flow(client):
    register_and_login(client)

    # Select programme
    resp = client.post("/student/select-programme", data={"programme_code": "4BSC01"}, follow_redirects=True)
    assert resp.status_code == 200

    # Select modules
    resp = client.post("/student/select-modules", data={"module_code": ["4MTH111"]}, follow_redirects=True)
    assert resp.status_code == 200
    assert b"4MTH111" in resp.data or b"Calculus" in resp.data

    # Find the StudentModule id via the dashboard link (simplest: query DB directly)
    from backend.models import StudentModule
    with client.application.app_context():
        sm = StudentModule.query.filter_by(module_code="4MTH111").first()
        sm_id = sm.id

    # Configure assessment structure: Tests 50%/3/2, Quizzes 20%/3/2, Assignments 20%/2/2, Practicals 10%/2/1, Tutorials 0%/2/0
    resp = client.post(f"/student/module/{sm_id}/configure", data={
        "weight_Tests": "50", "total_Tests": "3", "recorded_Tests": "2",
        "weight_Assignments": "20", "total_Assignments": "2", "recorded_Assignments": "2",
        "weight_Quizzes": "20", "total_Quizzes": "3", "recorded_Quizzes": "2",
        "weight_Practicals": "10", "total_Practicals": "2", "recorded_Practicals": "1",
        "weight_Tutorials": "0", "total_Tutorials": "2", "recorded_Tutorials": "0",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Assessment structure saved" in resp.data or b"saved" in resp.data.lower()

    # Upload marks: Tests 70, 50, 80 (best-2-of-3 -> should displace the 50)
    for pct in ["70", "50", "80"]:
        resp = client.post(f"/student/module/{sm_id}/upload",
                            data={"category_name": "Tests", "percentage": pct}, follow_redirects=True)
        assert resp.status_code == 200

    # Check the module detail page reflects the expected DP progress:
    # Tests: recorded {70,80} of cap 2, slot_weight = 50/2=25
    # earned = 0.70*25 + 0.80*25 = 37.5 ; attempted = 50 -> progress = 75%
    resp = client.get(f"/student/module/{sm_id}")
    page = resp.data.decode()
    assert "75.0%" in page or "75%" in page
    assert "Displaced" in page  # the 50% mark should show as displaced

    # Cross-check directly against the DP engine for full confidence
    from backend.dp_engine import CategoryConfig, marks_by_category_from_student_module, calculate_progress
    with client.application.app_context():
        sm = StudentModule.query.get(sm_id)
        categories = [CategoryConfig(c.category_name, c.dp_weighting, c.total_assessments, c.recorded_count)
                      for c in sm.categories]
        marks_by_cat = marks_by_category_from_student_module(sm)
        result = calculate_progress(categories, marks_by_cat, TestConfig.THRESHOLD_BANDS)
        assert result.progress_percentage == 75.0
        assert result.threshold_band_key == "very_safe"


def test_weighting_validation_rejects_non_100_percent(client):
    register_and_login(client)
    client.post("/student/select-programme", data={"programme_code": "4BSC01"}, follow_redirects=True)
    client.post("/student/select-modules", data={"module_code": ["4MTH111"]}, follow_redirects=True)

    from backend.models import StudentModule
    with client.application.app_context():
        sm_id = StudentModule.query.filter_by(module_code="4MTH111").first().id

    resp = client.post(f"/student/module/{sm_id}/configure", data={
        "weight_Tests": "50", "total_Tests": "3", "recorded_Tests": "2",
        "weight_Assignments": "20", "total_Assignments": "2", "recorded_Assignments": "2",
        "weight_Quizzes": "10", "total_Quizzes": "3", "recorded_Quizzes": "2",  # deliberately wrong (sums to 80)
        "weight_Practicals": "0", "total_Practicals": "0", "recorded_Practicals": "0",
        "weight_Tutorials": "0", "total_Tutorials": "0", "recorded_Tutorials": "0",
    }, follow_redirects=True)
    assert b"must sum to 100" in resp.data

    from backend.models import AssessmentCategoryConfig
    with client.application.app_context():
        count = AssessmentCategoryConfig.query.filter_by(student_module_id=sm_id).count()
        assert count == 0  # nothing was saved


def test_student_logs_in_with_student_number_not_email(client):
    register_and_login(client, student_number="20219999")
    client.get("/logout", follow_redirects=True)

    # Wrong identifier fails
    resp = client.post("/login", data={"identifier": "no-such-number", "password": "password123"},
                        follow_redirects=True)
    assert b"Invalid student number/email or password" in resp.data

    # Student number succeeds
    resp = client.post("/login", data={"identifier": "20219999", "password": "password123"},
                        follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert "_user_id" in sess


def test_lecturer_only_sees_students_in_assigned_module(client):
    with client.application.app_context():
        db.session.add(Module(module_code="4MTH222", module_name="Linear Algebra"))
        db.session.add(ProgrammeModule(programme_code="4BSC01", module_code="4MTH222"))
        staff = User(name="Dr Assigned", email="assigned@saassp.local", role="academic_staff")
        staff.set_password("pw")
        other_staff = User(name="Dr Other", email="other@saassp.local", role="academic_staff")
        other_staff.set_password("pw")
        db.session.add_all([staff, other_staff])
        db.session.commit()
        db.session.add(LecturerModule(lecturer_id=staff.id, module_code="4MTH111"))
        db.session.commit()
        staff_id, other_staff_id = staff.id, other_staff.id

    register_and_login(client, student_number="20218888")
    client.post("/student/select-programme", data={"programme_code": "4BSC01"}, follow_redirects=True)
    client.post("/student/select-modules", data={"module_code": ["4MTH111", "4MTH222"]}, follow_redirects=True)
    client.get("/logout", follow_redirects=True)

    # The lecturer assigned to 4MTH111 sees the student and only that module
    client.post("/login", data={"identifier": "assigned@saassp.local", "password": "pw"}, follow_redirects=True)
    resp = client.get("/staff/students")
    assert b"20218888" in resp.data

    with client.application.app_context():
        student = User.query.filter_by(student_number="20218888").first()
        student_id = student.id
    resp = client.get(f"/staff/students/{student_id}")
    assert b"4MTH111" in resp.data
    assert b"4MTH222" not in resp.data
    client.get("/logout", follow_redirects=True)

    # A lecturer with no modules assigned sees no students at all
    client.post("/login", data={"identifier": "other@saassp.local", "password": "pw"}, follow_redirects=True)
    resp = client.get("/staff/students")
    assert b"20218888" not in resp.data
    assert b"no modules assigned" in resp.data.lower()
