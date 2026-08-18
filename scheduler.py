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
from apscheduler.schedulers.background import BackgroundScheduler

import db
import mailer

logger = logging.getLogger("scheduler")
_scheduler = None


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


def init_scheduler(app, check_interval_minutes=5):
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)

    def _job():
        with app.app_context():
            try:
                process_due_invites()
            except Exception as e:
                logger.error("process_due_invites failed: %s", e)

    _scheduler.add_job(_job, "interval", minutes=check_interval_minutes, id="due_invites")
    _scheduler.start()
    logger.info("APScheduler started (checking due invites every %s min)", check_interval_minutes)
    return _scheduler
