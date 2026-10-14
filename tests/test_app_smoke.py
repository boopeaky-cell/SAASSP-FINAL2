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
    GROQ_API_KEY = ""    # force the "no feedback available" path - no network in tests
    GROQ_MODEL = "llama-3.3-70b-versatile"
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


CONFIG_A = {
    "weight_Tests": "50", "total_Tests": "3", "recorded_Tests": "2",
    "weight_Assignments": "20", "total_Assignments": "2", "recorded_Assignments": "2",
    "weight_Quizzes": "20", "total_Quizzes": "3", "recorded_Quizzes": "2",
    "weight_Practicals": "10", "total_Practicals": "2", "recorded_Practicals": "1",
    "weight_Tutorials": "0", "total_Tutorials": "2", "recorded_Tutorials": "0",
}


def make_staff(client, email, role="academic_staff", modules=()):
    with client.application.app_context():
        u = User(name=email.split("@")[0].title(), email=email, role=role)
        u.set_password("pw")
        db.session.add(u)
        db.session.commit()
        for code in modules:
            db.session.add(LecturerModule(lecturer_id=u.id, module_code=code))
        db.session.commit()


def login(client, identifier, password="pw"):
    client.get("/logout", follow_redirects=True)
    return client.post("/login", data={"identifier": identifier, "password": password},
                       follow_redirects=True)


def enrol_student(client, number, modules=("4MTH111",)):
    register_and_login(client, student_number=number)
    client.post("/student/select-programme", data={"programme_code": "4BSC01"}, follow_redirects=True)
    client.post("/student/select-modules", data={"module_code": list(modules)}, follow_redirects=True)
    from backend.models import StudentModule
    with client.application.app_context():
        ids = {sm.module_code: sm.id for sm in StudentModule.query.join(User).filter(User.student_number == number)}
    client.get("/logout", follow_redirects=True)
    return ids


def progress_of(client, number, module_code="4MTH111"):
    from backend.models import StudentModule
    from backend.progress import compute_progress
    with client.application.app_context():
        sm = StudentModule.query.join(User).filter(
            User.student_number == number, StudentModule.module_code == module_code).first()
        result, _ = compute_progress(sm)
        return None if result is None else result.progress_percentage


def test_full_student_flow(client):
    make_staff(client, "lect@saassp.local", modules=["4MTH111"])
    ids = enrol_student(client, "20211234")
    sm_id = ids["4MTH111"]

    # Before the lecturer configures anything, the student sees a waiting message
    login(client, "20211234", "password123")
    page = client.get(f"/student/module/{sm_id}").data.decode()
    assert "hasn't set up" in page
    client.get("/logout", follow_redirects=True)

    # The LECTURER configures the module
    login(client, "lect@saassp.local")
    resp = client.post("/staff/module/4MTH111/config", data=CONFIG_A, follow_redirects=True)
    assert b"Assessment structure saved" in resp.data

    # The student enters Tests 70, 50, 80 (best 2 of 3 -> 50 displaced)
    login(client, "20211234", "password123")
    for pct in ["70", "50", "80"]:
        resp = client.post(f"/student/module/{sm_id}/upload",
                           data={"assessment": "Tests::", "percentage": pct}, follow_redirects=True)
        assert resp.status_code == 200
    page = client.get(f"/student/module/{sm_id}").data.decode()
    assert "75.0%" in page or "75%" in page
    assert "Displaced" in page
    assert progress_of(client, "20211234") == 75.0


def test_student_cannot_configure_or_use_lecturer_routes(client):
    ids = enrol_student(client, "20211234")
    login(client, "20211234", "password123")
    assert client.post("/staff/module/4MTH111/config", data=CONFIG_A).status_code == 403
    assert client.get(f"/student/module/{ids['4MTH111']}/configure").status_code == 404


def test_weighting_validation_rejects_non_100_percent(client):
    make_staff(client, "lect@saassp.local", modules=["4MTH111"])
    login(client, "lect@saassp.local")
    bad = dict(CONFIG_A, weight_Quizzes="10")   # sums to 90
    resp = client.post("/staff/module/4MTH111/config", data=bad, follow_redirects=True)
    assert b"must sum to 100" in resp.data

    from backend.models import ModuleAssessmentConfig
    with client.application.app_context():
        assert ModuleAssessmentConfig.query.filter_by(module_code="4MTH111").count() == 0


def test_changing_the_structure_recalculates_progress(client):
    make_staff(client, "lect@saassp.local", modules=["4MTH111"])
    ids = enrol_student(client, "20211234")
    login(client, "lect@saassp.local")
    client.post("/staff/module/4MTH111/config", data=CONFIG_A, follow_redirects=True)

    login(client, "20211234", "password123")
    client.post(f"/student/module/{ids['4MTH111']}/upload",
                data={"assessment": "Tests::", "percentage": "80"}, follow_redirects=True)
    client.post(f"/student/module/{ids['4MTH111']}/upload",
                data={"assessment": "Quizzes::", "percentage": "40"}, follow_redirects=True)
    # Tests 80 @ weight 50, Quizzes 40 @ weight 20 -> (0.8*25 + 0.4*10) / (25+10) = 24/35 = 68.57
    assert progress_of(client, "20211234") == 68.57

    # Lecturer decides Quizzes should weigh far more than Tests
    login(client, "lect@saassp.local")
    changed = dict(CONFIG_A, weight_Tests="20", weight_Quizzes="50")
    resp = client.post("/staff/module/4MTH111/config", data=changed, follow_redirects=True)
    assert b"recalculated" in resp.data
    # (0.8*10 + 0.4*25) / (10+25) = 18/35 = 51.43
    assert progress_of(client, "20211234") == 51.43


def test_csv_upload_corrects_student_mark_and_reports_bad_rows(client):
    import io
    make_staff(client, "lect@saassp.local", modules=["4MTH111"])
    ids = enrol_student(client, "20211234")
    enrol_student(client, "20215555")
    login(client, "lect@saassp.local")
    client.post("/staff/module/4MTH111/config", data=CONFIG_A, follow_redirects=True)

    # Student 20211234 enters Test 1 as 90, but only after the lecturer defines it
    csv_body = ("student_number,mark\n20211234,60\n20215555,75\n99999999,50\n20211234x,abc\n20215555,150\n")
    # (last row repeats 20215555 with an invalid mark -> skipped, earlier valid row stays)
    resp = client.post("/staff/module/4MTH111/upload", data={
        "category_name": "Tests", "assessment_label": "Test 1",
        "csv_file": (io.BytesIO(csv_body.encode()), "test1.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    page = resp.data.decode()
    assert "2 mark(s) added" in page
    assert "99999999" in page and "not enrolled" in page
    assert progress_of(client, "20215555") == 75.0   # lecturer mark alone counts

    # Student now enters their own (inflated) Test 1 = 90 -> lecturer's 60 replaces it
    login(client, "20211234", "password123")
    client.post(f"/student/module/{ids['4MTH111']}/upload",
                data={"assessment": "Tests::Test 1", "percentage": "90"}, follow_redirects=True)
    assert progress_of(client, "20211234") == 60.0
    page = client.get(f"/student/module/{ids['4MTH111']}").data.decode()
    assert "Replaced by lecturer" in page

    # Re-uploading the same assessment updates, it doesn't duplicate
    login(client, "lect@saassp.local")
    resp = client.post("/staff/module/4MTH111/upload", data={
        "category_name": "Tests", "assessment_label": "Test 1",
        "csv_file": (io.BytesIO(b"student_number,mark\n20211234,70\n"), "t.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"1 updated" in resp.data
    assert progress_of(client, "20211234") == 70.0

    # Undo: lecturer marks vanish, the student's own 90 counts again
    from backend.models import ModuleAssessment
    with client.application.app_context():
        aid = ModuleAssessment.query.filter_by(label="Test 1").first().id
    client.post(f"/staff/module/4MTH111/assessment/{aid}/delete", follow_redirects=True)
    assert progress_of(client, "20211234") == 90.0


def test_csv_without_weighting_is_rejected(client):
    import io
    make_staff(client, "lect@saassp.local", modules=["4MTH111"])
    enrol_student(client, "20211234")
    login(client, "lect@saassp.local")
    resp = client.post("/staff/module/4MTH111/upload", data={
        "category_name": "Tests", "assessment_label": "Test 1",
        "csv_file": (io.BytesIO(b"student_number,mark\n20211234,60\n"), "t.csv")},
        content_type="multipart/form-data", follow_redirects=True)
    assert b"no weighting" in resp.data
    from backend.models import AssessmentMark
    with client.application.app_context():
        assert AssessmentMark.query.count() == 0


def test_lecturer_cannot_touch_another_lecturers_module(client):
    with client.application.app_context():
        db.session.add(Module(module_code="4MTH222", module_name="Linear Algebra"))
        db.session.add(ProgrammeModule(programme_code="4BSC01", module_code="4MTH222"))
        db.session.commit()
    make_staff(client, "a@saassp.local", modules=["4MTH111"])
    make_staff(client, "b@saassp.local", modules=["4MTH222"])
    enrol_student(client, "20211234", modules=("4MTH111",))
    login(client, "b@saassp.local")
    assert client.get("/staff/module/4MTH111").status_code == 404
    assert client.post("/staff/module/4MTH111/config", data=CONFIG_A).status_code == 404
    assert client.post("/staff/module/4MTH111/upload", data={}).status_code == 404
    # searching for the other lecturer's student finds nothing
    resp = client.get("/staff/students?student_number=20211234")
    assert b"No student in your module" in resp.data
    from backend.models import ModuleAssessmentConfig
    with client.application.app_context():
        assert ModuleAssessmentConfig.query.count() == 0


def test_lecturer_student_search_finds_own_student(client):
    make_staff(client, "a@saassp.local", modules=["4MTH111"])
    enrol_student(client, "20211234")
    enrol_student(client, "20219999")
    login(client, "a@saassp.local")
    resp = client.get("/staff/students?student_number=2021123")
    assert b"20211234" in resp.data and b"20219999" not in resp.data


def test_admin_assigns_modules_to_a_lecturer_on_its_own_page(client):
    make_staff(client, "boss@saassp.local", role="admin")
    make_staff(client, "lect@saassp.local")
    from backend.models import LecturerModule
    with client.application.app_context():
        lid = User.query.filter_by(email="lect@saassp.local").first().id
    login(client, "boss@saassp.local")
    assert b"Assign modules" in client.get("/admin/users").data
    page = client.get(f"/admin/users/{lid}/modules").data
    assert b"4MTH111" in page and b"Calculus I" in page
    resp = client.post(f"/admin/users/{lid}/modules", data={"module_codes": ["4MTH111"]}, follow_redirects=True)
    assert b"now teaches 1 module" in resp.data and b"4MTH111" in resp.data
    with client.application.app_context():
        assert [lm.module_code for lm in LecturerModule.query.filter_by(lecturer_id=lid)] == ["4MTH111"]
    # lecturers themselves cannot open this page
    login(client, "lect@saassp.local")
    assert client.get(f"/admin/users/{lid}/modules").status_code == 403


def test_admin_can_delete_students_and_lecturers(client):
    make_staff(client, "boss@saassp.local", role="admin")
    make_staff(client, "lect@saassp.local", modules=["4MTH111"])
    make_staff(client, "lect2@saassp.local", modules=["4MTH111"])
    ids = enrol_student(client, "20211234")
    login(client, "lect@saassp.local")
    client.post("/staff/module/4MTH111/config", data=CONFIG_A, follow_redirects=True)
    login(client, "20211234", "password123")
    client.post(f"/student/module/{ids['4MTH111']}/upload",
                data={"assessment": "Tests::", "percentage": "80"}, follow_redirects=True)
    login(client, "lect2@saassp.local")
    with client.application.app_context():
        sid = User.query.filter_by(student_number="20211234").first().id
    client.get(f"/staff/students/{sid}", follow_redirects=True)   # a lecturer looks the student up -> logged

    login(client, "boss@saassp.local")
    with client.application.app_context():
        targets = [u.id for u in User.query.filter(User.email.in_(["lect@saassp.local", "lect2@saassp.local"]))]
        targets.append(User.query.filter_by(student_number="20211234").first().id)
    for uid in targets:
        resp = client.post("/admin/users", data={"action": "delete", "user_id": uid}, follow_redirects=True)
        assert b"removed" in resp.data.lower(), resp.data
    with client.application.app_context():
        assert User.query.filter(User.id.in_(targets)).count() == 0
        from backend.models import AccessLog, StudentModule
        assert StudentModule.query.count() == 0
        assert AccessLog.query.count() > 0   # the audit trail survives removed accounts


def test_forgotten_password_admin_reset_then_user_changes_it(client):
    make_staff(client, "boss@saassp.local", role="admin")
    enrol_student(client, "20211234")
    with client.application.app_context():
        uid = User.query.filter_by(student_number="20211234").first().id

    login(client, "boss@saassp.local")
    resp = client.post("/admin/users", data={"action": "reset_password", "user_id": uid,
                                             "new_password": "short"}, follow_redirects=True)
    assert b"at least 6" in resp.data
    resp = client.post("/admin/users", data={"action": "reset_password", "user_id": uid,
                                             "new_password": "Temp1234"}, follow_redirects=True)
    assert b"Password reset for" in resp.data

    # old password no longer works, the temporary one does
    assert b"Invalid" in login(client, "20211234", "password123").data
    login(client, "20211234", "Temp1234")
    with client.session_transaction() as sess:
        assert "_user_id" in sess

    # the student picks their own password
    resp = client.post("/account/password", data={"current_password": "wrong", "new_password": "Mine5678",
                                                  "confirm_password": "Mine5678"}, follow_redirects=True)
    assert b"not correct" in resp.data
    resp = client.post("/account/password", data={"current_password": "Temp1234", "new_password": "Mine5678",
                                                  "confirm_password": "Mine5678"}, follow_redirects=True)
    assert b"Password changed" in resp.data
    login(client, "20211234", "Mine5678")
    with client.session_transaction() as sess:
        assert "_user_id" in sess


def test_forgot_password_email_link_flow(client, monkeypatch):
    sent = []
    import backend.auth as auth_mod
    monkeypatch.setattr(auth_mod, "send_email", lambda to, subj, body: sent.append((to, subj, body)) or True)

    register_and_login(client, student_number="20217777")
    client.post("/account/password", data={"action": "email", "email": "Stu@Example.com"}, follow_redirects=True)
    client.get("/logout", follow_redirects=True)

    # unknown account: same message, nothing sent
    resp = client.post("/forgot-password", data={"identifier": "00000000"}, follow_redirects=True)
    assert b"has been sent" in resp.data and sent == []

    # real account: one e-mail, to the address on file, containing a link
    resp = client.post("/forgot-password", data={"identifier": "20217777"}, follow_redirects=True)
    assert b"has been sent" in resp.data and len(sent) == 1
    to, subject, body = sent[0]
    assert to == "stu@example.com"
    link = [w for w in body.split() if "/reset-password/" in w][0]
    path = "/reset-password/" + link.split("/reset-password/")[1]

    # tampered link is refused
    assert b"invalid or has expired" in client.get(path[:-3] + "xyz", follow_redirects=True).data

    # good link: mismatching / short passwords refused, then success
    assert b"match" in client.post(path, data={"new_password": "abcdef", "confirm_password": "abcdeg"}).data
    resp = client.post(path, data={"new_password": "Brand-new1", "confirm_password": "Brand-new1"}, follow_redirects=True)
    assert b"Password changed" in resp.data
    login(client, "20217777", "Brand-new1")
    with client.session_transaction() as sess:
        assert "_user_id" in sess
    client.get("/logout", follow_redirects=True)

    # the link only works once
    assert b"invalid or has expired" in client.get(path, follow_redirects=True).data


def test_forgot_password_expired_link_and_no_email_on_file(client, monkeypatch):
    sent = []
    import backend.auth as auth_mod
    monkeypatch.setattr(auth_mod, "send_email", lambda *a: sent.append(a) or True)
    register_and_login(client, student_number="20218888")     # registered WITHOUT an e-mail
    client.get("/logout", follow_redirects=True)
    resp = client.post("/forgot-password", data={"identifier": "20218888"}, follow_redirects=True)
    assert b"has been sent" in resp.data and sent == []        # same message, nothing sent

    monkeypatch.setattr(auth_mod, "RESET_MAX_AGE", -1)          # every link counts as expired
    with client.application.app_context():
        user = User.query.filter_by(student_number="20218888").first()
        with client.application.test_request_context():
            token = auth_mod._make_reset_token(user)
    assert b"invalid or has expired" in client.get(f"/reset-password/{token}", follow_redirects=True).data


# ---------------------------------------------------------------------
# Open sign-up (students + lecturers), eye button, Advisor chatbot
# ---------------------------------------------------------------------

def test_password_boxes_get_the_eye_button(client):
    for url in ("/login", "/register", "/forgot-password"):
        page = client.get(url).data.decode()
        assert "pw-eye" in page          # the show/hide script is on every page


def test_lecturers_can_self_register_but_see_nothing_until_assigned(client):
    resp = client.post("/register", data={"account_type": "academic_staff", "name": "Dr New",
                                          "email": "new@uni.ac.za", "password": "secret1"},
                       follow_redirects=True)
    assert b"administrator must now assign" in resp.data or b"no modules assigned" in resp.data.lower()
    with client.application.app_context():
        u = User.query.filter_by(email="new@uni.ac.za").first()
        assert u.role == "academic_staff" and u.student_number is None
        assert LecturerModule.query.filter_by(lecturer_id=u.id).count() == 0
    enrol = client.get("/staff/students")
    assert b"no modules assigned" in enrol.data.lower()


def test_lecturer_registration_requires_email_and_admin_role_cannot_be_self_created(client):
    resp = client.post("/register", data={"account_type": "academic_staff", "name": "No Mail",
                                          "password": "secret1"}, follow_redirects=True)
    assert b"e-mail address" in resp.data
    resp = client.post("/register", data={"account_type": "admin", "name": "Sneaky",
                                          "email": "sneaky@x.com", "password": "secret1"},
                       follow_redirects=True)
    assert b"student or a lecturer" in resp.data
    with client.application.app_context():
        assert User.query.filter(User.email.in_(["sneaky@x.com"])).count() == 0
        assert User.query.filter_by(role="admin").count() == 0


def test_retired_advisor_account_is_logged_out_not_looped(client):
    make_staff(client, "old-advisor@saassp.local", role="advisor")
    resp = login(client, "old-advisor@saassp.local")
    assert b"no longer in use" in resp.data


def _setup_chat_student(client, number="20211234", marks=(("Tests", "80"),)):
    make_staff(client, f"lect{number}@saassp.local", modules=["4MTH111"])
    ids = enrol_student(client, number)
    login(client, f"lect{number}@saassp.local")
    client.post("/staff/module/4MTH111/config", data=CONFIG_A, follow_redirects=True)
    login(client, number, "password123")
    for cat, pct in marks:
        client.post(f"/student/module/{ids['4MTH111']}/upload",
                    data={"assessment": f"{cat}::", "percentage": pct}, follow_redirects=True)
    return ids


def test_student_dashboard_links_to_the_advisor_and_chat_works_without_ai_keys(client):
    _setup_chat_student(client)
    dash = client.get("/student/dashboard").data.decode()
    assert "Talk to your Advisor" in dash
    assert b"Your Advisor" in client.get("/student/advisor").data

    resp = client.post("/student/advisor/send", json={"message": "How am I doing?"})
    data = resp.get_json()
    assert resp.status_code == 200 and data["ai"] is False       # no keys in tests -> rule-based fallback
    assert "4MTH111" in data["reply"] and "80.0%" in data["reply"]
    page = client.get("/student/advisor").data.decode()
    assert "How am I doing?" in page and "4MTH111" in page       # conversation is kept


def test_advisor_uses_ai_with_only_this_students_data(client, monkeypatch):
    import backend.advisor_chat as chat
    seen = {}

    def fake_groq(messages, api_key, model):
        seen["system"] = messages[0]["content"]
        seen["last"] = messages[-1]["content"]
        return "You are in a very safe position in 4MTH111."
    monkeypatch.setattr(chat, "_try_groq", fake_groq)
    client.application.config["GROQ_API_KEY"] = "fake-key"

    # another student with distinctive data must never leak into this chat
    _setup_chat_student(client, "20215555", marks=(("Tests", "33"),))
    client.get("/logout", follow_redirects=True)
    _setup_chat_student(client, "20216666", marks=(("Tests", "91"),))
    resp = client.post("/student/advisor/send", json={"message": "Any advice?"})
    assert resp.get_json() == {"reply": "You are in a very safe position in 4MTH111.", "ai": True}
    assert "91.0%" in seen["system"] and "20216666" in seen["system"]
    assert "20215555" not in seen["system"] and "33.0%" not in seen["system"]
    assert seen["last"] == "Any advice?"


def test_advisor_validation_rate_limit_and_access(client):
    ids = _setup_chat_student(client)
    assert client.post("/student/advisor/send", json={"message": "  "}).status_code == 400
    assert client.post("/student/advisor/send", json={"message": "x" * 801}).status_code == 400
    from backend.models import ChatMessage
    with client.application.app_context():
        sid = User.query.filter_by(student_number="20211234").first().id
        for _ in range(30):
            db.session.add(ChatMessage(student_id=sid, role="user", content="hi"))
        db.session.commit()
    assert client.post("/student/advisor/send", json={"message": "one more"}).status_code == 429

    resp = client.post("/student/advisor/clear", follow_redirects=True)
    assert b"Conversation cleared" in resp.data
    with client.application.app_context():
        assert ChatMessage.query.count() == 0

    # lecturers and logged-out visitors cannot use the student chatbot
    login(client, "lect20211234@saassp.local")
    assert client.get("/student/advisor").status_code == 403
    client.get("/logout", follow_redirects=True)
    assert client.get("/student/advisor").status_code in (302, 401)


def test_deleting_a_student_also_removes_their_chat(client):
    make_staff(client, "boss@saassp.local", role="admin")
    _setup_chat_student(client)
    client.post("/student/advisor/send", json={"message": "hello"})
    login(client, "boss@saassp.local")
    with client.application.app_context():
        uid = User.query.filter_by(student_number="20211234").first().id
    resp = client.post("/admin/users", data={"action": "delete", "user_id": uid}, follow_redirects=True)
    assert b"removed" in resp.data.lower()
    from backend.models import ChatMessage
    with client.application.app_context():
        assert ChatMessage.query.count() == 0
