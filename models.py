"""
models.py
─────────
PostgreSQL persistence layer (psycopg2), using a small connection
pool so Flask doesn't open a fresh TCP connection per request.

Tables:
  sessions   — one row per candidate assessment attempt
  responses  — one row per question answered, linked to a session

Requires a reachable PostgreSQL server. Connection details come from
environment variables (see .env.example). On startup, init_db() creates
the target database (if missing) and both tables (if missing) — so the
*application* still boots with a single command, but PostgreSQL itself must
already be running somewhere (local install, Docker container, or a
managed instance).
"""

import os
import json
import uuid
import secrets
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool
from psycopg2.extras import DictCursor

DB_HOST = os.environ.get("POSTGRES_HOST", "localhost")
DB_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
DB_USER = os.environ.get("POSTGRES_USER", "postgres")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
DB_NAME = os.environ.get("POSTGRES_DB", "resume_assessment")

_pool = None


def _create_database_if_missing():
    # PostgreSQL requires creating DB outside of a transaction or by connecting to 'postgres' db
    pass


def _get_pool():
    global _pool
    if _pool is None:
        _create_database_if_missing()
        _pool = pool.SimpleConnectionPool(
            1, 5,
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            dbname=DB_NAME
        )
    return _pool


@contextmanager
def get_conn():
    pool_obj = _get_pool()
    conn = pool_obj.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool_obj.putconn(conn)


def init_db():
    with get_conn() as conn:
        cursor = conn.cursor()
        
        # 1. Create sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id VARCHAR(36) PRIMARY KEY,
                candidate_name VARCHAR(255),
                resume_skills JSON,
                jd_text TEXT,
                jd_required_skills JSON,
                jd_preferred_skills JSON,
                skills_tested JSON,
                current_round VARCHAR(20) DEFAULT 'basic',
                status VARCHAR(20) DEFAULT 'in_progress',
                basic_score FLOAT,
                medium_score FLOAT,
                advanced_score FLOAT,
                final_score FLOAT,
                verdict VARCHAR(50),
                missing_required_skills JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. Create responses table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(36) NOT NULL,
                round_name VARCHAR(20) NOT NULL,
                skill VARCHAR(255),
                question TEXT,
                options JSON,
                correct_index INT,
                model_answer TEXT,
                rubric JSON,
                candidate_answer TEXT,
                score FLOAT,
                justification TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        
        # 3. Create the index separately (PostgreSQL style)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_round 
            ON responses (session_id, round_name)
        """)
        
        cursor.close()


def create_session(candidate_name, resume_skills, jd_text, jd_required_skills,
                    jd_preferred_skills, skills_tested, missing_required_skills):
    session_id = str(uuid.uuid4())
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO sessions
               (id, candidate_name, resume_skills, jd_text, jd_required_skills,
                jd_preferred_skills, skills_tested, missing_required_skills)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (session_id, candidate_name, json.dumps(resume_skills), jd_text,
             json.dumps(jd_required_skills), json.dumps(jd_preferred_skills),
             json.dumps(skills_tested), json.dumps(missing_required_skills)),
        )
        cursor.close()
    return session_id


def create_round1_session(application_id, candidate_id, job_id, candidate_name,
                           resume_skills, jd_text, jd_required_skills,
                           jd_preferred_skills, skills_tested, missing_required_skills):
    """Round 1 variant of create_session — also links the session to the
    application/candidate/job and mints a unique invite_token so the
    email link can identify this session without exposing the raw id."""
    session_id = str(uuid.uuid4())
    invite_token = secrets.token_urlsafe(24)
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO sessions
               (id, candidate_name, resume_skills, jd_text, jd_required_skills,
                jd_preferred_skills, skills_tested, missing_required_skills,
                application_id, candidate_id, job_id, invite_token, current_round, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'part_a','invited')""",
            (session_id, candidate_name, json.dumps(resume_skills), jd_text,
             json.dumps(jd_required_skills), json.dumps(jd_preferred_skills),
             json.dumps(skills_tested), json.dumps(missing_required_skills),
             application_id, candidate_id, job_id, invite_token),
        )
        cursor.close()
    return session_id, invite_token


def get_session_by_token(invite_token):
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM sessions WHERE invite_token = %s", (invite_token,))
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None


def get_session(session_id):
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None
        row = dict(row)
        # psycopg2 decodes native JSON columns to Python objects
        # automatically in most driver versions; this stays defensive in
        # case a given version/connection returns raw JSON text instead.
        for key in ("resume_skills", "jd_required_skills", "jd_preferred_skills",
                    "skills_tested", "missing_required_skills"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except (TypeError, json.JSONDecodeError):
                    pass
        return row


def update_session(session_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [session_id]
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE sessions SET {cols} WHERE id = %s", values)
        cursor.close()


def add_response(session_id, round_name, skill, question, candidate_answer, score,
                  options=None, correct_index=None, model_answer=None,
                  rubric=None, justification=None, time_taken_seconds=None,
                  is_correct=None, paste_detected=False, keystroke_metrics=None,
                  question_bank_id=None):
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute(
            """INSERT INTO responses
               (session_id, round_name, skill, question, options, correct_index,
                model_answer, rubric, candidate_answer, score, justification,
                time_taken_seconds, is_correct, paste_detected, keystroke_metrics,
                question_bank_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (session_id, round_name, skill, question,
             json.dumps(options) if options is not None else None,
             correct_index,
             model_answer,
             json.dumps(rubric) if rubric is not None else None,
             candidate_answer, score, justification,
             time_taken_seconds, is_correct, paste_detected,
             json.dumps(keystroke_metrics) if keystroke_metrics is not None else None,
             question_bank_id),
        )
        row = cursor.fetchone()
        cursor.close()
        return row["id"] if row else None


def get_test_state(session_id):
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM test_state WHERE session_id = %s", (session_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO test_state (session_id) VALUES (%s) RETURNING *", (session_id,)
            )
            row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None


def update_test_state(session_id, **fields):
    if not fields:
        return
    get_test_state(session_id)  # ensure a row exists
    cols = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [session_id]
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE test_state SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE session_id = %s",
            values,
        )
        cursor.close()
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=DictCursor)
        if round_name:
            cursor.execute(
                "SELECT * FROM responses WHERE session_id = %s AND round_name = %s ORDER BY id",
                (session_id, round_name),
            )
        else:
            cursor.execute(
                "SELECT * FROM responses WHERE session_id = %s ORDER BY id",
                (session_id,),
            )
        rows = cursor.fetchall()
        cursor.close()
        
        # Convert to dict list
        rows = [dict(r) for r in rows]
        for row in rows:
            for key in ("options", "rubric"):
                if isinstance(row.get(key), str):
                    try:
                        row[key] = json.loads(row[key])
                    except (TypeError, json.JSONDecodeError):
                        pass
        return rows