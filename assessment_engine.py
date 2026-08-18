import gemini_client


# ══════════════════════════════════════════════════════════
#  CONFIG — tune these without touching logic
# ══════════════════════════════════════════════════════════

CONFIG = {
    "max_skills_tested": 6,
    "basic_questions_per_round": 8,
    "medium_questions_per_round": 5,
    "advanced_questions_per_round": 3,

    "gate_basic_to_medium_pct": 50,   # must score >= this to unlock Medium
    "gate_medium_to_advanced_pct": 60,  # must score >= this to unlock Advanced

    "weight_basic": 0.20,
    "weight_medium": 0.35,
    "weight_advanced": 0.45,

    "verdict_strong_fit_pct": 75,
    "verdict_borderline_pct": 50,
}


# ══════════════════════════════════════════════════════════
#  SKILL MATCHING — JD required/preferred ∩ resume skills
# ══════════════════════════════════════════════════════════

def _normalize(skill):
    return skill.strip().lower()


def select_skills_to_test(resume_skills, jd_required_skills, jd_preferred_skills):
    """
    Decides which skills actually get tested, and flags JD-required skills
    the candidate never claimed (useful to surface to a recruiter even
    though we don't/can't test a skill nobody claimed to have).

    Priority order for the skills we DO test:
      1. In both JD-required AND resume       (verify the strongest claims first)
      2. In both JD-preferred AND resume       (still relevant to this JD)
      3. Resume-only skills, to fill remaining slots if JD list is short

    Returns: (skills_to_test: list[str], missing_required_skills: list[str])
    """
    resume_norm = {_normalize(s): s for s in resume_skills}
    required_norm = {_normalize(s) for s in jd_required_skills}
    preferred_norm = {_normalize(s) for s in jd_preferred_skills}

    matched_required = [resume_norm[s] for s in required_norm if s in resume_norm]
    matched_preferred = [resume_norm[s] for s in preferred_norm
                          if s in resume_norm and s not in required_norm]

    missing_required = [s for s in jd_required_skills if _normalize(s) not in resume_norm]

    skills_to_test = []
    seen = set()
    for s in matched_required + matched_preferred:
        key = _normalize(s)
        if key not in seen:
            skills_to_test.append(s)
            seen.add(key)

    # If JD didn't give us enough overlap to fill the round (e.g. sparse JD,
    # or JD text wasn't provided at all), top up with the candidate's own
    # top resume skills so the assessment still has enough to test.
    if len(skills_to_test) < CONFIG["max_skills_tested"]:
        for s in resume_skills:
            key = _normalize(s)
            if key not in seen:
                skills_to_test.append(s)
                seen.add(key)
            if len(skills_to_test) >= CONFIG["max_skills_tested"]:
                break

    return skills_to_test[:CONFIG["max_skills_tested"]], missing_required


# ══════════════════════════════════════════════════════════
#  ROUND GENERATION
# ══════════════════════════════════════════════════════════

def generate_basic_round(skills):
    """Returns a list of question dicts for the MCQ round, spread across skills."""
    n = CONFIG["basic_questions_per_round"]
    questions = []
    asked_by_skill = {s: [] for s in skills}
    for i in range(n):
        skill = skills[i % len(skills)]
        q = gemini_client.generate_mcq_question(skill, prior_questions=asked_by_skill[skill])
        q["skill"] = skill
        q["round_name"] = "basic"
        asked_by_skill[skill].append(q["question"])
        questions.append(q)
    return questions


def generate_medium_round(skills):
    """Returns a list of question dicts for the short-answer round, one per skill (capped)."""
    n = min(CONFIG["medium_questions_per_round"], len(skills))
    questions = []
    for i in range(n):
        skill = skills[i]
        q = gemini_client.generate_short_answer_question(skill)
        q["skill"] = skill
        q["round_name"] = "medium"
        questions.append(q)
    return questions


def generate_advanced_round(skills):
    """
    Returns a list of question dicts for the long-answer round.
    These are deliberately cross-skill scenario questions, pairing up
    skills where possible rather than testing each in isolation —
    that's what actually distinguishes real experience from rote knowledge.
    """
    n = CONFIG["advanced_questions_per_round"]
    questions = []
    if len(skills) >= 2:
        pairs = [[skills[i % len(skills)], skills[(i + 1) % len(skills)]] for i in range(n)]
    else:
        pairs = [[skills[0]] for _ in range(n)]

    for skill_pair in pairs:
        q = gemini_client.generate_long_answer_question(skill_pair)
        q["skill"] = " + ".join(skill_pair)
        q["round_name"] = "advanced"
        questions.append(q)
    return questions


# ══════════════════════════════════════════════════════════
#  SCORING — Gemini never computes a percentage; this does
# ══════════════════════════════════════════════════════════

def score_basic_round(responses):
    """
    responses: list of {"correct_index": int, "selected_index": int}
    MCQ is graded in plain Python — exact match, no Gemini call needed.
    Questions with correct_index == -1 (generation failed) are excluded
    from the denominator entirely so they don't unfairly penalize the candidate.
    """
    scorable = [r for r in responses if r.get("correct_index", -1) != -1]
    if not scorable:
        return 0.0
    correct = sum(1 for r in scorable if r["selected_index"] == r["correct_index"])
    return round((correct / len(scorable)) * 100, 1)


def score_open_round(gemini_scores):
    """
    gemini_scores: list of 0-10 floats from gemini_client.grade_answer()
    Used for both Medium and Advanced rounds — same 0-10 scale either way,
    so the normalization is identical.
    """
    if not gemini_scores:
        return 0.0
    avg = sum(gemini_scores) / len(gemini_scores)
    return round((avg / 10) * 100, 1)


def should_advance_to_medium(basic_pct):
    return basic_pct >= CONFIG["gate_basic_to_medium_pct"]


def should_advance_to_advanced(medium_pct):
    return medium_pct >= CONFIG["gate_medium_to_advanced_pct"]


def compute_final_result(basic_pct, medium_pct, advanced_pct):
    """
    Weighted blend ONLY over rounds actually completed, renormalized so a
    candidate who stopped early gets an honest score for what they did
    complete — not a fabricated 3-round average with zeros baked in.

    Returns: {"final_score": float, "verdict": str, "status": str}
    """
    weights_present = []
    scores_present = []

    if basic_pct is not None:
        weights_present.append(CONFIG["weight_basic"])
        scores_present.append(basic_pct)
    if medium_pct is not None:
        weights_present.append(CONFIG["weight_medium"])
        scores_present.append(medium_pct)
    if advanced_pct is not None:
        weights_present.append(CONFIG["weight_advanced"])
        scores_present.append(advanced_pct)

    if not scores_present:
        return {"final_score": 0.0, "verdict": "Not a fit", "status": "no_data"}

    total_weight = sum(weights_present)
    final_score = sum(w * s for w, s in zip(weights_present, scores_present)) / total_weight
    final_score = round(final_score, 1)

    if advanced_pct is not None:
        status = "complete"
    elif medium_pct is not None:
        status = "stopped_medium"
    else:
        status = "stopped_basic"

    if final_score >= CONFIG["verdict_strong_fit_pct"] and status == "complete":
        verdict = "Strong fit"
    elif final_score >= CONFIG["verdict_borderline_pct"]:
        verdict = "Borderline"
    else:
        verdict = "Not a fit"

    return {"final_score": final_score, "verdict": verdict, "status": status}