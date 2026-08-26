"""
mailer.py
─────────
Premium SMTP email service for the AI Recruitment & Talent Analytics System.

Automated emails:
  • ATS rejection
  • Round 1 assessment invitation
  • Shortlist notification
  • Interview notification
  • Offer notification

Features:
  • Premium responsive HTML email templates
  • Plain-text fallback
  • Personalized candidate messaging
  • Consistent company branding
  • Assessment CTA button
  • Application information cards
  • Email delivery logging
  • SMTP failure tracking
  • HTML escaping for user-provided values
"""

import os
import smtplib
import logging
from html import escape
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import db


logger = logging.getLogger("mailer")


# ─────────────────────────────────────────────────────────────
# SMTP CONFIGURATION
# ─────────────────────────────────────────────────────────────

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

SMTP_USE_TLS = (
    os.environ.get("SMTP_USE_TLS", "true").lower() != "false"
)

APP_BASE_URL = os.environ.get(
    "APP_BASE_URL",
    "http://localhost:5001"
)

# Optional branding variables
APP_NAME = os.environ.get(
    "APP_NAME",
    "AI Recruitment & Talent Analytics"
)

BRAND_PRIMARY = "#4F46E5"
BRAND_DARK = "#111827"
BRAND_MUTED = "#6B7280"
BRAND_LIGHT = "#F5F7FF"
BORDER_COLOR = "#E5E7EB"


# ─────────────────────────────────────────────────────────────
# COMMON HTML HELPERS
# ─────────────────────────────────────────────────────────────

def _safe(value):
    """Safely escape user-provided values before inserting into HTML."""
    return escape(str(value or ""))


def _first_name(candidate_name):
    """Return a clean first name for personalization."""
    if not candidate_name:
        return "there"

    return _safe(candidate_name.strip().split()[0])


def _email_layout(company_name, preheader, content):
    """
    Premium responsive email wrapper used by every email.
    """

    company = _safe(company_name)
    app_name = _safe(APP_NAME)
    preheader = _safe(preheader)

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1.0">

<meta name="color-scheme" content="light">

<title>{company}</title>

<style>
    body {{
        margin: 0;
        padding: 0;
        background: #f3f4f6;
        font-family:
            -apple-system,
            BlinkMacSystemFont,
            "Segoe UI",
            Roboto,
            Helvetica,
            Arial,
            sans-serif;
        color: #111827;
    }}

    .wrapper {{
        width: 100%;
        padding: 40px 16px;
        box-sizing: border-box;
    }}

    .container {{
        max-width: 640px;
        margin: 0 auto;
        background: #ffffff;
        border-radius: 18px;
        overflow: hidden;
        box-shadow:
            0 10px 35px rgba(17, 24, 39, 0.08);
    }}

    .header {{
        background: linear-gradient(
            135deg,
            #4f46e5 0%,
            #6366f1 50%,
            #7c3aed 100%
        );
        padding: 30px 36px;
        color: #ffffff;
    }}

    .brand {{
        font-size: 18px;
        font-weight: 700;
        letter-spacing: -0.2px;
    }}

    .brand-subtitle {{
        margin-top: 5px;
        font-size: 12px;
        opacity: 0.82;
    }}

    .content {{
        padding: 42px 40px;
    }}

    .eyebrow {{
        color: #4f46e5;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 1.1px;
        text-transform: uppercase;
        margin-bottom: 12px;
    }}

    h1 {{
        margin: 0 0 16px 0;
        color: #111827;
        font-size: 28px;
        line-height: 1.25;
        letter-spacing: -0.6px;
    }}

    p {{
        margin: 0 0 18px 0;
        color: #4b5563;
        font-size: 15px;
        line-height: 1.75;
    }}

    strong {{
        color: #111827;
    }}

    .info-card {{
        margin: 26px 0;
        padding: 20px;
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
    }}

    .info-row {{
        margin-bottom: 12px;
    }}

    .info-row:last-child {{
        margin-bottom: 0;
    }}

    .label {{
        display: block;
        margin-bottom: 3px;
        color: #9ca3af;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.7px;
        text-transform: uppercase;
    }}

    .value {{
        color: #111827;
        font-size: 14px;
        font-weight: 600;
    }}

    .highlight {{
        margin: 26px 0;
        padding: 20px;
        background: #eef2ff;
        border-left: 4px solid #4f46e5;
        border-radius: 8px;
    }}

    .highlight p {{
        margin: 0;
        color: #3730a3;
        font-size: 14px;
    }}

    .success-card {{
        margin: 26px 0;
        padding: 20px;
        background: #ecfdf5;
        border: 1px solid #a7f3d0;
        border-radius: 10px;
    }}

    .success-card p {{
        margin: 0;
        color: #065f46;
        font-size: 14px;
    }}

    .neutral-card {{
        margin: 26px 0;
        padding: 20px;
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
    }}

    .neutral-card p {{
        margin: 0;
        color: #4b5563;
        font-size: 14px;
    }}

    .button-wrapper {{
        text-align: center;
        margin: 32px 0;
    }}

    .button {{
        display: inline-block;
        padding: 14px 28px;
        background: #4f46e5;
        color: #ffffff !important;
        text-decoration: none;
        font-size: 15px;
        font-weight: 700;
        border-radius: 9px;
        box-shadow: 0 6px 16px rgba(79, 70, 229, 0.25);
    }}

    .small {{
        color: #9ca3af;
        font-size: 12px;
        line-height: 1.6;
    }}

    .divider {{
        height: 1px;
        background: #e5e7eb;
        margin: 30px 0;
    }}

    .footer {{
        padding: 26px 36px;
        background: #f9fafb;
        border-top: 1px solid #e5e7eb;
        text-align: center;
    }}

    .footer-company {{
        color: #374151;
        font-size: 13px;
        font-weight: 700;
    }}

    .footer-text {{
        margin-top: 6px;
        color: #9ca3af;
        font-size: 11px;
        line-height: 1.6;
    }}

    @media only screen and (max-width: 600px) {{
        .wrapper {{
            padding: 15px 8px;
        }}

        .header {{
            padding: 24px;
        }}

        .content {{
            padding: 30px 22px;
        }}

        h1 {{
            font-size: 24px;
        }}

        .button {{
            display: block;
        }}

        .footer {{
            padding: 22px;
        }}
    }}
</style>

</head>

<body>

<!-- Hidden preview text -->
<div style="
    display:none;
    max-height:0;
    overflow:hidden;
    opacity:0;
    color:transparent;
">
    {preheader}
</div>

<div class="wrapper">

    <div class="container">

        <div class="header">
            <div class="brand">
                {company}
            </div>

            <div class="brand-subtitle">
                {app_name}
            </div>
        </div>

        <div class="content">

            {content}

        </div>

        <div class="footer">

            <div class="footer-company">
                {company} Hiring Team
            </div>

            <div class="footer-text">
                This is an automated recruitment communication.
                Please do not reply directly to this email unless
                instructed otherwise.
            </div>

        </div>

    </div>

</div>

</body>
</html>
"""


# ─────────────────────────────────────────────────────────────
# SEND EMAIL
# ─────────────────────────────────────────────────────────────

def _send(
    to_address,
    subject,
    html_body,
    text_body,
    application_id=None,
    email_type="generic"
):
    """
    Sends an email and records the result in email_log.
    """

    if not SMTP_HOST or not SMTP_USER:

        logger.warning(
            "SMTP not configured — skipping '%s' to %s",
            subject,
            to_address
        )

        db.execute(
            """
            INSERT INTO email_log
            (application_id, email_type, to_address, status, error)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (
                application_id,
                email_type,
                to_address,
                "skipped_no_config",
                "SMTP_HOST/SMTP_USER not set"
            )
        )

        return False

    msg = MIMEMultipart("alternative")

    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_address

    # Plain text fallback
    msg.attach(
        MIMEText(text_body, "plain", "utf-8")
    )

    # Premium HTML version
    msg.attach(
        MIMEText(html_body, "html", "utf-8")
    )

    try:

        with smtplib.SMTP(
            SMTP_HOST,
            SMTP_PORT,
            timeout=15
        ) as server:

            if SMTP_USE_TLS:
                server.starttls()

            server.login(
                SMTP_USER,
                SMTP_PASSWORD
            )

            server.sendmail(
                SMTP_FROM,
                [to_address],
                msg.as_string()
            )

        db.execute(
            """
            INSERT INTO email_log
            (application_id, email_type, to_address, status)
            VALUES (%s,%s,%s,%s)
            """,
            (
                application_id,
                email_type,
                to_address,
                "sent"
            )
        )

        logger.info(
            "Email sent successfully to %s: %s",
            to_address,
            subject
        )

        return True

    except Exception as e:

        logger.error(
            "Email send failed to %s: %s",
            to_address,
            e
        )

        db.execute(
            """
            INSERT INTO email_log
            (application_id, email_type, to_address, status, error)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (
                application_id,
                email_type,
                to_address,
                "failed",
                str(e)
            )
        )

        return False


# ─────────────────────────────────────────────────────────────
# REJECTION EMAIL
# ─────────────────────────────────────────────────────────────

def send_rejection_email(
    candidate_email,
    candidate_name,
    job_title,
    company_name,
    application_id=None
):

    first_name = _first_name(candidate_name)
    job = _safe(job_title)
    company = _safe(company_name)

    subject = (
        f"An update on your application — "
        f"{job_title} at {company_name}"
    )

    html_content = f"""

    <div class="eyebrow">
        Application Update
    </div>

    <h1>
        Thank you for your interest, {first_name}.
    </h1>

    <p>
        We sincerely appreciate the time and effort you invested in
        applying for the <strong>{job}</strong> opportunity at
        <strong>{company}</strong>.
    </p>

    <p>
        After carefully reviewing your application against the
        requirements of this particular role, we've decided not to
        move forward with your application at this stage.
    </p>

    <div class="neutral-card">
        <p>
            <strong>A note from our hiring team</strong><br><br>
            Recruitment decisions are often about finding the closest
            match between a candidate's current experience and the
            specific needs of a role. This decision does not define
            your abilities, potential, or future opportunities.
        </p>
    </div>

    <p>
        We encourage you to keep an eye on future opportunities with
        <strong>{company}</strong>. A different role may be a much
        stronger match for your experience and aspirations.
    </p>

    <p>
        Thank you again for considering us as part of your career
        journey. We wish you every success in what comes next.
    </p>

    <div class="divider"></div>

    <p>
        Warm regards,<br>
        <strong>The {company} Hiring Team</strong>
    </p>

    """

    text_body = f"""
Hi {candidate_name or 'there'},

Thank you for applying for the {job_title} role at {company_name}.

After reviewing your application against the requirements of this
particular role, we've decided not to move forward with your
application at this stage.

This decision does not define your abilities or future potential.
Recruitment decisions often depend on the specific match between
a candidate's experience and the team's current needs.

We encourage you to keep an eye on future opportunities with
{company_name}.

Thank you again for your interest, and we wish you every success
in your career journey.

Warm regards,
The {company_name} Hiring Team
"""

    html_body = _email_layout(
        company_name,
        f"An update regarding your {job_title} application.",
        html_content
    )

    return _send(
        candidate_email,
        subject,
        html_body,
        text_body,
        application_id,
        "rejection"
    )


# ─────────────────────────────────────────────────────────────
# TEST INVITATION EMAIL
# ─────────────────────────────────────────────────────────────

def send_test_invite_email(
    candidate_email,
    candidate_name,
    job_title,
    company_name,
    invite_token,
    application_id=None
):

    first_name = _first_name(candidate_name)
    job = _safe(job_title)
    company = _safe(company_name)

    link = (
        f"{APP_BASE_URL}/test/start/{invite_token}"
    )

    subject = (
        f"You're moving forward 🎉 — "
        f"Round 1 Assessment for {job_title}"
    )

    html_content = f"""

    <div class="eyebrow">
        Next Step in Your Application
    </div>

    <h1>
        Great news, {first_name}! 🎉
    </h1>

    <p>
        Your application for the <strong>{job}</strong> role at
        <strong>{company}</strong> has successfully moved forward.
    </p>

    <p>
        We'd now like to invite you to complete the next step in
        our selection process: a short <strong>Round 1 Skills
        Assessment</strong>.
    </p>

    <div class="success-card">
        <p>
            <strong>You're through to the next stage.</strong><br><br>
            This assessment helps our hiring team understand how
            your skills and problem-solving approach align with the
            requirements of the role.
        </p>
    </div>

    <div class="info-card">

        <div class="info-row">
            <span class="label">Position</span>
            <span class="value">{job}</span>
        </div>

        <div class="info-row">
            <span class="label">Company</span>
            <span class="value">{company}</span>
        </div>

        <div class="info-row">
            <span class="label">Assessment</span>
            <span class="value">Round 1 Skills Assessment</span>
        </div>

        <div class="info-row">
            <span class="label">Estimated Time</span>
            <span class="value">25–30 minutes</span>
        </div>

    </div>

    <div class="button-wrapper">

        <a
            href="{link}"
            class="button"
            target="_blank"
            rel="noopener noreferrer"
        >
            Start Round 1 Assessment →
        </a>

    </div>

    <div class="highlight">

        <p>
            <strong>Before you begin</strong><br><br>

            • Use a desktop or laptop computer.<br>
            • Make sure you have a physical keyboard.<br>
            • Complete the assessment in full-screen mode.<br>
            • Choose a quiet place where you won't be interrupted.<br>
            • Set aside approximately 25–30 minutes.
        </p>

    </div>

    <p>
        Your assessment link is unique to you. Please do not share
        it with anyone else.
    </p>

    <p>
        Take your time, read each question carefully, and do your
        best. We look forward to seeing how you approach the
        challenge.
    </p>

    <div class="divider"></div>

    <p>
        Best of luck! 🚀<br>
        <strong>The {company} Hiring Team</strong>
    </p>

    """

    text_body = f"""
Hi {candidate_name or 'there'},

Great news!

Your application for the {job_title} role at {company_name}
has successfully moved forward.

The next step is a Round 1 Skills Assessment.

Assessment details:
Position: {job_title}
Company: {company_name}
Estimated time: 25–30 minutes

Start your assessment here:
{link}

Before you begin:

- Use a desktop or laptop computer.
- Make sure you have a physical keyboard.
- Complete the assessment in full-screen mode.
- Choose a quiet place where you won't be interrupted.
- Set aside approximately 25–30 minutes.

This assessment link is unique to you. Please do not share it.

Take your time, read each question carefully, and do your best.

Best of luck!

The {company_name} Hiring Team
"""

    html_body = _email_layout(
        company_name,
        f"Your application has moved forward. Start your Round 1 assessment.",
        html_content
    )

    return _send(
        candidate_email,
        subject,
        html_body,
        text_body,
        application_id,
        "test_invite"
    )


# ─────────────────────────────────────────────────────────────
# STATUS MESSAGES
# ─────────────────────────────────────────────────────────────

STATUS_MESSAGES = {

    "Shortlisted": {
        "headline": "You're on our shortlist! 🎉",
        "eyebrow": "Application Progress",
        "message": (
            "We're pleased to let you know that your application "
            "has been shortlisted. Your experience and profile have "
            "stood out during our review."
        ),
        "support": (
            "Our hiring team will review the shortlisted candidates "
            "and reach out with the next steps."
        ),
    },

    "Interview": {
        "headline": "You've reached the interview stage! 🎯",
        "eyebrow": "Interview Stage",
        "message": (
            "We're excited to let you know that your application "
            "has progressed to the interview stage."
        ),
        "support": (
            "The hiring team will contact you shortly with details "
            "about the interview, including the expected format and "
            "schedule."
        ),
    },

    "Offered": {
        "headline": "Congratulations — you've received an offer! 🎉",
        "eyebrow": "Offer Update",
        "message": (
            "We're delighted to let you know that the hiring team "
            "has selected you for an offer for this role."
        ),
        "support": (
            "Please check your inbox for the offer details and any "
            "additional information from the hiring team."
        ),
    },
}


# ─────────────────────────────────────────────────────────────
# STATUS UPDATE EMAIL
# ─────────────────────────────────────────────────────────────

def send_status_update_email(
    candidate_email,
    candidate_name,
    job_title,
    company_name,
    new_status,
    application_id=None
):
    """
    Sends a professional candidate notification when the employer
    changes the application status.

    Rejected is intentionally excluded because rejection has its
    own dedicated template.
    """

    if new_status not in STATUS_MESSAGES:
        return False

    first_name = _first_name(candidate_name)
    job = _safe(job_title)
    company = _safe(company_name)

    config = STATUS_MESSAGES[new_status]

    subject = (
        f"{config['headline']} — "
        f"{job_title} at {company_name}"
    )

    html_content = f"""

    <div class="eyebrow">
        {_safe(config['eyebrow'])}
    </div>

    <h1>
        {_safe(config['headline'])}
    </h1>

    <p>
        Hi <strong>{first_name}</strong>,
    </p>

    <p>
        {config['message']}
    </p>

    <div class="info-card">

        <div class="info-row">
            <span class="label">Role</span>
            <span class="value">{job}</span>
        </div>

        <div class="info-row">
            <span class="label">Company</span>
            <span class="value">{company}</span>
        </div>

        <div class="info-row">
            <span class="label">Current Stage</span>
            <span class="value">{_safe(new_status)}</span>
        </div>

    </div>

    <div class="success-card">
        <p>
            <strong>What's next?</strong><br><br>
            {config['support']}
        </p>
    </div>

    <p>
        Thank you for your continued interest in
        <strong>{company}</strong>. We appreciate the time and
        effort you've invested throughout the process.
    </p>

    <div class="divider"></div>

    <p>
        Warm regards,<br>
        <strong>The {company} Hiring Team</strong>
    </p>

    """

    text_body = f"""
Hi {candidate_name or 'there'},

{config['headline']}

{config['message']}

Application details:
Role: {job_title}
Company: {company_name}
Current Stage: {new_status}

What's next?

{config['support']}

Thank you for your continued interest in {company_name}.
We appreciate the time and effort you've invested throughout
the recruitment process.

Warm regards,
The {company_name} Hiring Team
"""

    html_body = _email_layout(
        company_name,
        f"{config['headline']} for your {job_title} application.",
        html_content
    )

    return _send(
        candidate_email,
        subject,
        html_body,
        text_body,
        application_id,
        f"status_{new_status.lower()}"
    )