"""
resume_score_engine.py
────────────────────────
The OTHER scorer: standalone resume quality, with no JD to compare
against. This is what powers the candidate-facing "Resume Score" page
— "how strong is this resume on its own merits" — as opposed to
ats_engine.py, which scores a resume AGAINST a specific job description.

Previously this page just did `random.randint(68, 95)` and showed three
hardcoded skill tags. This replaces that with one real, structured
Gemini call, graded by the same strict, evidence-first standard as the
JD-matching engine, so a candidate can't get a high standalone score
for a vague resume the JD engine would score harshly.
"""

import json
import logging

import gemini_client
import resume_parser
from ats_engine import _bias_stripped_experience, _bias_stripped_projects

logger = logging.getLogger("resume_score_engine")

WEIGHTS = {
    "skills_depth": 0.25,
    "experience_clarity": 0.25,
    "impact_score": 0.30,
    "ats_friendliness": 0.20,
}

SCORE_PROMPT = """You are a skeptical, senior technical recruiter reviewing a
resume with NO specific job in mind — you're assessing how strong this resume
is on its own merits, the way it would read to any hiring manager.

Grade strictly and use the FULL 0-100 range. Most real resumes should land
in the 40-75 band; reserve 85+ for resumes with genuinely strong, specific,
quantified evidence throughout. A resume that just lists skills and duties
with no evidence or metrics should score LOW on impact and experience
clarity, regardless of how many skills are listed. Do not inflate scores to
be encouraging — the candidate needs an honest signal, not a participation
score.

RESUME DATA (already stripped of name, contact info, college name, and
graduation years):
Skills claimed: {skills}
Experience entries (role/duration/responsibilities, no employer names): {experience}
Projects: {projects}
Structural signal: {structure_note}

Score FOUR dimensions, each 0-100:

1. "skills_depth" — breadth AND depth of skills. Penalize a long list of
   buzzwords with no supporting evidence in experience/projects; reward
   skills that are clearly backed up by actual usage.

2. "experience_clarity" — are roles, durations, and scope described clearly
   enough that a reader understands what this person actually did and at
   what level of seniority? Vague one-line bullet points score low.

3. "impact_score" — Impact vs. Duty analysis. Does the resume quantify
   outcomes ("reduced latency by 300ms", "grew revenue 20%") rather than
   just listing duties ("responsible for X", "worked on Y")? Score high
   ONLY for genuine quantified impact, not vague claims of impact.

4. "ats_friendliness" — based on the structural signal given, does this
   resume have clearly parseable sections (skills/experience/projects
   distinctly identifiable) that an automated system or a fast human skim
   could reliably extract? Penalize if sections were hard to detect.

Also return:
- "strengths": 2-4 short, SPECIFIC strengths (reference actual content, not
  generic praise like "good communication skills")
- "weaknesses": 2-4 short, SPECIFIC, actionable weaknesses
- "suggested_skills": 3-6 skills that would meaningfully strengthen this
  resume given what's already there — skills genuinely adjacent/complementary
  to their existing stack, not generic buzzwords unrelated to their field
- "summary": 2-3 sentence honest overall assessment

Return ONLY valid JSON, no markdown fences, in exactly this shape:
{{"skills_depth": 0, "experience_clarity": 0, "impact_score": 0, "ats_friendliness": 0,
  "strengths": [], "weaknesses": [], "suggested_skills": [], "summary": ""}}
"""


def _structure_note(parsed):
    found = []
    if parsed.get("skills"):
        found.append(f"{len(parsed['skills'])} skills detected in a distinct Skills section")
    else:
        found.append("no distinct Skills section detected")
    if parsed.get("experience"):
        found.append(f"{len(parsed['experience'])} experience entries detected")
    else:
        found.append("no distinct Experience section detected")
    if parsed.get("projects"):
        found.append(f"{len(parsed['projects'])} project entries detected")
    else:
        found.append("no distinct Projects section detected")
    return "; ".join(found)


def compute_overall_score(sub_scores):
    total = sum(sub_scores[k] * WEIGHTS[k] for k in WEIGHTS)
    return round(total)


def score_resume_file(resume_file_obj):
    """resume_file_obj: an open, seekable file handle to the resume PDF.
    Returns a dict ready to persist + display, plus the parsed resume
    fields (so the caller can save it into the resume bank too)."""
    raw_text = resume_parser.extract_text_from_pdf(resume_file_obj)
    if not raw_text or not raw_text.strip():
        raise ValueError("Could not extract any text from this resume file.")

    parsed = resume_parser.process_resume(raw_text)
    return score_parsed_resume(parsed, raw_text=raw_text)


def score_parsed_resume(parsed, raw_text=""):
    structure_note = _structure_note(parsed)
    prompt = SCORE_PROMPT.format(
        skills=json.dumps(parsed.get("skills", [])),
        experience=json.dumps(_bias_stripped_experience(parsed.get("experience", []))),
        projects=json.dumps(_bias_stripped_projects(parsed.get("projects", []))),
        structure_note=structure_note,
    )
    result = gemini_client._call_gemini_json(prompt, temperature=0.15)

    if not result:
        result = {
            "skills_depth": 0, "experience_clarity": 0, "impact_score": 0, "ats_friendliness": 0,
            "strengths": [], "weaknesses": ["Automated scoring unavailable — try again shortly."],
            "suggested_skills": [], "summary": "Automated scoring failed; please retry.",
        }

    for key in ("skills_depth", "experience_clarity", "impact_score", "ats_friendliness"):
        try:
            result[key] = max(0, min(100, int(result.get(key, 0))))
        except (TypeError, ValueError):
            result[key] = 0
    result.setdefault("strengths", [])
    result.setdefault("weaknesses", [])
    result.setdefault("suggested_skills", [])
    result.setdefault("summary", "")

    overall = compute_overall_score(result)

    return {
        "resume_score": overall,
        "skills_depth": result["skills_depth"],
        "experience_clarity": result["experience_clarity"],
        "impact_score": result["impact_score"],
        "ats_friendliness": result["ats_friendliness"],
        "strengths": result["strengths"],
        "weaknesses": result["weaknesses"],
        "suggested_skills": result["suggested_skills"],
        "summary": result["summary"],
        "_resume_masked_text": parsed["masked_resume"],
        "_resume_skills": parsed["skills"],
        "_resume_experience": parsed["experience"],
        "_resume_projects": parsed["projects"],
        "_resume_name_found": parsed["name_found"],
        "_resume_raw_text": raw_text,
    }