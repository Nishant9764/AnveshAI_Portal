"""
integrity.py
────────────
Anti-cheat scoring for the Round 1 test session.

Three signal streams feed in:
  1. Discrete events from the browser (tab_switch, fullscreen_exit,
     copy, paste, right_click, devtools_open) — logged via
     log_event(). Three "hard" warnings (tab_switch / fullscreen_exit)
     auto-finishes the test.
  2. A hard 5-second-out-of-screen rule: the moment the candidate exits
     fullscreen or switches tabs, the browser starts a visible 5-second
     countdown. If they haven't returned by the time it hits zero, the
     browser reports 'screen_exit_timeout' and the test ends immediately
     — no strikes involved, this one is instant.
  3. Keystroke dynamics per answer — flight time (ms between keydown
     events) and dwell time (ms a key is held) — compared against the
     candidate's own calibration baseline captured on the pre-flight
     screen. A subjective answer that appears near-instantly, or whose
     rhythm is statistically flat/robotic compared to the baseline, is
     flagged as likely pasted/automated.

Nothing here ever silently fails a candidate — it only lowers
integrity_score and adds flags. The employer sees the flags and makes
the call; a low integrity score does not by itself change the ATS or
round scores. The one exception is screen_exit_timeout, which is an
immediate, unconditional termination by design (the candidate is shown
a clear 5-second countdown before it happens, so it's never a surprise).
"""

import statistics

import db

HARD_EVENTS = {"tab_switch", "fullscreen_exit"}
INSTANT_TERMINATE_EVENTS = {"screen_exit_timeout"}
MAX_WARNINGS = 3

EVENT_PENALTY = {
    "tab_switch": 15,
    "fullscreen_exit": 15,
    "copy": 10,
    "paste": 20,
    "right_click": 5,
    "devtools_open": 20,
    "screen_exit_timeout": 100,
    "shortcut_blocked": 5,
}


def log_event(session_id, event_type, detail=None):
    db.execute(
        "INSERT INTO integrity_events (session_id, event_type, detail) VALUES (%s,%s,%s)",
        (session_id, event_type, detail),
    )

    warnings_count = None
    if event_type in HARD_EVENTS:
        row = db.query_one(
            "UPDATE sessions SET warnings_count = warnings_count + 1 WHERE id=%s RETURNING warnings_count",
            (session_id,),
        )
        warnings_count = row["warnings_count"] if row else None

    penalty = EVENT_PENALTY.get(event_type, 5)
    db.execute(
        "UPDATE sessions SET integrity_score = GREATEST(0, integrity_score - %s) WHERE id=%s",
        (penalty, session_id),
    )

    should_terminate = event_type in INSTANT_TERMINATE_EVENTS or (
        warnings_count is not None and warnings_count >= MAX_WARNINGS
    )

    return {
        "warnings_count": warnings_count,
        "should_terminate": should_terminate,
        "reason": "screen_exit_timeout" if event_type in INSTANT_TERMINATE_EVENTS else (
            "warnings" if should_terminate else None
        ),
    }


def save_keystroke_baseline(session_id, baseline_metrics):
    """baseline_metrics: {"flight_times": [...ms], "dwell_times": [...ms], "wpm": float}
    captured from the pre-flight typing-calibration sentence."""
    import json
    db.execute(
        "UPDATE sessions SET keystroke_baseline=%s WHERE id=%s",
        (json.dumps(baseline_metrics), session_id),
    )


def _mean(vals):
    return statistics.mean(vals) if vals else 0


def _stdev(vals):
    return statistics.pstdev(vals) if len(vals) > 1 else 0


def analyze_answer_keystrokes(session_id, baseline, answer_metrics, answer_text, time_taken_seconds):
    """
    baseline: {"flight_times": [...], "dwell_times": [...], "wpm": float}
    answer_metrics: same shape, captured while answering this question.

    Returns (paste_detected: bool, flags: list[str]).
    Flags a paste/automation event when:
      - a long answer appears with almost no keystroke events (native
        paste doesn't fire per-character keydown events), OR
      - typing speed is implausibly fast for the answer length given
        time_taken_seconds, OR
      - keystroke rhythm variance collapses to near-zero (robotic,
        perfectly even bursts) compared to the candidate's own baseline.
    """
    flags = []
    answer_len = len(answer_text or "")
    flight_times = answer_metrics.get("flight_times", []) if answer_metrics else []
    dwell_times = answer_metrics.get("dwell_times", []) if answer_metrics else []

    keydown_chars = len(flight_times) + 1 if flight_times else 0
    if answer_len >= 40 and keydown_chars < answer_len * 0.3:
        flags.append("Large amount of text appeared with very few keystroke events (likely pasted).")

    if answer_len > 0 and time_taken_seconds and time_taken_seconds > 0:
        effective_wpm = (answer_len / 5) / (time_taken_seconds / 60)
        if effective_wpm > 180:
            flags.append(f"Implausible typing speed for this answer (~{int(effective_wpm)} WPM).")

    if baseline and baseline.get("flight_times") and flight_times:
        baseline_std = _stdev(baseline["flight_times"])
        answer_std = _stdev(flight_times)
        baseline_mean = _mean(baseline["flight_times"])
        answer_mean = _mean(flight_times)
        if baseline_std > 5 and answer_std < baseline_std * 0.15 and len(flight_times) >= 10:
            flags.append("Keystroke rhythm is unnaturally uniform compared to calibration baseline (possible auto-typing).")
        if baseline_mean > 0 and answer_mean < baseline_mean * 0.2 and len(flight_times) >= 10:
            flags.append("Typing cadence far faster than the candidate's own calibrated baseline.")

    paste_detected = len(flags) > 0
    if paste_detected:
        log_event(session_id, "paste_suspected", "; ".join(flags))
    return paste_detected, flags


def get_integrity_report(session_id):
    session = db.query_one(
        "SELECT integrity_score, warnings_count, integrity_flags FROM sessions WHERE id=%s",
        (session_id,),
    )
    events = db.query_all(
        "SELECT event_type, detail, created_at FROM integrity_events WHERE session_id=%s ORDER BY created_at",
        (session_id,),
    )
    if not session:
        return None
    score = session["integrity_score"]
    if score >= 90:
        label = "Clean"
    elif score >= 65:
        label = "Minor flags"
    else:
        label = "Flagged - anomalous activity detected"
    return {
        "integrity_score": score,
        "warnings_count": session["warnings_count"],
        "label": label,
        "events": events,
    }