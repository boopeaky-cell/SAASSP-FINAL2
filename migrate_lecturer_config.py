"""
One-time database update for the "lecturer sets the assessment structure" change.

Run it ONCE against each database (your local saassp.db, and later the Neon
database when you redeploy):

    python migrate_lecturer_config.py

What it does (safe to run twice - it skips anything already done):
  1. creates the new tables (module_assessment_configs, module_assessments, access_logs)
  2. adds the new columns to assessment_marks (source, assessment_label)
  3. removes the old per-student table assessment_category_configs
     (assessment structures are now set per MODULE by the lecturer, so
      lecturers must set them up once - students' own marks are kept)
It never deletes any student marks.
"""
from sqlalchemy import inspect, text

from app import create_app, db


def main():
    app = create_app()
    with app.app_context():
        db.create_all()          # creates only the tables that don't exist yet
        insp = inspect(db.engine)

        cols = {c["name"] for c in insp.get_columns("assessment_marks")}
        with db.engine.begin() as conn:
            if "source" not in cols:
                conn.execute(text("ALTER TABLE assessment_marks ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT 'student'"))
                print("Added assessment_marks.source")
            if "assessment_label" not in cols:
                conn.execute(text("ALTER TABLE assessment_marks ADD COLUMN assessment_label VARCHAR(60)"))
                print("Added assessment_marks.assessment_label")
            if "assessment_category_configs" in insp.get_table_names():
                conn.execute(text("DROP TABLE assessment_category_configs"))
                print("Removed old table assessment_category_configs")
        print("Migration complete.")


if __name__ == "__main__":
    main()
