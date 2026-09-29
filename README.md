# SAASSP - DP Progress & AI-Assisted Feedback Module

A working implementation of the module described in `SAASSP_Proposal_Final.docx`,
`SAASSP_SRS.docx` and `SAASSP_SDD.docx`: students configure a module's assessment
structure, upload marks, get a continuous weighted DP progress percentage and
threshold band, and receive AI-assisted feedback (Gemini) after every upload.

This is real, runnable code - not a mockup. The DP calculation engine and the
full student flow are covered by automated tests (see "Running the tests" below).

## What's implemented

Matches the SRS use cases 1:1:

| SRS use case | Where |
|---|---|
| 3.2.1 Login | `backend/auth.py` |
| 3.2.2 Register and Select Programme and Modules | `backend/student.py` (`select_programme`, `select_modules`) |
| 3.2.3 Configure Assessment Structure | `backend/student.py` (`configure_assessment`) |
| 3.2.4 Upload Assessment Mark | `backend/student.py` (`upload_mark`) |
| 3.2.5 Receive AI-Assisted Feedback | `backend/feedback_service.py`, invoked from `upload_mark` |
| 3.2.6 View DP Progress Dashboard | `backend/student.py` (`dashboard`, `module_detail`) |
| 3.2.7 View Student Progress (Academic Staff, read-only) | `backend/staff.py` |
| 3.2.8 Manage Programme and Module Data | `backend/admin.py` (`programmes`) |
| 3.2.9 Manage User Accounts | `backend/admin.py` (`users`) |

The deterministic core - weighted best-N-of-M DP progress, the six threshold
bands, and blip-tolerant trend classification - lives entirely in
`backend/dp_engine.py`, separate from the AI Feedback Service, exactly as
specified in SDD Section 3.1 ("This engine never depends on the AI Feedback
Service").

**Not implemented** (out of scope per the SRS/SDD): module recommendations,
prerequisite/co-requisite checking, credit/graduation tracking,
machine-learning risk classification, lecturer-side assessment configuration,
and the conversational AI chatbot.

## Setup

```bash
cd saassp
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

By default the app uses a local SQLite file (`saassp.db`) so you can try
it with zero extra setup. All settings live in the `.env` file in the project root (see
`.env.example` for the template). It is loaded automatically - no `export`
commands needed. To use MySQL instead (matching the SDD's deployment
target), uncomment `DATABASE_URL` in `.env`:

```
DATABASE_URL=mysql+pymysql://saassp_user:password@localhost/saassp_app
```

To enable real AI-assisted feedback, put a Gemini API key in `.env`:

```
GEMINI_API_KEY=your-key-here
```

Without it, the app still works fully - `upload_mark` just stores "AI feedback
wasn't available this time" instead of generated text, exactly as SRS 3.2.5's
Alternative Path specifies.

### Load the dataset and create accounts

```bash
python seed_data.py
```

This loads `data/programmes.csv`, `data/modules.csv` and
`data/programme_modules.csv` (the same dataset you provided), and creates:

- Admin: `admin@saassp.local` / `admin123`
- Academic Staff: `staff@saassp.local` / `staff123`

Students self-register from the login page.

### Run it

```bash
python app.py
# or
flask run
```

Then open http://localhost:5000. Register a Student account, select a
programme and modules, configure a module's assessment structure, and upload
a mark to see DP progress and feedback appear.

## Running the tests

```bash
pip install pytest   # already in requirements.txt
pytest tests/ -v
```

- `tests/test_dp_engine.py` - 13 unit tests on the DP calculation engine in
  isolation: the weighted-progress formula, best-N-of-M displacement, all six
  threshold bands, and trend classification (including the exact "started
  badly, recovered" and "tolerates one reversal" scenarios worked through
  while designing this).
- `tests/test_app_smoke.py` - 2 integration tests that drive the real Flask
  routes (register → select programme → select modules → configure → upload
  marks) and check the number shown on the page matches what the engine
  itself computes, plus that the 100%-weighting validation actually blocks
  a bad configuration.

All 15 currently pass.

## Project layout

```
saassp/
  app.py                 main application file: Flask setup, connects
                         static/ (frontend) to backend/, starts the server
  .env                   your settings (secret key, database, Gemini key)
  .env.example           template of .env without secrets
  static/                FRONTEND
    templates/             Jinja2 HTML pages (Bootstrap 5, UNIZULU navy/gold)
    css/style.css
    img/
  backend/               BACKEND
    __init__.py            shared db + login manager
    models.py             SQLAlchemy models (SDD 4.1 data dictionary)
    dp_engine.py           DP Progress Calculation Engine (SDD 3.1)
    feedback_service.py    AI Feedback Service / Gemini client (SDD 3.1)
    auth.py                 Login / Register routes
    student.py              Student-facing routes
    staff.py                 Academic Staff routes
    admin.py                  Administration routes
  data/                        the CSV dataset you provided
  tests/
    test_dp_engine.py
    test_app_smoke.py
  seed_data.py
  config.py            reads .env
  requirements.txt
```

## Known gaps to close before a real deployment

- Passwords are hashed (Werkzeug) but there's no password-reset flow yet.
- CSRF protection (Flask-WTF) isn't wired in - add it before exposing this
  beyond local testing.
- The admin CSV importer trusts well-formed input; malformed CSVs will raise
  rather than showing a friendly per-row error.
- No pagination on the admin programme/user lists - fine for now, will need
  it once the real dataset (37 programmes, 120 modules) is fully loaded
  alongside a real cohort of users.
