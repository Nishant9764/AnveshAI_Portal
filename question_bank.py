"""
question_bank.py
─────────────────
Round 1 / Part A pulls MCQs from the `questions` table — never generates
them live — so the test is fast, cheap, and can't be prompt-injected via
a resume. Selection logic:

  - Pick 2-3 questions per skill the candidate actually claims (so a
    FastAPI claim gets FastAPI questions, not generic backend trivia).
  - Difficulty is "honeypot calibrated": if the ATS screen inferred a
    senior-looking resume, pull harder questions to verify the claim.
    A resume that reads senior but can only answer junior MCQs is
    exactly the signal this is designed to catch.
  - Always lands in the 10-15 question band the spec asks for, topping
    up from a general pool for the tested skills if any one skill is
    thin in the bank, and trimming evenly if we overshoot.
"""

import random

import db

MIN_QUESTIONS = 10
MAX_QUESTIONS = 15
PER_SKILL_TARGET = 3

DIFFICULTY_BY_SENIORITY = {
    "junior": ["easy", "easy", "medium"],
    "mid": ["easy", "medium", "medium"],
    "senior": ["medium", "hard", "hard"],
}


def _fetch_for_skill(skill, difficulties, exclude_ids, limit):
    """Fetch up to `limit` questions for one skill, preferring the
    honeypot-calibrated difficulty order, falling back to any difficulty
    for that skill if the bank is thin.

    Matching is trimmed + case-insensitive on both skill and difficulty —
    real-world/imported datasets are never perfectly normalized, and a
    stray space or "Python " vs "python" shouldn't silently produce zero
    matches."""
    picked = []
    seen = set(exclude_ids)

    for diff in difficulties:
        if len(picked) >= limit:
            break
        rows = db.query_all(
            """SELECT * FROM questions
               WHERE lower(trim(skill)) = lower(trim(%s))
                 AND lower(trim(difficulty)) = lower(trim(%s))
                 AND lower(trim(question_type)) = 'mcq'
               ORDER BY random() LIMIT %s""",
            (skill, diff, limit - len(picked)),
        )
        for r in rows:
            if r["id"] not in seen:
                picked.append(r)
                seen.add(r["id"])

    if len(picked) < limit:
        rows = db.query_all(
            """SELECT * FROM questions
               WHERE lower(trim(skill)) = lower(trim(%s))
                 AND lower(trim(question_type)) = 'mcq'
               ORDER BY random() LIMIT %s""",
            (skill, limit - len(picked)),
        )
        for r in rows:
            if r["id"] not in seen:
                picked.append(r)
                seen.add(r["id"])

    return picked


def select_round1_mcqs(skills, seniority="mid"):
    """
    skills: list of skill names extracted from the resume (Round 1
    should test what THEY claimed, not the whole JD).
    seniority: 'junior' | 'mid' | 'senior' — from ats_engine's honeypot
    calibration signal.

    Returns a list of question rows (dicts), length in [MIN_QUESTIONS,
    MAX_QUESTIONS], or fewer only if the bank truly can't fill it —
    caller should treat < MIN_QUESTIONS as "bank not seeded for this
    skill set" and fall back gracefully.
    """
    difficulties = DIFFICULTY_BY_SENIORITY.get(seniority, DIFFICULTY_BY_SENIORITY["mid"])
    skills = [s for s in (skills or []) if s and s.strip()] or ["General"]
    random.shuffle(skills)

    picked = []
    exclude_ids = []
    for skill in skills:
        got = _fetch_for_skill(skill, difficulties, exclude_ids, PER_SKILL_TARGET)
        picked.extend(got)
        exclude_ids.extend(q["id"] for q in got)
        if len(picked) >= MAX_QUESTIONS:
            break

    # Top up from a general pool across the same skills if short of the minimum.
    if len(picked) < MIN_QUESTIONS:
        need = MIN_QUESTIONS - len(picked)
        rows = db.query_all(
            """SELECT * FROM questions
               WHERE lower(trim(question_type)) = 'mcq' AND id != ALL(%s)
               ORDER BY random() LIMIT %s""",
            (exclude_ids or [-1], need),
        )
        picked.extend(rows)

    random.shuffle(picked)
    return picked[:MAX_QUESTIONS]


def select_bonus_questions(skills, seniority, exclude_ids, count):
    """Dynamic extension: a handful of extra questions for candidates who
    land in the borderline band after the first 10-15."""
    difficulties = DIFFICULTY_BY_SENIORITY.get(seniority, DIFFICULTY_BY_SENIORITY["mid"])
    skills = [s for s in (skills or []) if s and s.strip()] or ["General"]
    random.shuffle(skills)
    picked = []
    for skill in skills:
        if len(picked) >= count:
            break
        got = _fetch_for_skill(skill, difficulties, exclude_ids + [q["id"] for q in picked], 2)
        picked.extend(got)
    return picked[:count]