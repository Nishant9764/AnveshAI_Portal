"""
ats_engine.py
─────────────
The real resume-vs-JD scoring pipeline. Replaces the old random
compute_match_score() heuristic in app.py, and folds in what
round_one_ats/ was doing as a separate microservice.

Pipeline for every application:
  1. resume_parser.process_resume()  -> PII-redacted text + structured
     skills / experience / projects (already built, reused as-is).
  2. strip_bias()                    -> additionally blanks out college
     names, graduation years, and employer/organization names so the
     grading model can't anchor on pedigree/brand instead of merit.
  3. gemini_client.parse_jd_skills() -> required vs preferred skills
     from the JD (already built, reused as-is).
  4. _call_gemini_screen()           -> ONE structured Gemini call that
     returns four sub-scores (technical / experience / soft-skills /
     impact) + matched/missing skills + red flags. Gemini never
     computes the final blended number — that's done in plain Python
     below, same philosophy as assessment_engine.py.
  5. compute_overall_score()         -> weighted blend -> 0-100 int.

Returns a single dict that app.py stores directly onto the
`applications` row.
"""

import json
import logging
import re

import gemini_client
import resume_parser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ats_engine")

# Weighted blend for the overall ats_score shown to employers.
# Technical + experience carry the most weight; soft-skills and impact
# are meaningful differentiators but shouldn't dominate a hard-skills gate.
WEIGHTS = {
    "technical": 0.40,
    "experience": 0.25,
    "soft_skills": 0.15,
    "impact": 0.20,
}

_UNIVERSITY_RE = re.compile(
    r"\b[A-Z][A-Za-z.&' \-]{2,60}\b(University|Institute of Technology|"
    r"Institute|College|Polytechnic|IIT|NIT|IIIT|BITS)\b[A-Za-z.&' \-,]*",
    re.IGNORECASE,
)
_GRAD_YEAR_RE = re.compile(r"\b(19|20)\d{2}\s*(-|to|–)\s*(19|20)\d{2}\b|\b(19|20)\d{2}\b")


def strip_bias(masked_text, sections=None):
    """
    Runs on top of resume_parser's PII redaction. Removes signals that
    can bias an LLM grader toward pedigree rather than merit: college /
    university names and graduation years. Company names in the
    Experience section are left as-is at the text level (resume_parser
    already separates company from role/responsibilities, and dropping
    them entirely would remove legitimate "worked in a regulated
    industry" context) but are excluded from what we send for the
    Experience/Technical scoring by only forwarding parsed
    responsibilities + duration, never the raw employer header line.
    """
    text = _UNIVERSITY_RE.sub("[EDUCATION INSTITUTION]", masked_text)
    text = _GRAD_YEAR_RE.sub("[YEAR]", text)
    return text


def _bias_stripped_experience(experience_entries):
    """Send role + duration + responsibilities, never the company name."""
    cleaned = []
    for e in experience_entries or []:
        cleaned.append({
            "role": e.get("role", ""),
            "duration": strip_bias(e.get("duration", "")),
            "responsibilities": [strip_bias(r) for r in e.get("responsibilities", [])],
        })
    return cleaned


def _bias_stripped_projects(project_entries):
    cleaned = []
    for p in project_entries or []:
        cleaned.append({
            "name": p.get("name", ""),
            "description": strip_bias(p.get("description", "")),
            "technologies": p.get("technologies", []),
        })
    return cleaned


SCREEN_PROMPT = """You are a skeptical, senior technical recruiter conducting
a rigorous first-pass screen. You are given a candidate's resume (already
stripped of name, contact details, college name, and graduation years) and a
job description. Score the candidate PURELY on demonstrated skills,
experience, and merit — never on pedigree, which has been removed.

GRADING CALIBRATION — read carefully before scoring:
- Use the FULL 0-100 range. Do not default to a "safe" 60-75 band. A resume
  with no real overlap with the JD should score in the 0-30s. A resume that
  is a genuinely strong, specific, evidence-backed match should score 85+.
  Most real candidates land in the 35-70 band — reserve high scores for
  resumes that earn them.
- A skill only counts as "matched" if the resume shows it being USED
  (in a project, a responsibility, an outcome) — not merely listed in a
  skills section with no supporting evidence elsewhere. Listed-but-unused
  skills should lower confidence, not raise the match score.
- Do not let a long skills list compensate for thin, vague, or generic
  experience descriptions. Specificity and evidence outweigh keyword count.
- Be explicitly skeptical of resumes that read as keyword-stuffed relative
  to the JD (skills section suspiciously mirrors the JD's exact wording with
  no corroborating detail elsewhere) — flag this as a red flag, not a match.
- If required skills are missing, technical_match_score MUST reflect that
  materially (do not average it away with unrelated strengths).

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"

JD REQUIRED SKILLS: {required_skills}
JD PREFERRED SKILLS: {preferred_skills}

CANDIDATE RESUME DATA (bias-stripped):
Skills claimed: {skills}
Experience entries (role/duration/responsibilities only, no employer names): {experience}
Projects: {projects}

Score the candidate on FOUR separate dimensions, each 0-100:

1. "technical_match_score" — do they have the hard/coding skills the JD
   asks for, WITH EVIDENCE OF USE (not just listed)? Weight required
   skills heavily, preferred skills lightly. Missing required skills should
   pull this score down materially, not marginally.

2. "experience_match_score" — do their years of experience and seniority
   level match what the JD implies (junior/mid/senior)? Consider role
   titles and responsibility complexity, not just raw years. A senior-level
   JD paired with junior-level evidence should score low here even if the
   skills match.

3. "soft_skills_score" — does the resume show CONCRETE EVIDENCE of soft
   skills the JD cares about (leadership, collaboration, communication,
   ownership) — e.g. "led a team of 4", "coordinated with stakeholders" —
   not just the candidate listing the word as a skill. If the JD doesn't
   ask for soft skills, score based on general evidence of ownership. No
   evidence found = low score, not a neutral default.

4. "impact_score" — Impact vs. Duty analysis. Does the candidate quantify
   their achievements with metrics/outcomes ("increased sales by 20%",
   "reduced latency by 300ms", "cut costs by $50k") rather than just
   listing duties ("responsible for sales", "worked on backend")? Score
   high ONLY for genuine quantified impact — vague claims of impact
   ("significantly improved performance") without a number score low.

Also return:
- "matched_skills": JD skills the resume clearly demonstrates WITH EVIDENCE
  (not just present in a skills list)
- "missing_skills": JD REQUIRED skills the resume does not demonstrate
- "red_flags": concerns (unexplained gaps, vague/unverifiable claims,
  responsibilities inconsistent with claimed seniority, keyword-stuffed
  skills with no corroborating evidence, skills claimed but never used
  anywhere in experience/projects, etc.) — empty list only if genuinely none
- "justification_summary": 2-3 sentence explanation of the overall picture,
  grounded in specific evidence from the resume, not generic praise

Return ONLY valid JSON, no markdown fences, in exactly this shape:
{{"technical_match_score": 0, "experience_match_score": 0,
  "soft_skills_score": 0, "impact_score": 0,
  "matched_skills": [], "missing_skills": [], "red_flags": [],
  "justification_summary": ""}}
"""


def _call_gemini_screen(jd_text, required_skills, preferred_skills, skills, experience, projects):
    prompt = SCREEN_PROMPT.format(
        jd_text=jd_text or "(no description provided)",
        required_skills=json.dumps(required_skills),
        preferred_skills=json.dumps(preferred_skills),
        skills=json.dumps(skills),
        experience=json.dumps(_bias_stripped_experience(experience)),
        projects=json.dumps(_bias_stripped_projects(projects)),
    )
    result = gemini_client._call_gemini_json(prompt, temperature=0.15)

    if not result:
        # Safe fallback: never crash the apply flow because Gemini hiccuped.
        return {
            "technical_match_score": 0, "experience_match_score": 0,
            "soft_skills_score": 0, "impact_score": 0,
            "matched_skills": [], "missing_skills": list(required_skills or []),
            "red_flags": ["Automated scoring unavailable — flagged for manual review."],
            "justification_summary": "Automated ATS scoring failed; needs manual review.",
        }

    for key in ("technical_match_score", "experience_match_score",
                "soft_skills_score", "impact_score"):
        try:
            result[key] = max(0, min(100, int(result.get(key, 0))))
        except (TypeError, ValueError):
            result[key] = 0
    result.setdefault("matched_skills", [])
    result.setdefault("missing_skills", [])
    result.setdefault("red_flags", [])
    result.setdefault("justification_summary", "")
    return result


def compute_overall_score(sub_scores):
    key_map = {
        "technical": "technical_match_score",
        "experience": "experience_match_score",
        "soft_skills": "soft_skills_score",
        "impact": "impact_score",
    }
    total = sum(sub_scores[key_map[k]] * WEIGHTS[k] for k in WEIGHTS)
    return round(total)


def experience_level_from_score(technical_score, experience_score):
    """Honeypot calibration input: infer how 'senior' this resume reads,
    so Round 1 MCQ difficulty can be picked to match (or stress-test)
    the claim."""
    avg = (technical_score + experience_score) / 2
    if avg >= 80:
        return "senior"
    if avg >= 55:
        return "mid"
    return "junior"


def screen_application(resume_file_obj, jd_text, jd_skills=None):
    """
    Entry point for a freshly-uploaded resume. resume_file_obj: an open,
    seekable file handle to the resume PDF. Extracts + parses it, then
    delegates to screen_parsed_resume().
    """
    raw_text = resume_parser.extract_text_from_pdf(resume_file_obj)
    if not raw_text or not raw_text.strip():
        raise ValueError("Could not extract any text from this resume file.")

    parsed = resume_parser.process_resume(raw_text)
    result = screen_parsed_resume(parsed, jd_text, jd_skills)
    result["_resume_raw_text"] = raw_text
    return result


def screen_parsed_resume(parsed, jd_text, jd_skills=None):
    """
    Entry point for an already-parsed resume (e.g. re-used from the
    `resumes` table when a candidate picks an existing resume instead
    of uploading a new one — skips PDF extraction entirely).

    jd_skills: optional pre-computed
    {"required_skills": [...], "preferred_skills": [...]} to avoid a
    duplicate Gemini call when the job posting already parsed them.

    Returns a dict ready to spread onto the `applications` row, plus
    the parsed resume data (skills/experience/projects/masked_text) so
    the caller can persist it to the `resumes` table too.
    """
    if jd_skills is None:
        jd_skills = gemini_client.parse_jd_skills(jd_text or "")
    required = jd_skills.get("required_skills", [])
    preferred = jd_skills.get("preferred_skills", [])

    sub_scores = _call_gemini_screen(
        jd_text=jd_text,
        required_skills=required,
        preferred_skills=preferred,
        skills=parsed["skills"],
        experience=parsed["experience"],
        projects=parsed["projects"],
    )

    overall = compute_overall_score(sub_scores)
    seniority = experience_level_from_score(
        sub_scores["technical_match_score"], sub_scores["experience_match_score"]
    )

    return {
        "ats_score": overall,
        "technical_match_score": sub_scores["technical_match_score"],
        "experience_match_score": sub_scores["experience_match_score"],
        "soft_skills_score": sub_scores["soft_skills_score"],
        "impact_score": sub_scores["impact_score"],
        "matched_skills": sub_scores["matched_skills"],
        "missing_skills": sub_scores["missing_skills"],
        "red_flags": sub_scores["red_flags"],
        "ats_summary": sub_scores["justification_summary"],
        "inferred_seniority": seniority,
        "jd_required_skills": required,
        "jd_preferred_skills": preferred,
        # resume record fields, to persist in `resumes`
        "_resume_raw_text": parsed.get("raw_text", ""),
        "_resume_masked_text": parsed["masked_resume"],
        "_resume_skills": parsed["skills"],
        "_resume_experience": parsed["experience"],
        "_resume_projects": parsed["projects"],
        "_resume_name_found": parsed["name_found"],
    }
