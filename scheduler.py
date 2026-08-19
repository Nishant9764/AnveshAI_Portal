"""
scheduler.py
────────────
APScheduler running inside the Flask process (no external infra).
Handles the employer's "12h" / "24h" test-invite trigger modes:
on apply, if the ATS score clears the baseline but the employer chose a
delayed trigger, the invite email is queued with invite_scheduled_at
set in the future. This job wakes up periodically and sends anything
that's now due — unless the employer manually rejected the candidate
in the meantime, in which case it's skipped.

'immediate' and 'manual' modes never touch this scheduler:
  - immediate  -> email sent synchronously right in the apply route.
  - manual     -> only sent when the employer clicks "Send Invite".
"""

import logging
import uuid
from apscheduler.schedulers.background import BackgroundScheduler

import db
import mailer

logger = logging.getLogger("scheduler")
_scheduler = None
_app_ref = None


def process_due_invites():
    """Runs every CHECK_INTERVAL. Finds applications whose delayed
    invite is due and not manually rejected/withdrawn, sends the email,
    flips invite_status -> 'sent'."""
    due = db.query_all(
        """SELECT a.id AS application_id, a.round1_session_id, a.status,
                  u.full_name, u.email, j.title AS job_title, j.company_name
           FROM applications a
           JOIN users u ON u.id = a.candidate_id
           JOIN jobs j ON j.id = a.job_id
           WHERE a.invite_status = 'scheduled'
             AND a.invite_scheduled_at IS NOT NULL
             AND a.invite_scheduled_at <= NOW()
             AND a.status != 'Rejected'"""
    )
    for row in due:
        session = db.query_one(
            "SELECT invite_token FROM sessions WHERE id=%s", (row["round1_session_id"],)
        )
        if not session or not session.get("invite_token"):
            logger.error("No invite token for application %s — skipping", row["application_id"])
            continue
        sent = mailer.send_test_invite_email(
            candidate_email=row["email"], candidate_name=row["full_name"],
            job_title=row["job_title"], company_name=row["company_name"],
            invite_token=session["invite_token"], application_id=row["application_id"],
        )
        if sent:
            db.execute(
                "UPDATE applications SET invite_status='sent', invite_sent_at=NOW() WHERE id=%s",
                (row["application_id"],),
            )
            logger.info("Sent delayed test invite for application %s", row["application_id"])


def recover_stale_screenings(stale_after_minutes=5):
    """Safety net: if an application has been 'processing' for longer
    than a screening call should ever realistically take, the background
    job almost certainly crashed somewhere without hitting our own
    except-block (e.g. the process itself got killed mid-job). Recover it
    to 'failed' with a clear reason rather than leaving the candidate and
    employer staring at 'Scoring…' forever with no way to know why."""
    rows = db.query_all(
        """UPDATE applications
           SET screening_status='failed',
               screening_error='Screening timed out — please contact support or reapply.'
           WHERE screening_status='processing'
             AND applied_at < NOW() - (INTERVAL '1 minute' * %s)
           RETURNING id""",
        (int(stale_after_minutes),),
    )
    if rows:
        logger.info("Recovered %d stale 'processing' application(s)", len(rows))


def init_scheduler(app, check_interval_minutes=5):
    global _scheduler, _app_ref
    _app_ref = app
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)

    def _job():
        with app.app_context():
            try:
                process_due_invites()
            except Exception as e:
                logger.error("process_due_invites failed: %s", e)

    def _recovery_job():
        with app.app_context():
            try:
                recover_stale_screenings()
            except Exception as e:
                logger.error("recover_stale_screenings failed: %s", e)

    _scheduler.add_job(_job, "interval", minutes=check_interval_minutes, id="due_invites")
    _scheduler.add_job(_recovery_job, "interval", minutes=2, id="recover_stale_screenings")
    _scheduler.start()
    logger.info("APScheduler started (checking due invites every %s min)", check_interval_minutes)
    return _scheduler


def run_in_background(func, *args, **kwargs):
    """
    Fire-and-forget: runs `func(*args, **kwargs)` inside an app context,
    on the scheduler's own thread pool, essentially immediately — NOT on
    the Flask request thread. This is what lets `apply_to_job` return an
    instant "Application submitted!" response to the candidate while the
    actual Gemini scoring (the slow part — a real network round-trip)
    happens after the response has already gone out.

    Uses APScheduler (already running for the 12h/24h invite triggers)
    rather than spinning up a separate thread-management system.
    """
    if _scheduler is None or _app_ref is None:
        raise RuntimeError("Scheduler not initialized — call init_scheduler(app) first.")

    def _wrapped():
        with _app_ref.app_context():
            try:
                func(*args, **kwargs)
            except Exception as e:
                logger.error("Background job %s failed: %s", getattr(func, "__name__", func), e)

    job_id = f"bg-{uuid.uuid4().hex[:12]}"
    _scheduler.add_job(_wrapped, "date", id=job_id)
    return job_id