"""
resume_bank.py
──────────────
CRUD for the `resumes` table. A candidate can upload once and reuse
that resume on future applications ("select resume") or upload a new
one at apply time — no separate cover letter step, per spec.
"""

import json
import db


def list_for_candidate(candidate_id):
    rows = db.query_all(
        """SELECT id, filename, uploaded_at, is_default, experience_yrs,
                  skills, name_found
           FROM resumes WHERE candidate_id = %s
           ORDER BY is_default DESC, uploaded_at DESC""",
        (candidate_id,),
    )
    for r in rows:
        if isinstance(r.get("skills"), str):
            try:
                r["skills"] = json.loads(r["skills"])
            except (TypeError, ValueError):
                r["skills"] = []
    return rows


def get(resume_id, candidate_id=None):
    if candidate_id is not None:
        row = db.query_one(
            "SELECT * FROM resumes WHERE id=%s AND candidate_id=%s", (resume_id, candidate_id)
        )
    else:
        row = db.query_one("SELECT * FROM resumes WHERE id=%s", (resume_id,))
    return row


def save(candidate_id, filename, file_path, parsed, experience_yrs=0, make_default=True):
    """parsed: the dict returned by resume_parser.process_resume(), plus
    masked_resume text. Stores structured data so future applications can
    reuse this resume without re-parsing the PDF."""
    if make_default:
        db.execute("UPDATE resumes SET is_default=FALSE WHERE candidate_id=%s", (candidate_id,))

    resume_id = db.execute(
        """INSERT INTO resumes
           (candidate_id, filename, file_path, raw_text, masked_text,
            skills, experience, projects, experience_yrs, name_found, is_default)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (candidate_id, filename, file_path,
         parsed.get("raw_text", ""), parsed.get("masked_resume", ""),
         json.dumps(parsed.get("skills", [])),
         json.dumps(parsed.get("experience", [])),
         json.dumps(parsed.get("projects", [])),
         experience_yrs, parsed.get("name_found", ""), make_default),
    )
    return resume_id


def as_parsed_dict(resume_row):
    """Reconstruct the shape ats_engine expects from a stored resume row."""
    def _j(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v or []

    return {
        "masked_resume": resume_row.get("masked_text", ""),
        "skills": _j(resume_row.get("skills")),
        "experience": _j(resume_row.get("experience")),
        "projects": _j(resume_row.get("projects")),
        "name_found": resume_row.get("name_found", ""),
    }
