"""
Loads programmes.csv, modules.csv and programme_modules.csv (see the
Administration Component in SAASSP_SDD.docx Section 3.1) and creates a
default System Administrator account so there is a way to log in and
take it from there.

Usage:
    python seed_data.py
"""
import csv
import os

from app import create_app, db
from backend.models import Programme, Module, ProgrammeModule, User, LecturerModule

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_programmes():
    path = os.path.join(DATA_DIR, "programmes.csv")
    added = 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["programme_code"].strip()
            if Programme.query.get(code):
                continue
            db.session.add(Programme(programme_code=code, programme_name=row["programme_name"].strip()))
            added += 1
    db.session.commit()
    print(f"Programmes: {added} added.")


def load_modules():
    path = os.path.join(DATA_DIR, "modules.csv")
    added = 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = row["module_code"].strip()
            if Module.query.get(code):
                continue
            db.session.add(Module(module_code=code, module_name=row["module_name"].strip()))
            added += 1
    db.session.commit()
    print(f"Modules: {added} added.")


def load_links():
    path = os.path.join(DATA_DIR, "programme_modules.csv")
    added, skipped = 0, 0
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            p_code = row["programme_code"].strip()
            m_code = row["module_code"].strip()
            if not Programme.query.get(p_code) or not Module.query.get(m_code):
                skipped += 1
                continue
            exists = ProgrammeModule.query.filter_by(programme_code=p_code, module_code=m_code).first()
            if exists:
                skipped += 1
                continue
            db.session.add(ProgrammeModule(programme_code=p_code, module_code=m_code))
            added += 1
    db.session.commit()
    print(f"Programme-module links: {added} added, {skipped} skipped.")


def ensure_default_accounts():
    if not User.query.filter_by(email="admin@saassp.local").first():
        u = User(name="Default Administrator", email="admin@saassp.local", role="admin")
        u.set_password("admin123")
        db.session.add(u)
        print("Created admin account: admin@saassp.local / admin123")

    if not User.query.filter_by(email="staff@saassp.local").first():
        u = User(name="Demo Academic Staff", email="staff@saassp.local", role="academic_staff")
        u.set_password("staff123")
        db.session.add(u)
        db.session.flush()
        # Assign this demo lecturer the first module in the dataset so the
        # "lecturer only sees their own module" behaviour is demonstrable
        # out of the box. A real administrator assigns this in
        # Manage User Accounts.
        first_module = Module.query.order_by(Module.module_code).first()
        if first_module:
            db.session.add(LecturerModule(lecturer_id=u.id, module_code=first_module.module_code))
        print("Created academic staff account: staff@saassp.local / staff123")

    if not User.query.filter_by(email="advisor@saassp.local").first():
        u = User(name="Demo Academic Advisor", email="advisor@saassp.local", role="advisor")
        u.set_password("advisor123")
        db.session.add(u)
        print("Created advisor account: advisor@saassp.local / advisor123")

    db.session.commit()


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        load_programmes()
        load_modules()
        load_links()
        ensure_default_accounts()
        print("Seeding complete.")
