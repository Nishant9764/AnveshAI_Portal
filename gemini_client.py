"""
gemini_client.py
─────────────────
Single point of contact with the Gemini API. Every call:
  - requests strict JSON output (response_mime_type)
  - validates the shape it gets back
  - retries once on malformed JSON
  - falls back to a safe default rather than raising, so one bad
    Gemini response never crashes a candidate's assessment session

Gemini is NEVER asked to compute percentages, weights, or final verdicts.
It only ever returns small, single-purpose JSON objects (a question, a
grading score, a skill list). All aggregation math lives in
assessment_engine.py, in plain Python, where it's auditable and testable.
"""

import os
import json
import time
import logging

from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gemini_client")

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
API_KEY = os.environ.get("GEMINI_API_KEY", "")

_client = None


def get_client():
    """Lazily create the Gemini client so importing this module never
    fails just because the API key isn't set yet (e.g. during local
    dev before .env is filled in)."""
    global _client
    if _client is None:
        if not API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to your .env file "
                "(see .env.example)."
            )
        _client = genai.Client(api_key=API_KEY)
    return _client


def _call_gemini_json(prompt, max_retries=1, temperature=0.4):
    """Call Gemini once, asking for raw JSON back. Retries once on
    malformed JSON. Returns a parsed dict/list, or None if both
    attempts fail (caller must handle the fallback).

    temperature: lower (~0.1-0.2) for scoring/grading calls where you
    want consistent, repeatable judgments on the same input; the
    default 0.4 suits question generation, where some variety is fine."""
    client = get_client()
    attempt = 0
    last_error = None

    while attempt <= max_retries:
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                ),
            )
            raw_text = response.text.strip()
            return json.loads(raw_text)
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning("Gemini returned malformed JSON (attempt %s): %s", attempt, e)
        except Exception as e:
            last_error = e
            logger.warning("Gemini call failed (attempt %s): %s", attempt, e)
        attempt += 1
        if attempt <= max_retries:
            time.sleep(1)

    logger.error("Gemini call failed after retries: %s", last_error)
    return None


# ══════════════════════════════════════════════════════════
#  1. JD PARSING — extract required vs preferred skills from JD text
# ══════════════════════════════════════════════════════════

def parse_jd_skills(jd_text):
    """
    Returns: {"required_skills": [...], "preferred_skills": [...]}
    Falls back to an empty structure (never None) so callers can always
    safely do jd_skills["required_skills"].
    """
    prompt = f"""You are analyzing a job description to extract the technical
skills it asks for. Read the JD below and classify every distinct skill,
tool, language, or technology it mentions into two buckets:
  - "required_skills": things stated as mandatory / must-have / required
  - "preferred_skills": things stated as nice-to-have / bonus / preferred

Rules:
- Use short, canonical skill names (e.g. "Python" not "Python programming language").
- Do not invent skills that aren't mentioned or clearly implied.
- If the JD doesn't clearly distinguish required vs preferred, use your best
  judgement based on phrasing and ordering (skills listed first / in a
  "requirements" section are usually required).
- Return ONLY valid JSON, no markdown fences, no commentary.

JSON shape to return:
{{"required_skills": ["skill1", "skill2"], "preferred_skills": ["skill3"]}}

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"
"""
    result = _call_gemini_json(prompt)
    if not result or not isinstance(result, dict):
        return {"required_skills": [], "preferred_skills": []}

    result.setdefault("required_skills", [])
    result.setdefault("preferred_skills", [])
    return result


# ══════════════════════════════════════════════════════════
#  2. QUESTION GENERATION
# ══════════════════════════════════════════════════════════

def generate_mcq_question(skill, prior_questions=None):
    """
    Basic round. Returns:
    {"question": "...", "options": ["A", "B", "C", "D"], "correct_index": 0}
    """
    prior_questions = prior_questions or []
    avoid_clause = (
        f"Avoid repeating or closely resembling these already-asked questions: {prior_questions}."
        if prior_questions else ""
    )
    prompt = f"""Create ONE multiple-choice question to test basic/fundamental
knowledge of "{skill}", suitable for a first-round screening test.
The question should test understanding, not trick the candidate with ambiguous wording.
Exactly 4 options, only one correct. {avoid_clause}

Return ONLY valid JSON in this exact shape, no markdown fences:
{{"question": "...", "options": ["...", "...", "...", "..."], "correct_index": 0}}
"correct_index" is the 0-based index of the correct option in "options".
"""
    result = _call_gemini_json(prompt)
    if not result or "options" not in result or len(result.get("options", [])) != 4:
        return {
            "question": f"(Question generation unavailable for {skill} — skipped)",
            "options": ["N/A", "N/A", "N/A", "N/A"],
            "correct_index": -1,  # -1 = unscored, never counted against candidate
        }
    return result


def generate_short_answer_question(skill, prior_questions=None):
    """
    Medium round. Returns:
    {"question": "...", "model_answer": "...", "rubric": ["criterion1", "criterion2", ...]}
    """
    prior_questions = prior_questions or []
    avoid_clause = (
        f"Avoid repeating or closely resembling these already-asked questions: {prior_questions}."
        if prior_questions else ""
    )
    prompt = f"""Create ONE short-answer question to test practical, working
knowledge of "{skill}", suitable for a mid-level screening round. It should
require a 2-5 sentence answer, not a single word, and should test applied
understanding rather than rote definitions. {avoid_clause}

Also provide a brief model answer and a grading rubric of 3-4 short criteria
a grader should check for.

Return ONLY valid JSON in this exact shape, no markdown fences:
{{"question": "...", "model_answer": "...", "rubric": ["criterion1", "criterion2", "criterion3"]}}
"""
    result = _call_gemini_json(prompt)
    if not result or "question" not in result:
        return {
            "question": f"(Question generation unavailable for {skill} — skipped)",
            "model_answer": "",
            "rubric": [],
            "_unscored": True,
        }
    return result


def generate_long_answer_question(skills, prior_questions=None):
    """
    Advanced round. Cross-skill scenario question.
    skills: list of skill names to weave together (e.g. ["Python", "AWS"])
    Returns:
    {"question": "...", "model_answer": "...", "rubric": ["criterion1", ...]}
    """
    prior_questions = prior_questions or []
    skill_str = ", ".join(skills)
    avoid_clause = (
        f"Avoid repeating or closely resembling these already-asked questions: {prior_questions}."
        if prior_questions else ""
    )
    prompt = f"""Create ONE scenario-based, long-answer question for an advanced
screening round that requires the candidate to apply knowledge of: {skill_str}.
It should resemble a real-world problem or design decision (e.g. "how would
you approach X given constraint Y"), require a multi-paragraph answer, and
meaningfully separate candidates with real hands-on experience from those
with only surface knowledge. {avoid_clause}

Also provide a model answer outline and a grading rubric of 4-5 specific
criteria a grader should check for (e.g. "mentions trade-off between X and Y",
"identifies failure mode Z").

Return ONLY valid JSON in this exact shape, no markdown fences:
{{"question": "...", "model_answer": "...", "rubric": ["criterion1", "criterion2", "criterion3", "criterion4"]}}
"""
    result = _call_gemini_json(prompt)
    if not result or "question" not in result:
        return {
            "question": f"(Question generation unavailable for {skill_str} — skipped)",
            "model_answer": "",
            "rubric": [],
            "_unscored": True,
        }
    return result


# ══════════════════════════════════════════════════════════
#  3. ANSWER GRADING (medium + advanced rounds only)
# ══════════════════════════════════════════════════════════

def grade_answer(question, rubric, model_answer, candidate_answer):
    """
    Returns: {"score": 0-10, "justification": "...", "matched_criteria": [...]}
    Score is always on a fixed 0-10 scale regardless of round, so
    assessment_engine.py can normalize consistently. Gemini NEVER
    returns a percentage here — just the raw 0-10 score plus reasoning.
    """
    if not candidate_answer or not candidate_answer.strip():
        return {"score": 0, "justification": "No answer provided.", "matched_criteria": []}

    prompt = f"""You are grading a candidate's answer in a technical screening
assessment. Be fair but rigorous — this score affects a real hiring decision.

QUESTION:
{question}

GRADING RUBRIC (criteria the answer should demonstrate):
{json.dumps(rubric)}

REFERENCE MODEL ANSWER (for your reference only, candidate need not match wording):
{model_answer}

CANDIDATE'S ANSWER:
\"\"\"
{candidate_answer}
\"\"\"

Score the candidate's answer from 0 to 10 based on how well it satisfies the
rubric criteria, technical correctness, and depth of understanding. A score
of 0-3 means largely incorrect/superficial, 4-6 means partially correct,
7-8 means solid and correct, 9-10 means excellent/expert-level.

Return ONLY valid JSON in this exact shape, no markdown fences:
{{"score": 7, "justification": "brief 1-2 sentence reason for the score", "matched_criteria": ["criterion1"]}}
"""
    result = _call_gemini_json(prompt)
    if not result or "score" not in result:
        return {
            "score": 0,
            "justification": "Grading unavailable due to a service error; scored as 0 — flag for manual review.",
            "matched_criteria": [],
            "_grading_failed": True,
        }

    # Clamp defensively — never trust the model to stay in range
    try:
        score = float(result.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    result["score"] = max(0, min(10, score))
    result.setdefault("justification", "")
    result.setdefault("matched_criteria", [])
    return result