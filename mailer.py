"""
mailer.py
─────────
SMTP email sending for the two automated candidate emails:
  - instant polite rejection (ATS score below the employer's baseline)
  - test invite link (ATS passed, sent per the employer's trigger timing)

Reads SMTP_* from environment (see .env). Every send is logged to
`email_log` regardless of success/failure, so a broken SMTP config
never silently loses track of who should have been emailed.
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import db

logger = logging.getLogger("mailer")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5001")


def _send(to_address, subject, html_body, application_id=None, email_type="generic"):
    if not SMTP_HOST or not SMTP_USER:
        logger.warning("SMTP not configured — skipping send of '%s' to %s", subject, to_address)
        db.execute(
            """INSERT INTO email_log (application_id, email_type, to_address, status, error)
               VALUES (%s,%s,%s,%s,%s)""",
            (application_id, email_type, to_address, "skipped_no_config", "SMTP_HOST/SMTP_USER not set"),
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_address
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_address], msg.as_string())
        db.execute(
            """INSERT INTO email_log (application_id, email_type, to_address, status)
               VALUES (%s,%s,%s,%s)""",
            (application_id, email_type, to_address, "sent"),
        )
        return True
    except Exception as e:
        logger.error("Email send failed to %s: %s", to_address, e)
        db.execute(
            """INSERT INTO email_log (application_id, email_type, to_address, status, error)
               VALUES (%s,%s,%s,%s,%s)""",
            (application_id, email_type, to_address, "failed", str(e)),
        )
        return False


def send_rejection_email(candidate_email, candidate_name, job_title, company_name, application_id=None):
    subject = f"Update on your application — {job_title} at {company_name}"
    body = f"""
    <p>Hi {candidate_name.split()[0] if candidate_name else 'there'},</p>
    <p>Thank you for applying to the <strong>{job_title}</strong> role at
    <strong>{company_name}</strong>, and for taking the time to submit your resume.</p>
    <p>After review, we won't be moving forward with your application for this
    particular role. This isn't a reflection of your overall abilities — roles
    often come down to a specific match with what a team needs right now.</p>
    <p>We'd genuinely encourage you to apply to other openings that fit your
    background. Thanks again for your interest, and we wish you the best in
    your search.</p>
    <p>— The {company_name} Hiring Team</p>
    """
    return _send(candidate_email, subject, body, application_id, "rejection")


def send_test_invite_email(candidate_email, candidate_name, job_title, company_name, invite_token, application_id=None):
    link = f"{APP_BASE_URL}/test/start/{invite_token}"
    subject = f"You're invited: Round 1 Skills Assessment — {job_title} at {company_name}"
    body = f"""
    <p>Hi {candidate_name.split()[0] if candidate_name else 'there'},</p>
    <p>Good news — your application for <strong>{job_title}</strong> at
    <strong>{company_name}</strong> has moved forward. The next step is a short
    Round 1 skills assessment.</p>
    <p><a href="{link}" style="background:#4f46e5;color:#fff;padding:10px 18px;
    border-radius:6px;text-decoration:none;display:inline-block;">Start Round 1 Assessment</a></p>
    <p><strong>Please note:</strong> this assessment requires a desktop or
    laptop with a physical keyboard, in full-screen mode. It won't work on a
    mobile device. Set aside about 25-30 minutes, uninterrupted.</p>
    <p>This link is unique to you — please don't share it.</p>
    <p>— The {company_name} Hiring Team</p>
    """
    return _send(candidate_email, subject, body, application_id, "test_invite")
