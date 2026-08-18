"""
round1_engine.py
─────────────────
Round 1 = Skills Assessment, two parts:

  Part A — DB-backed MCQs (question_bank.py), 10-15 questions,
  skill-targeted and honeypot-difficulty-calibrated. Gating logic:

    - Score clearly high (>= PASS_PCT)   -> proceed straight to Part B.
    - Score clearly low  (< REJECT_PCT)  -> stop immediately, graceful
      exit screen, system auto-rejects (below-average = reject, per spec).
    - Borderline (REJECT_PCT..PASS_PCT)  -> dynamic extension: ask
      BONUS_QUESTIONS more before making the final call, rather than
      guessing off a small sample.

  Part B — 2-3 open-ended questions generated live by Gemini, strictly
  from the candidate's own resume projects (not generic questions),
  graded with the same 0-10 rubric approach as the existing
  gemini_client.grade_answer().

Round 1 overall result feeds compute_round1_result(), which the caller
(app.py) uses to decide: auto-reject, or hand off to Round 2 (which is
an employer-manual decision per spec — the system does not auto-advance
past Round 1 into Round 2).
"""

import json

import gemini_client
import question_bank

PART_A_PASS_PCT = 70        # clearly good -> go straight to Part B
PART_A_REJECT_PCT = 40      # clearly bad -> reject, no extension needed
BONUS_QUESTIONS = 5         # borderline -> test this many more before deciding
BORDERLINE_REJECT_PCT = 50  # after extension, this is the final bar

PART_B_QUESTION_COUNT = 3


def score_part_a(is_correct_list):
    """is_correct_list: list of booleans, one per answered MCQ."""
    if not is_correct_list:
        return 0.0
    correct = sum(1 for c in is_correct_list if c)
    return round((correct / len(is_correct_list)) * 100, 1)


def part_a_gate(pct, already_extended):
    """
    Returns one of: "advance" (go to Part B), "reject" (auto-reject,
    graceful exit), "extend" (ask BONUS_QUESTIONS more, only once).
    """
    if pct >= PART_A_PASS_PCT:
        return "advance"
    if pct < PART_A_REJECT_PCT:
        return "reject"
    if already_extended:
        return "advance" if pct >= BORDERLINE_REJECT_PCT else "reject"
    return "extend"


def generate_part_b_questions(projects, skills):
    """
    Gemini-generated, strictly from the candidate's own projects — this
    is the part that catches resume padding: if you didn't build it,
    you can't answer specifics about it.

    ONE Gemini call generates all PART_B_QUESTION_COUNT questions
    together (matching round2_engine/round3_engine's batching) — looping
    a separate call per project would multiply spend for no benefit.
    """
    if not projects:
        # No parsed projects (e.g. thin resume) -> fall back to
        # experience-grounded questions using top skills instead.
        skill_str = ", ".join((skills or [])[:3]) or "their listed skills"
        return [{
            "question": (
                f"Describe a real project or task where you applied {skill_str}. "
                "What was the specific problem, what did you build or decide, "
                "and what was the outcome?"
            ),
            "model_answer": "",
            "rubric": ["Describes a concrete, specific problem", "Explains their own contribution",
                       "States a measurable or observable outcome"],
            "skill": skill_str,
        }]

    project_lines = []
    for p in projects[:PART_B_QUESTION_COUNT]:
        project_lines.append(
            f"- {p.get('name', '')}: {p.get('description', '')} "
            f"(tech: {', '.join(p.get('technologies', []))})"
        )
    projects_block = "\n".join(project_lines)

    prompt = f"""You are interviewing a candidate about projects they listed on
their resume. For EACH project below, write ONE specific, hard-to-fake
follow-up question that only someone who actually built it could answer well
— probing a real implementation decision (schema design, a specific
trade-off, how a tricky edge case was handled), not something answerable
generically from the description alone.

PROJECTS:
{projects_block}

Return ONLY valid JSON, no markdown fences, as a list with one entry per
project, in the same order:
[{{"question": "...", "model_answer": "brief ideal-answer outline",
   "rubric": ["criterion1", "criterion2", "criterion3"], "project_name": "..."}}]
"""
    result = gemini_client._call_gemini_json(prompt)
    questions = []
    if isinstance(result, list):
        for i, q in enumerate(result[:PART_B_QUESTION_COUNT]):
            if isinstance(q, dict) and "question" in q:
                q.setdefault("model_answer", "")
                q.setdefault("rubric", [])
                proj = projects[i] if i < len(projects) else {}
                q["skill"] = q.get("project_name") or proj.get("name", "")
                questions.append(q)

    if not questions:
        # Batched call failed/malformed — fall back to one safe generic
        # question per project rather than making N more Gemini calls.
        for p in projects[:PART_B_QUESTION_COUNT]:
            questions.append({
                "question": f"Walk me through how you built \"{p.get('name', 'this project')}\" — what was the hardest technical decision, and why did you make it that way?",
                "model_answer": "",
                "rubric": ["Specific technical detail", "Clear reasoning for the decision", "Awareness of trade-offs"],
                "skill": p.get("name", ""),
            })

    if len(questions) < PART_B_QUESTION_COUNT and skills:
        skill_str = ", ".join(skills[:2])
        questions.append({
            "question": f"Beyond your listed projects, describe a specific challenge you solved using {skill_str}.",
            "model_answer": "",
            "rubric": ["Specific example", "Own contribution clear", "Technically credible"],
            "skill": skill_str,
        })
    return questions[:PART_B_QUESTION_COUNT]


def grade_part_b(question, rubric, model_answer, candidate_answer):
    """Returns (score_0_to_10, justification)."""
    result = gemini_client.grade_answer(question, rubric, model_answer, candidate_answer)
    return result["score"], result.get("justification", "")


def score_part_b(gemini_scores):
    if not gemini_scores:
        return 0.0
    avg = sum(gemini_scores) / len(gemini_scores)
    return round((avg / 10) * 100, 1)


def compute_round1_result(mcq_pct, part_b_pct):
    """
    Final Round 1 verdict. Weighted blend of Part A (DB MCQ — verifies
    breadth/claims) and Part B (Gemini subjective — verifies depth on
    their own projects). Below-average overall -> auto-reject.
    """
    if part_b_pct is None:
        overall = mcq_pct
    else:
        overall = round(mcq_pct * 0.55 + part_b_pct * 0.45, 1)

    if overall >= 60:
        verdict = "pass"
    else:
        verdict = "reject"
    return {"round1_score": overall, "verdict": verdict}