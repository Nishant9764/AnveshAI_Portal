"""
question_bank.py
─────────────────
Round 1 / Part A pulls MCQs from the `questions` table.

IMPORTANT:
- Questions are NEVER generated live.
- Round 1 tests only the skills supplied by the resume/ATS pipeline.
- Function names are kept compatible with the existing codebase.
- Uses the existing `db` module for database connections.
- Questions are distributed as evenly as possible across claimed skills.
- Question types are diversified when possible.
- No duplicate questions.
- Returns between 10-15 questions whenever the question bank has
  enough usable questions.
"""

import random
import logging

import db


logger = logging.getLogger("question_bank")


# ============================================================
# CONFIGURATION
# ============================================================

MIN_QUESTIONS = 10
MAX_QUESTIONS = 15

PER_SKILL_TARGET = 3


# Kept for backward compatibility with the existing codebase.
# Difficulty is no longer the primary selector because the newer
# working selector proved that skill accuracy + balanced distribution
# is more reliable.
DIFFICULTY_BY_SENIORITY = {
    "junior": ["easy", "easy", "medium"],
    "mid": ["easy", "medium", "medium"],
    "senior": ["medium", "hard", "hard"],
}


# ============================================================
# NORMALIZATION
# ============================================================

def _normalize_skill(skill):
    """
    Normalize a skill for comparison.

    Examples:
        'Python'       -> 'python'
        ' Python '     -> 'python'
        'FastAPI'      -> 'fastapi'
        'fast api'     -> 'fast api'
    """

    if not skill:
        return ""

    return " ".join(
        str(skill).strip().lower().split()
    )


def _normalize_question_type(question_type):
    """
    Normalize question type for comparison.
    """

    if not question_type:
        return ""

    return " ".join(
        str(question_type).strip().lower().split()
    )


def _clean_skills(skills):
    """
    Remove empty and duplicate skills while preserving the
    original database-facing skill names.

    Example:

        ['Python', ' python ', 'FastAPI', '', None]

    becomes:

        ['Python', 'FastAPI']
    """

    unique_skills = []
    seen = set()

    for skill in skills or []:

        if skill is None:
            continue

        original = str(skill).strip()

        if not original:
            continue

        normalized = _normalize_skill(original)

        if normalized in seen:
            continue

        unique_skills.append(original)
        seen.add(normalized)

    return unique_skills


# ============================================================
# QUESTION AVAILABILITY
# ============================================================

def _get_skill_question_count(skill):
    """
    Return the number of usable questions available for a skill.

    A question is considered usable when both `options` and
    `correct_option` are populated.

    We intentionally do NOT require question_type='mcq' here.

    This is important because imported datasets may contain:
        MCQ
        mcq
        multiple choice
        Multiple Choice
        NULL
        empty values

    The actual usable question data is more important than a
    potentially inconsistent question_type value.
    """

    rows = db.query_all(
        """
        SELECT COUNT(*) AS count
        FROM questions
        WHERE lower(trim(skill)) = lower(trim(%s))
          AND options IS NOT NULL
          AND correct_option IS NOT NULL
        """,
        (skill,),
    )

    if not rows:
        return 0

    return int(rows[0]["count"] or 0)


def _get_question_types(skill):
    """
    Return available question types for a skill with their counts.

    Example:

        {
            'conceptual': 20,
            'code completion': 12,
            'output prediction': 8
        }
    """

    rows = db.query_all(
        """
        SELECT
            lower(trim(question_type)) AS question_type,
            COUNT(*) AS count
        FROM questions
        WHERE lower(trim(skill)) = lower(trim(%s))
          AND options IS NOT NULL
          AND correct_option IS NOT NULL
          AND question_type IS NOT NULL
          AND trim(question_type) <> ''
        GROUP BY lower(trim(question_type))
        ORDER BY count DESC
        """,
        (skill,),
    )

    return {
        row["question_type"]: int(row["count"])
        for row in rows
        if row.get("question_type")
    }


# ============================================================
# FETCH QUESTIONS
# ============================================================

def _fetch_questions_for_skill(
    skill,
    limit,
    exclude_ids=None,
    question_type=None,
):
    """
    Fetch random usable questions for a skill.

    Parameters:
        skill:
            Candidate-claimed skill.

        limit:
            Maximum number of questions.

        exclude_ids:
            Question IDs already selected.

        question_type:
            Optional normalized question type.

    Returns:
        List of question rows.
    """

    if limit <= 0:
        return []

    exclude_ids = list(exclude_ids or [])

    sql = """
        SELECT *
        FROM questions
        WHERE lower(trim(skill)) = lower(trim(%s))
          AND options IS NOT NULL
          AND correct_option IS NOT NULL
    """

    params = [skill]

    if question_type:

        sql += """
            AND lower(trim(question_type))
                = lower(trim(%s))
        """

        params.append(question_type)

    if exclude_ids:

        sql += """
            AND id <> ALL(%s)
        """

        params.append(exclude_ids)

    sql += """
        ORDER BY random()
        LIMIT %s
    """

    params.append(limit)

    return db.query_all(
        sql,
        tuple(params),
    )


# ============================================================
# BACKWARD-COMPATIBLE FETCH FUNCTION
# ============================================================

def _fetch_for_skill(
    skill,
    difficulties,
    exclude_ids,
    limit,
):
    """
    Backward-compatible wrapper used by the existing codebase.

    The old implementation primarily selected by difficulty.

    The updated implementation uses the improved V3 selection
    behavior: usable questions for the claimed skill are selected,
    with question-type diversity handled separately.

    `difficulties` is intentionally retained in the signature so
    existing callers do not break.
    """

    # --------------------------------------------------------
    # First try to respect the supplied difficulty order.
    # --------------------------------------------------------
    #
    # This keeps some of the original honeypot behavior without
    # allowing it to destroy skill selection.
    #
    # If insufficient questions exist at those difficulties,
    # we automatically fall back to any usable question for
    # the claimed skill.

    picked = []
    seen = set(exclude_ids or [])

    for difficulty in difficulties or []:

        if len(picked) >= limit:
            break

        rows = db.query_all(
            """
            SELECT *
            FROM questions
            WHERE lower(trim(skill))
                    = lower(trim(%s))
              AND lower(trim(difficulty))
                    = lower(trim(%s))
              AND options IS NOT NULL
              AND correct_option IS NOT NULL
              AND id <> ALL(%s)
            ORDER BY random()
            LIMIT %s
            """,
            (
                skill,
                difficulty,
                list(seen) or [-1],
                limit - len(picked),
            ),
        )

        for row in rows:

            question_id = row["id"]

            if question_id in seen:
                continue

            picked.append(row)
            seen.add(question_id)

    # --------------------------------------------------------
    # Fallback: any usable question for this skill.
    # --------------------------------------------------------

    if len(picked) < limit:

        rows = _fetch_questions_for_skill(
            skill=skill,
            limit=limit - len(picked),
            exclude_ids=list(seen),
        )

        for row in rows:

            question_id = row["id"]

            if question_id in seen:
                continue

            picked.append(row)
            seen.add(question_id)

    return picked[:limit]


# ============================================================
# QUESTION TYPE SELECTION
# ============================================================

def _choose_question_types(
    available_types,
    count,
):
    """
    Choose question types while trying to maximize diversity.

    Rare types are not mandatory, but we avoid repeatedly choosing
    the same type when multiple types are available.
    """

    if count <= 0 or not available_types:
        return []

    remaining = dict(available_types)

    selected_types = []

    while len(selected_types) < count:

        candidates = [
            question_type
            for question_type, quantity in remaining.items()
            if quantity > 0
        ]

        if not candidates:
            break

        # Prefer types that have not already been selected.
        unseen = [
            question_type
            for question_type in candidates
            if question_type not in selected_types
        ]

        if unseen:

            chosen = random.choice(unseen)

        else:

            chosen = random.choice(candidates)

        selected_types.append(chosen)

        remaining[chosen] -= 1

    return selected_types


# ============================================================
# SELECT QUESTIONS FOR ONE SKILL
# ============================================================

def _select_questions_for_skill(
    skill,
    count,
    exclude_ids,
):
    """
    Select `count` questions for one skill.

    Strategy:

        1. Inspect available question types.
        2. Try to select different types.
        3. Select one random question per chosen type.
        4. Fill any remaining slots from any usable question
           belonging to the same skill.
        5. Never duplicate question IDs.
    """

    if count <= 0:
        return []

    exclude_ids = list(exclude_ids or [])

    available_types = _get_question_types(skill)

    # --------------------------------------------------------
    # No question-type information.
    #
    # Still select valid questions based strictly on skill.
    # --------------------------------------------------------

    if not available_types:

        return _fetch_questions_for_skill(
            skill=skill,
            limit=count,
            exclude_ids=exclude_ids,
        )

    selected_types = _choose_question_types(
        available_types,
        count,
    )

    selected = []
    used_ids = list(exclude_ids)

    # --------------------------------------------------------
    # First pass:
    # One question per selected type.
    # --------------------------------------------------------

    for question_type in selected_types:

        rows = _fetch_questions_for_skill(
            skill=skill,
            limit=1,
            exclude_ids=used_ids,
            question_type=question_type,
        )

        if not rows:
            continue

        question = rows[0]

        selected.append(question)
        used_ids.append(question["id"])

    # --------------------------------------------------------
    # Fallback:
    # Fill remaining slots from the same skill.
    # --------------------------------------------------------

    remaining = count - len(selected)

    if remaining > 0:

        rows = _fetch_questions_for_skill(
            skill=skill,
            limit=remaining,
            exclude_ids=used_ids,
        )

        for row in rows:

            if row["id"] in used_ids:
                continue

            selected.append(row)
            used_ids.append(row["id"])

    return selected[:count]


# ============================================================
# BALANCED SKILL DISTRIBUTION
# ============================================================

def _calculate_skill_distribution(
    skills,
    total_questions,
    available,
):
    """
    Distribute questions as evenly as possible across skills.

    Examples:

        2 skills / 10
            -> 5 / 5

        3 skills / 10
            -> 4 / 3 / 3

        3 skills / 15
            -> 5 / 5 / 5

        4 skills / 15
            -> 4 / 4 / 4 / 3

    Availability is always respected.

    If one skill only has 2 questions, the remaining questions
    are redistributed to other skills.
    """

    distribution = {
        skill: 0
        for skill in skills
    }

    if not skills or total_questions <= 0:
        return distribution

    remaining = total_questions

    # --------------------------------------------------------
    # First pass:
    # Give every available skill at least one question.
    # --------------------------------------------------------

    for skill in skills:

        if remaining <= 0:
            break

        if available.get(skill, 0) <= 0:
            continue

        distribution[skill] = 1
        remaining -= 1

    # --------------------------------------------------------
    # Second pass:
    # Keep adding to the least-loaded skill.
    # --------------------------------------------------------

    order = list(skills)
    random.shuffle(order)

    while remaining > 0:

        candidates = [
            skill
            for skill in order
            if distribution[skill]
            < available.get(skill, 0)
        ]

        if not candidates:
            break

        smallest = min(
            distribution[skill]
            for skill in candidates
        )

        least_loaded = [
            skill
            for skill in candidates
            if distribution[skill] == smallest
        ]

        chosen = random.choice(least_loaded)

        distribution[chosen] += 1
        remaining -= 1

    return distribution


# ============================================================
# MAIN ROUND 1 SELECTOR
# ============================================================

def select_round1_mcqs(
    skills,
    seniority="mid",
):
    """
    Select Round 1 MCQs from the questions table.

    Parameters
    ----------
    skills:
        Skills extracted from the candidate resume/ATS pipeline.

        Example:
            ['Python', 'FastAPI']

    seniority:
        'junior' | 'mid' | 'senior'

        Retained for compatibility with the existing application.

    Rules
    -----
    1. Only candidate-claimed skills are tested.
    2. Skills are normalized and deduplicated.
    3. Questions are distributed evenly across skills.
    4. Question types are diversified where possible.
    5. No duplicate question IDs.
    6. Target is 10 questions.
    7. Maximum is 15 questions.
    8. If the bank is thin, available questions are redistributed.
    9. No live question generation.
    """

    # --------------------------------------------------------
    # Clean incoming skills.
    # --------------------------------------------------------

    skills = _clean_skills(skills)

    if not skills:

        logger.warning(
            "select_round1_mcqs received no usable skills."
        )

        return []

    # --------------------------------------------------------
    # Keep seniority compatibility.
    #
    # We don't allow seniority filtering to prevent valid
    # questions from being selected.
    # --------------------------------------------------------

    seniority = (
        str(seniority).strip().lower()
        if seniority
        else "mid"
    )

    if seniority not in DIFFICULTY_BY_SENIORITY:

        seniority = "mid"

    # --------------------------------------------------------
    # Randomize skill order.
    #
    # Distribution remains balanced, but the extra question
    # in cases such as 10 / 3 skills is randomized.
    # --------------------------------------------------------

    random.shuffle(skills)

    # --------------------------------------------------------
    # STEP 1:
    # Check question availability per claimed skill.
    # --------------------------------------------------------

    available = {}

    for skill in skills:

        available[skill] = _get_skill_question_count(
            skill
        )

    skills_with_questions = [
        skill
        for skill in skills
        if available.get(skill, 0) > 0
    ]

    if not skills_with_questions:

        logger.warning(
            "No usable questions found for claimed skills: %s",
            skills,
        )

        return []

    # --------------------------------------------------------
    # STEP 2:
    # Determine target count.
    #
    # Normal Round 1 target is 10.
    #
    # If the bank has fewer than 10 usable questions across
    # the claimed skills, return whatever is actually available.
    # --------------------------------------------------------

    total_available = sum(
        available[skill]
        for skill in skills_with_questions
    )

    target = min(
        MIN_QUESTIONS,
        total_available,
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # If enough questions exist, target exactly 10.
    #
    # The old implementation could get 3 per skill and then
    # randomly top up from unrelated skills.
    #
    # That is removed.
    #
    # We NEVER pull unrelated skills just to reach 10.
    # --------------------------------------------------------

    if total_available >= MIN_QUESTIONS:

        target = MIN_QUESTIONS

    # --------------------------------------------------------
    # STEP 3:
    # Balanced distribution.
    # --------------------------------------------------------

    distribution = _calculate_skill_distribution(
        skills_with_questions,
        target,
        available,
    )

    # --------------------------------------------------------
    # STEP 4:
    # Select questions for each claimed skill.
    # --------------------------------------------------------

    selected = []
    selected_ids = []

    for skill in skills_with_questions:

        count = distribution.get(
            skill,
            0,
        )

        if count <= 0:
            continue

        rows = _select_questions_for_skill(
            skill=skill,
            count=count,
            exclude_ids=selected_ids,
        )

        for row in rows:

            question_id = row["id"]

            if question_id in selected_ids:
                continue

            selected.append(row)
            selected_ids.append(question_id)

    # --------------------------------------------------------
    # STEP 5:
    # If something couldn't be fetched despite availability,
    # redistribute missing slots ONLY among the candidate's
    # claimed skills.
    # --------------------------------------------------------

    missing = target - len(selected)

    if missing > 0:

        logger.warning(
            "Round 1 initially selected %d/%d questions for skills %s. "
            "Attempting skill-only top-up.",
            len(selected),
            target,
            skills_with_questions,
        )

        remaining_skills = list(skills_with_questions)
        random.shuffle(remaining_skills)

        for skill in remaining_skills:

            if missing <= 0:
                break

            rows = _fetch_questions_for_skill(
                skill=skill,
                limit=missing,
                exclude_ids=selected_ids,
            )

            for row in rows:

                if row["id"] in selected_ids:
                    continue

                selected.append(row)
                selected_ids.append(row["id"])
                missing -= 1

                if missing <= 0:
                    break

    # --------------------------------------------------------
    # STEP 6:
    # Final shuffle.
    # --------------------------------------------------------

    random.shuffle(selected)

    # --------------------------------------------------------
    # STEP 7:
    # Hard safety cap.
    # --------------------------------------------------------

    return selected[:MAX_QUESTIONS]


# ============================================================
# BONUS QUESTIONS
# ============================================================

def select_bonus_questions(
    skills,
    seniority,
    exclude_ids,
    count,
):
    """
    Dynamic extension for candidates who land in the
    borderline band after Round 1.

    Important:
        Bonus questions also come ONLY from candidate-claimed
        skills.

    No generic/unrelated questions are introduced.
    """

    if count <= 0:
        return []

    skills = _clean_skills(skills)

    if not skills:
        return []

    seniority = (
        str(seniority).strip().lower()
        if seniority
        else "mid"
    )

    if seniority not in DIFFICULTY_BY_SENIORITY:

        seniority = "mid"

    exclude_ids = list(exclude_ids or [])

    random.shuffle(skills)

    # --------------------------------------------------------
    # Find availability.
    # --------------------------------------------------------

    available = {}

    for skill in skills:

        available[skill] = _get_skill_question_count(
            skill
        )

    skills_with_questions = [
        skill
        for skill in skills
        if available.get(skill, 0) > 0
    ]

    if not skills_with_questions:
        return []

    # --------------------------------------------------------
    # Don't request more than actually exists.
    # --------------------------------------------------------

    total_available = sum(
        available[skill]
        for skill in skills_with_questions
    )

    target = min(
        count,
        total_available,
    )

    # --------------------------------------------------------
    # Balanced distribution.
    # --------------------------------------------------------

    distribution = _calculate_skill_distribution(
        skills_with_questions,
        target,
        available,
    )

    picked = []
    picked_ids = list(exclude_ids)

    # --------------------------------------------------------
    # Select bonus questions.
    # --------------------------------------------------------

    for skill in skills_with_questions:

        skill_count = distribution.get(
            skill,
            0,
        )

        if skill_count <= 0:
            continue

        rows = _select_questions_for_skill(
            skill=skill,
            count=skill_count,
            exclude_ids=picked_ids,
        )

        for row in rows:

            question_id = row["id"]

            if question_id in picked_ids:
                continue

            picked.append(row)
            picked_ids.append(question_id)

            if len(picked) >= target:
                break

        if len(picked) >= target:
            break

    # --------------------------------------------------------
    # Final fallback:
    # Still only candidate-claimed skills.
    # --------------------------------------------------------

    if len(picked) < target:

        remaining = target - len(picked)

        for skill in skills_with_questions:

            if remaining <= 0:
                break

            rows = _fetch_questions_for_skill(
                skill=skill,
                limit=remaining,
                exclude_ids=picked_ids,
            )

            for row in rows:

                if row["id"] in picked_ids:
                    continue

                picked.append(row)
                picked_ids.append(row["id"])
                remaining -= 1

                if remaining <= 0:
                    break

    random.shuffle(picked)

    return picked[:count]