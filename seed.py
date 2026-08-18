"""
seed.py — populate the database with a couple of demo accounts and jobs
so the dashboards aren't empty when you first run the app.

Usage:
    python seed.py
"""

import psycopg2
import psycopg2.extras
from werkzeug.security import generate_password_hash
from config import Config

conn = psycopg2.connect(
    host=Config.POSTGRES_HOST,
    user=Config.POSTGRES_USER,
    password=Config.POSTGRES_PASSWORD,
    dbname=Config.POSTGRES_DB,
    port=Config.POSTGRES_PORT
)
conn.autocommit = True
# cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
    
    # ---- Demo employer ----
    cur.execute("SELECT id FROM users WHERE email = %s", ("employer@test.com",))

    # ---- Demo employer ----
    cur.execute("SELECT id FROM users WHERE email = %s", ("employer@test.com",))
    employer = cur.fetchone()
    if not employer:
        cur.execute(
            """INSERT INTO users (full_name, email, password_hash, role, avatar_initials)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            ("Acme Corp", "employer@test.com", generate_password_hash("test@123"), "employer", "AC"),
        )
        employer_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO company_profiles (user_id, company_name, industry, location) VALUES (%s,%s,%s,%s)",
            (employer_id, "Google", "Technology", "Bangalore, India"),
        )
        print("Created test employer: employer@test.com / test@123")
    else:
        employer_id = employer["id"]
        print("Demo employer already exists.")

    # ---- Demo candidate ----
    cur.execute("SELECT id FROM users WHERE email = %s", ("candidate@test.com",))
    candidate = cur.fetchone()
    if not candidate:
        cur.execute(
            """INSERT INTO users (full_name, email, password_hash, role, avatar_initials)
               VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            ("Rahul Sharma", "candidate@test.com", generate_password_hash("test@123"), "candidate", "RS"),
        )
        candidate_id = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO candidate_profiles (user_id, headline, location, experience_yrs, tech_stack, resume_score)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (candidate_id, "Backend Developer", "Bangalore, India", 3.0, "Python, Flask, AWS, Docker", 82),
        )
        print("Created test candidate: candidate@test.com / test@123")
    else:
        candidate_id = candidate["id"]
        print("test candidate already exists.")

    # ---- Demo jobs ----
    cur.execute("SELECT COUNT(*) AS c FROM jobs WHERE employer_id = %s", (employer_id,))
    if cur.fetchone()["c"] == 0:
        jobs = [
            ("Software Engineer", "Google", "Bangalore, India", "Full-time", "Python, Flask, AWS, Docker", 18, 28),
            ("Backend Developer", "Microsoft", "Hyderabad, India", "Full-time", "Python, Django, PostgreSQL, AWS", 16, 30),
            ("Full Stack Developer", "Acme Corp", "Remote", "Remote", "React, Node.js, MongoDB", 14, 22),
            ("Python Developer", "Acme Corp", "Hyderabad, India", "Full-time", "Python, Flask, Redis", 12, 20),
        ]
        for title, company, location, jtype, stack, smin, smax in jobs:
            cur.execute(
                """INSERT INTO jobs (employer_id, title, company_name, location, job_type, tech_stack,
                                      salary_min_lpa, salary_max_lpa)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (employer_id, title, company, location, jtype, stack, smin, smax),
            )
        print(f"Created {len(jobs)} demo jobs.")
    else:
        print("Demo jobs already exist.")

print("\nSeed complete. You can log in with:")
print("  Employer:  employer@test.com / test@123")
print("  Candidate: candidate@test.com / test@123")

conn.close()
