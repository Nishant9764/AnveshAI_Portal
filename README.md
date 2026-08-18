# SmartHire AI — Flask + PostgreSQL Job Portal

A full job-portal web app: public landing page, candidate signup/login,
employer signup/login, and separate dashboards for each role — styled to
match the SmartHire AI design (indigo/purple, card-based dashboards,
sidebar navigation).

## Stack

- **Backend:** Flask (Python)
- **Database:** PostgreSQL (via psycopg2-binary — no ORM, plain SQL so it's easy to read/modify)
- **Auth:** Flask sessions + Werkzeug password hashing
- **Frontend:** Server-rendered Jinja2 templates + plain CSS (no build step)

## 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Set up PostgreSQL

Create the database and tables:

```bash
postgresql -u root -p < schema.sql
```

This creates a `smarthire_ai` database with tables: `users`,
`candidate_profiles`, `company_profiles`, `jobs`, `applications`, `saved_jobs`.

## 3. Configure the database connection

Either edit `config.py` directly, or set environment variables before running:

```bash
export POSTGRES_HOST=localhost
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=yourpassword
export POSTGRES_DB=smarthire_ai
export SECRET_KEY=some-random-secret-string
```

On Windows (PowerShell): `$env:POSTGRES_PASSWORD="yourpassword"`

## 4. (Optional) Seed demo data

This creates a demo employer + candidate account and a few job postings so
the dashboards aren't empty on first run:

```bash
python seed.py
```

Demo logins after seeding:
- Employer: `employer@demo.com` / `password123`
- Candidate: `candidate@demo.com` / `password123`

## 5. Run the app

```bash
python app.py
```

Visit **http://127.0.0.1:5000**

## How it's organized

```
smarthire/
├── app.py                  # All routes (auth, candidate, employer)
├── config.py                # PostgreSQL + app config (reads from env vars)
├── db.py                    # Thin psycopg2-binary connection helper
├── schema.sql                # Run this once to create DB + tables
├── seed.py                   # Optional demo data
├── requirements.txt
├── static/
│   ├── css/style.css        # All styling — single stylesheet, CSS variables for theme
│   └── uploads/             # Uploaded resumes land here
└── templates/
    ├── base.html             # Layout for public pages (navbar + footer)
    ├── dashboard_base.html   # Layout for logged-in pages (sidebar + topbar)
    ├── index.html             # Landing page
    ├── login.html / signup.html
    ├── candidate_*.html      # Candidate dashboard, jobs feed, saved jobs,
    │                          applications, resume score, profile
    ├── employer_*.html       # Employer dashboard, create job, manage jobs,
    │                          applicants, company profile
    └── settings.html         # Shared by both roles
```

## How the pieces fit together

**Auth & roles.** `users.role` is either `candidate` or `employer`. On
signup, a matching row is also created in `candidate_profiles` or
`company_profiles`. The `login_required(role=...)` decorator in `app.py`
guards every dashboard route, so a candidate can never load an employer
page and vice versa.

**The "AI match score"** shown on job cards and applicant lists is computed
by `compute_match_score()` in `app.py` — a simple keyword-overlap between
a job's `tech_stack` and the candidate's `tech_stack`. It's a stand-in for
a real ML model; swap that function out if you want to plug in an actual
scoring service later.

**Resume score** works the same way: uploading a file in
`/candidate/resume-score` saves it to `static/uploads/` and assigns a
randomized score in a realistic range, since there's no real parsing model
wired in. Replace the logic inside the `resume_score()` view in `app.py`
if you want to connect a real resume-parsing/scoring API.

**Styling.** Everything lives in one file, `static/css/style.css`, using
CSS custom properties at the top (`--color-primary`, `--gradient-hero`,
etc.) so the whole theme can be retinted by changing a handful of variables.

## Extending it

- Swap the donut/line/pie charts (currently hand-drawn inline SVG) for a
  JS charting library if you want animation or tooltips.
- Add a `messages` table + routes if you want the "Messages" nav item to
  do something — it's currently a placeholder link in both dashboards.
- The `more filters` row on the candidate dashboard is decorative; wire it
  up to extra query params on `/candidate/jobs` if you want full filtering.
