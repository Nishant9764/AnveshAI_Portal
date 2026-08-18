# What changed in this build

## 1. Setup

```bash
pip install -r requirements.txt --break-system-packages
cp .env.example .env   # fill in real values
psql -U <user> -d <db> -f schema.sql        # if starting fresh
psql -U <user> -d <db> -f migration_v2.sql  # adds everything new — safe to
                                             # re-run, all statements are
                                             # idempotent (IF NOT EXISTS / ADD
                                             # COLUMN IF NOT EXISTS)
python seed_questions.py                    # starter MCQ bank (~20 questions
                                             # across Python/FastAPI/React/SQL/
                                             # AWS/Docker/MongoDB/JS). Add your
                                             # real bank the same way for
                                             # production use.
python app.py
```

SMTP credentials are required for rejection/invite emails to actually send —
without them, sends are logged to `email_log` as `skipped_no_config` and the
app keeps working (nothing crashes), you just won't see real emails.

## 2. New candidate flow
- Feed on Dashboard + Jobs page is now ranked by overlap between the job's
  tech stack and the candidate's profile skills + interests (new `interests`
  field on the profile page). "Matches my skills/interests only" filter on
  the Jobs page.
- Apply flow: pick an existing resume or upload a new one. No cover letter.
  On submit, the resume is parsed + PII-redacted (existing `resume_parser.py`,
  untouched), bias-stripped (college/grad year/company names removed), and
  scored against the JD in one Gemini call — instantly, synchronously, in the
  apply request.

## 3. Real ATS scoring (`ats_engine.py`)
Four sub-scores instead of one guess:
- **Technical match** — hard skills vs. JD required/preferred
- **Experience match** — seniority/years vs. what the JD implies
- **Soft skills** — evidence-based (concrete phrases like "led a team of 4"),
  not just keyword presence
- **Impact score** — the "impact vs. duty" analysis you asked for: rewards
  quantified outcomes ("increased sales 20%") over duty statements
  ("responsible for sales")

Blended into one `ats_score` in plain Python (weights in `ats_engine.WEIGHTS`),
compared against the employer's `min_match_score` (set on the job posting
form). Below it → instant polite rejection email. At/above it → Round 1
pipeline kicks off per the employer's trigger setting (Immediate / 12h / 24h /
Manual — dropdown on the job form).

## 4. Round 1 — Skills Assessment (`round1_engine.py`, `question_bank.py`)
- **Part A**: 10–15 MCQs pulled from the new `questions` table (schema
  matches exactly what you gave me), targeted to the skills on the resume,
  difficulty calibrated to how senior the resume reads (the "honeypot" — a
  resume that reads senior but can't answer harder questions is exactly the
  signal this catches). ≥70% → straight to Part B. <40% → instant reject +
  graceful exit screen. In between → 5 bonus questions before a final call.
- **Part B**: 2–3 Gemini-generated questions grounded in the candidate's own
  parsed projects — can't be answered generically.
- Round 1 result is a weighted blend (55% Part A / 45% Part B); below 60 →
  auto-reject + rejection email.

## 5. Rounds 2 & 3 — employer-unlocked, never automatic
- **Round 2** (`round2_engine.py`): Gemini questions from projects +
  experience — the "how did you actually handle this" round.
- **Round 3** (`round3_engine.py`): Gemini questions grounded in the specific
  JD text.
- Both only unlock when the employer clicks the button on the Applicants
  page — the system never advances a candidate into these automatically.

## 6. Anti-cheat (`integrity.py`, `static/js/anticheat.js`)
- Full-screen enforcement, tab-switch detection, copy/paste blocking — 3
  warnings, 4th ends the test and routes to the graceful-exit screen.
- Keystroke dynamics: a calibration sentence on the pre-flight screen
  captures the candidate's own flight-time/dwell-time baseline; every
  subjective answer is compared against it. Large blocks of text with almost
  no keystroke events, implausible WPM, or unnaturally flat rhythm vs.
  baseline → flagged as likely pasted, logged, shown to the employer as an
  integrity report (never silently fails the candidate — the employer
  decides).
- Desktop-only: the email link checks the User-Agent; mobile devices get a
  "please open on desktop" screen instead of the test.
- Candidate never sees their exact score at the end — a generic positive
  completion screen names a couple of strong topics and says their profile
  was sent to the employer.

## 7. Automation (`mailer.py`, `scheduler.py`)
- SMTP rejection + invite emails, logged to `email_log` regardless of
  success/failure.
- APScheduler runs inside the Flask process, checking every 5 min (config-
  urable) for `12h`/`24h` delayed invites that are now due — skips anyone the
  employer manually rejected in the meantime.

## 8. Bugs fixed along the way
- `SECRET_KEY` config was reading the wrong env var name (`.env` had
  `FLASK_SECRET_KEY`) — was silently using the dev default every run.
- `requirements.txt` was missing `google-genai`, `PyMuPDF`, and `PyPDF2`
  despite being imported — fresh installs would have crashed.
- Old assessment routes (`/assessment/<id>/basic|medium|advanced`) redirected
  to a `home` endpoint that didn't exist — would 500 on any failure path.
  These routes (and their orphaned templates) are removed entirely, replaced
  by the new Round 1/2/3 system.

## 9. Notable design decision
Question-bank/subjective-question state for an in-progress test lives in a
new `test_state` table, **not** the Flask cookie session — a 10–15 question
MCQ bank (or Gemini-generated subjective questions with rubrics) comfortably
exceeds the ~4KB signed-cookie limit, and cookie state also breaks across
tabs/devices. This is more robust than it might look at first glance.

## Known limits / next steps
- `seed_questions.py` ships ~20 demo questions. For real use, bulk-import
  your actual question bank into the `questions` table (same columns).
- The crude devtools-open heuristic in `anticheat.js` (window-size delta)
  is a soft signal, not foolproof — flagged, not blocking.
- Round 2/3 "unlock" is currently a same-invite-link flow (candidate returns
  to `/test/<session_id>/round2` — you may want a fresh emailed link per
  round; the routes are ready for that, just add a mailer call + a new
  `send_round2_invite` button if you want it explicit).
