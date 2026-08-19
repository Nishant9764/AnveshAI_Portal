import os
import json
import random
import logging
from functools import wraps
from datetime import datetime, timedelta

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, jsonify, abort, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from config import Config
import db

# AI Modules
import resume_parser
import gemini_client
import models

# ATS / testing pipeline
import ats_engine
import resume_score_engine
import resume_bank
import question_bank
import round1_engine
import round2_engine
import round3_engine
import integrity
import mailer
import scheduler as bg_scheduler

TRIGGER_HOURS = {"12h": 12, "24h": 24}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    db.init_app(app)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    register_routes(app)
    # Guard against Flask's dev-server reloader (debug=True) spawning this
    # twice: with the reloader on, the process is re-exec'd once with
    # WERKZEUG_RUN_MAIN unset (the outer watcher, never serves requests)
    # and once with it set to "true" (the real server). Without this
    # guard both processes start their own APScheduler, silently doubling
    # up periodic jobs. In production (no reloader, app.debug False) the
    # var is never set either way, so the scheduler still starts normally.
    if (not app.debug) or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        bg_scheduler.init_scheduler(app, app.config.get("SCHEDULER_CHECK_INTERVAL_MINUTES", 5))
    return app


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def login_required(role=None):
    """Decorator: require a logged-in user, optionally of a specific role."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                flash("You don't have access to that page.", "error")
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def initials_from_name(name):
    parts = name.strip().split()
    if not parts:
        return "U"
    if len(parts) == 1:
        return parts[0][0].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def allowed_file(filename, app):
    return "." in filename and \
        filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


def allowed_image_file(filename):
    return "." in filename and \
        filename.rsplit(".", 1)[1].lower() in {"png", "jpg", "jpeg", "webp", "svg"}


def _skill_overlap_rank(job_tech_stack, candidate_tech_stack):
    """Used only for feed ranking/personalization (not the real ATS
    score, which is computed by ats_engine.py at apply time). Cheap
    overlap count so the candidate feed can surface the most relevant
    jobs first."""
    if not job_tech_stack or not candidate_tech_stack:
        return 0
    job_set = {t.strip().lower() for t in job_tech_stack.split(",") if t.strip()}
    cand_set = {t.strip().lower() for t in candidate_tech_stack.split(",") if t.strip()}
    return len(job_set & cand_set)


def _schedule_invite_time(trigger_mode):
    hours = TRIGGER_HOURS.get(trigger_mode)
    return (datetime.now() + timedelta(hours=hours)) if hours else None


def time_ago(dt):
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    delta = datetime.now() - dt
    secs = delta.total_seconds()
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} hours ago"
    return f"{int(secs // 86400)} days ago"


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

def register_routes(app):

    app.jinja_env.filters["time_ago"] = time_ago

    @app.context_processor
    def inject_user():
        return {
            "current_user_name": session.get("full_name"),
            "current_user_role": session.get("role"),
            "current_user_initials": session.get("initials"),
        }

    # ---------------- Public marketing site ----------------

    @app.route("/")
    def index():
        return render_template("index.html")

    # ---------------- Auth ----------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "candidate")

            user = db.query_one(
                "SELECT * FROM users WHERE email = %s AND role = %s",
                (email, role),
            )

            if user and check_password_hash(user["password_hash"], password):
                session["user_id"] = user["id"]
                session["full_name"] = user["full_name"]
                session["role"] = user["role"]
                session["initials"] = user["avatar_initials"] or initials_from_name(user["full_name"])
                flash(f"Welcome back, {user['full_name'].split()[0]}!", "success")
                if user["role"] == "employer":
                    return redirect(url_for("employer_dashboard"))
                return redirect(url_for("candidate_dashboard"))

            flash("Invalid email, password, or account type.", "error")
            return redirect(url_for("login"))

        role = request.args.get("role", "candidate")
        return render_template("login.html", role=role)

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            role = request.form.get("role", "candidate")

            if not full_name or not email or not password:
                flash("Please fill in all fields.", "error")
                return redirect(url_for("signup", role=role))

            if password != confirm:
                flash("Passwords do not match.", "error")
                return redirect(url_for("signup", role=role))

            existing = db.query_one("SELECT id FROM users WHERE email = %s", (email,))
            if existing:
                flash("An account with that email already exists.", "error")
                return redirect(url_for("signup", role=role))

            password_hash = generate_password_hash(password)
            initials = initials_from_name(full_name)

            user_id = db.execute(
                """INSERT INTO users (full_name, email, password_hash, role, avatar_initials)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (full_name, email, password_hash, role, initials),
            )

            if role == "employer":
                db.execute(
                    "INSERT INTO company_profiles (user_id, company_name) VALUES (%s, %s)",
                    (user_id, full_name),
                )
            else:
                db.execute(
                    "INSERT INTO candidate_profiles (user_id) VALUES (%s)",
                    (user_id,),
                )

            session["user_id"] = user_id
            session["full_name"] = full_name
            session["role"] = role
            session["initials"] = initials

            flash("Account created! Welcome to SmartHire AI.", "success")
            if role == "employer":
                return redirect(url_for("employer_dashboard"))
            return redirect(url_for("candidate_dashboard"))

        role = request.args.get("role", "candidate")
        return render_template("signup.html", role=role)

    @app.route("/logout")
    def logout():
        session.clear()
        flash("You've been logged out.", "success")
        return redirect(url_for("index"))

    # ---------------- Candidate dashboard ----------------

    @app.route("/candidate/dashboard")
    @login_required(role="candidate")
    def candidate_dashboard():
        user_id = session["user_id"]

        profile = db.query_one(
            "SELECT * FROM candidate_profiles WHERE user_id = %s", (user_id,)
        )

        already_applied_ids = {
            row["job_id"] for row in db.query_all(
                "SELECT job_id FROM applications WHERE candidate_id = %s", (user_id,)
            )
        }
        candidate_stack = ((profile.get("tech_stack") if profile else "") or "") + "," + \
                           ((profile.get("interests") if profile else "") or "")

        all_active = db.query_all(
            """SELECT * FROM jobs WHERE status = 'Active'
               ORDER BY posted_at DESC LIMIT 60"""
        )
        # Personalize: rank by overlap with the candidate's skills/interests,
        # not just recency. Jobs already applied to sink to the end of the
        # feed (not hidden — the candidate can still see/revisit them) with
        # their "Apply" button swapped for an "Applied" state in the template.
        ranked = sorted(
            all_active,
            key=lambda j: (j["id"] in already_applied_ids, -_skill_overlap_rank(j["tech_stack"], candidate_stack)),
        )
        jobs = ranked[:6]

        saved_job_ids = {
            row["job_id"] for row in db.query_all(
                "SELECT job_id FROM saved_jobs WHERE candidate_id = %s", (user_id,)
            )
        }

        counts = db.query_one(
            """SELECT
                 COUNT(*) FILTER (WHERE status='Applied') AS applied,
                 COUNT(*) FILTER (WHERE status='Shortlisted') AS shortlisted,
                 COUNT(*) FILTER (WHERE status='Interview') AS interview,
                 COUNT(*) FILTER (WHERE status='Rejected') AS rejected
               FROM applications WHERE candidate_id = %s""",
            (user_id,),
        ) or {}

        stats = {
            "applied": counts.get("applied") or 0,
            "shortlisted": counts.get("shortlisted") or 0,
            "interview": counts.get("interview") or 0,
            "rejected": counts.get("rejected") or 0,
        }

        return render_template(
            "candidate_dashboard.html",
            profile=profile,
            jobs=jobs,
            saved_job_ids=saved_job_ids,
            applied_job_ids=already_applied_ids,
            stats=stats,
        )

    @app.route("/candidate/jobs")
    @login_required(role="candidate")
    def candidate_jobs():
        user_id = session["user_id"]
        q = request.args.get("q", "").strip()
        location = request.args.get("location", "").strip()
        match_only = request.args.get("match_only") == "1"

        sql = "SELECT * FROM jobs WHERE status = 'Active'"
        params = []
        if q:
            sql += " AND (title LIKE %s OR tech_stack LIKE %s)"
            params += [f"%{q}%", f"%{q}%"]
        if location:
            sql += " AND location LIKE %s"
            params.append(f"%{location}%")
        sql += " ORDER BY posted_at DESC"

        jobs = db.query_all(sql, params)

        applied_job_ids = {
            row["job_id"] for row in db.query_all(
                "SELECT job_id FROM applications WHERE candidate_id = %s", (user_id,)
            )
        }
        profile = db.query_one("SELECT * FROM candidate_profiles WHERE user_id = %s", (user_id,))
        candidate_stack = ((profile.get("tech_stack") if profile else "") or "") + "," + \
                           ((profile.get("interests") if profile else "") or "")
        for j in jobs:
            j["_overlap"] = _skill_overlap_rank(j["tech_stack"], candidate_stack)
        # Personalized ranking: best-matching jobs first (skills/interests
        # overlap); jobs already applied to sink to the end of the feed
        # regardless of match strength, rather than disappearing entirely.
        jobs.sort(key=lambda j: (j["id"] in applied_job_ids, -j["_overlap"]))
        if match_only:
            jobs = [j for j in jobs if j["_overlap"] > 0]

        saved_job_ids = {
            row["job_id"] for row in db.query_all(
                "SELECT job_id FROM saved_jobs WHERE candidate_id = %s", (user_id,)
            )
        }
        return render_template(
            "candidate_jobs.html", jobs=jobs, saved_job_ids=saved_job_ids,
            q=q, location=location, match_only=match_only,
        )

    @app.route("/candidate/jobs/<int:job_id>/apply", methods=["GET"])
    @login_required(role="candidate")
    def apply_to_job_page(job_id):
        user_id = session["user_id"]
        job = db.query_one("SELECT * FROM jobs WHERE id = %s AND status = 'Active'", (job_id,))
        if not job:
            flash("That job no longer exists.", "error")
            return redirect(url_for("candidate_jobs"))

        existing = db.query_one(
            "SELECT id FROM applications WHERE job_id=%s AND candidate_id=%s", (job_id, user_id)
        )
        if existing:
            flash("You've already applied to this job.", "error")
            return redirect(url_for("candidate_applications"))

        resumes = resume_bank.list_for_candidate(user_id)
        return render_template("apply_job.html", job=job, resumes=resumes)

    @app.route("/candidate/jobs/<int:job_id>/apply", methods=["POST"])
    @login_required(role="candidate")
    def apply_to_job(job_id):
        user_id = session["user_id"]
        job = db.query_one("SELECT * FROM jobs WHERE id = %s AND status = 'Active'", (job_id,))
        if not job:
            flash("That job no longer exists.", "error")
            return redirect(url_for("candidate_jobs"))

        existing = db.query_one(
            "SELECT id FROM applications WHERE job_id=%s AND candidate_id=%s", (job_id, user_id)
        )
        if existing:
            flash("You've already applied to this job.", "error")
            return redirect(url_for("candidate_applications"))

        resume_choice = request.form.get("resume_choice")  # "existing" or "upload"
        resume_id = None
        skills_for_session = []

        # Everything in this block is FAST (no Gemini call) — local
        # regex-based parsing only — so the candidate isn't kept waiting
        # for it. The actual JD-match scoring (a real network round-trip
        # to Gemini) happens in a background job below, after we've
        # already responded.
        try:
            if resume_choice == "existing":
                resume_id = int(request.form.get("resume_id", 0) or 0)
                resume_row = resume_bank.get(resume_id, candidate_id=user_id)
                if not resume_row:
                    flash("Please select a valid resume.", "error")
                    return redirect(url_for("apply_to_job_page", job_id=job_id))
                parsed = resume_bank.as_parsed_dict(resume_row)
                skills_for_session = parsed.get("skills", [])
            else:
                file = request.files.get("resume")
                if not file or not file.filename or not allowed_file(file.filename, app):
                    flash("Please upload a PDF, DOC, or DOCX resume.", "error")
                    return redirect(url_for("apply_to_job_page", job_id=job_id))
                filename = secure_filename(f"user{user_id}_{int(datetime.now().timestamp())}_{file.filename}")
                file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                file.save(file_path)

                with open(file_path, "rb") as f:
                    raw_text = resume_parser.extract_text_from_pdf(f)
                if not raw_text or not raw_text.strip():
                    flash("Couldn't read that resume file — please try another.", "error")
                    return redirect(url_for("apply_to_job_page", job_id=job_id))
                parsed_resume = resume_parser.process_resume(raw_text)
                resume_parsed_for_save = {
                    "masked_resume": parsed_resume["masked_resume"],
                    "skills": parsed_resume["skills"],
                    "experience": parsed_resume["experience"],
                    "projects": parsed_resume["projects"],
                    "name_found": parsed_resume["name_found"],
                    "raw_text": raw_text,
                }
                resume_id = resume_bank.save(user_id, filename, file_path, resume_parsed_for_save)
                skills_for_session = parsed_resume["skills"]
                # keep candidate_profiles.tech_stack fresh too, for feed personalization
                if skills_for_session:
                    db.execute(
                        "UPDATE candidate_profiles SET tech_stack=%s WHERE user_id=%s",
                        (", ".join(skills_for_session), user_id),
                    )
        except Exception as e:
            print(f"Resume parsing error: {e}")
            flash("We couldn't read that resume right now. Please try again.", "error")
            return redirect(url_for("apply_to_job_page", job_id=job_id))

        application_id = db.execute(
            """INSERT INTO applications (job_id, candidate_id, resume_id, screening_status)
               VALUES (%s,%s,%s,'pending') RETURNING id""",
            (job_id, user_id, resume_id),
        )

        bg_scheduler.run_in_background(_process_application_screening, application_id)

        flash(
            "Application submitted! We're scoring your resume against this job now — "
            "your match score will show up on My Applications in a moment.",
            "success",
        )
        return redirect(url_for("candidate_applications"))

    def _process_application_screening(application_id):
        """Runs on a background thread (see scheduler.run_in_background),
        AFTER the candidate has already gotten their 'Application
        submitted!' response. This is where the actual Gemini network
        call happens, so it never blocks the request.

        The ENTIRE body is wrapped — any failure anywhere (a bad JOIN, a
        missing column from an unrun migration, a mailer error, whatever)
        must end in screening_status='failed' with a real error message,
        never leave the row stuck at 'processing' forever with no visible
        reason. A visibly failed screen is fixable; a silently stuck one
        just looks broken."""
        db.execute("UPDATE applications SET screening_status='processing' WHERE id=%s", (application_id,))

        try:
            row = db.query_one(
                """SELECT a.*, u.id AS candidate_id, u.full_name, u.email,
                          j.id AS job_id, j.title, j.company_name, j.description,
                          j.min_match_score, j.test_trigger_mode,
                          j.jd_required_skills, j.jd_preferred_skills
                   FROM applications a
                   JOIN users u ON u.id = a.candidate_id
                   JOIN jobs j ON j.id = a.job_id
                   WHERE a.id=%s""",
                (application_id,),
            )
            if not row:
                logger.error("Screening job: application %s not found", application_id)
                return

            resume_row = db.query_one("SELECT * FROM resumes WHERE id=%s", (row["resume_id"],))
            if not resume_row:
                db.execute(
                    "UPDATE applications SET screening_status='failed', screening_error=%s WHERE id=%s",
                    ("Resume record not found", application_id),
                )
                return

            parsed = resume_bank.as_parsed_dict(resume_row)
            cached_required = row.get("jd_required_skills") or []
            cached_preferred = row.get("jd_preferred_skills") or []
            cached_jd_skills = {"required_skills": cached_required, "preferred_skills": cached_preferred} \
                if (cached_required or cached_preferred) else None

            screen = ats_engine.screen_parsed_resume(parsed, row["description"], jd_skills=cached_jd_skills)

            db.execute(
                """UPDATE applications
                   SET match_score=%s, technical_match_score=%s, experience_match_score=%s,
                       soft_skills_score=%s, impact_score=%s, matched_skills=%s, missing_skills=%s,
                       red_flags=%s, ats_summary=%s, passed_ats=%s, screening_status='done'
                   WHERE id=%s""",
                (screen["ats_score"], screen["technical_match_score"], screen["experience_match_score"],
                 screen["soft_skills_score"], screen["impact_score"],
                 json.dumps(screen["matched_skills"]), json.dumps(screen["missing_skills"]),
                 json.dumps(screen["red_flags"]), screen["ats_summary"],
                 screen["ats_score"] >= (row.get("min_match_score") or 60),
                 application_id),
            )

            job = {
                "id": row["job_id"], "title": row["title"], "company_name": row["company_name"],
                "description": row["description"], "min_match_score": row["min_match_score"],
                "test_trigger_mode": row["test_trigger_mode"],
            }
            candidate = {"id": row["candidate_id"], "full_name": row["full_name"], "email": row["email"]}
            skills_for_session = parsed.get("skills", [])
            # Dispatch (reject email / round1 session+invite) failing should
            # NOT re-hide a score that was already computed and saved above —
            # log it, but the application stays 'done' with its real score.
            try:
                _dispatch_ats_outcome(application_id, job, candidate, screen, resume_id=row["resume_id"],
                                       skills=skills_for_session)
            except Exception as e:
                logger.error("Dispatch failed for application %s (score was still saved): %s", application_id, e)

        except Exception as e:
            logger.error("Screening failed for application %s: %s", application_id, e)
            db.execute(
                "UPDATE applications SET screening_status='failed', screening_error=%s WHERE id=%s",
                (str(e)[:500], application_id),
            )

    def _dispatch_ats_outcome(application_id, job, candidate, screen, resume_id, skills):
        """Implements the employer's configured pipeline once ATS scoring
        is done: instant rejection if below baseline, otherwise a Round 1
        session is created and the invite is sent immediately / scheduled
        for later / held for manual approval, per job.test_trigger_mode."""
        passed = screen["ats_score"] >= (job.get("min_match_score") or 60)

        if not passed:
            db.execute(
                "UPDATE applications SET invite_status='rejected', rejected_reason=%s WHERE id=%s",
                (f"ATS match score {screen['ats_score']}% below required {job['min_match_score']}%",
                 application_id),
            )
            sent = mailer.send_rejection_email(
                candidate["email"], candidate["full_name"], job["title"], job["company_name"],
                application_id=application_id,
            )
            if sent:
                db.execute(
                    "UPDATE applications SET rejection_emailed_at=NOW() WHERE id=%s", (application_id,)
                )
            db.execute("UPDATE applications SET status='Rejected' WHERE id=%s", (application_id,))
            return

        # Passed ATS — set up the Round 1 session now regardless of trigger
        # timing, so the invite link/token exists whenever it's sent.
        session_id, invite_token = models.create_round1_session(
            application_id=application_id, candidate_id=candidate["id"],
            job_id=job["id"], candidate_name=candidate["full_name"],
            resume_skills=skills or [], jd_text=job["description"] or "",
            jd_required_skills=screen.get("jd_required_skills", []),
            jd_preferred_skills=screen.get("jd_preferred_skills", []),
            skills_tested=(skills or [])[:6],
            missing_required_skills=screen.get("missing_skills", []),
        )
        db.execute("UPDATE applications SET round1_session_id=%s WHERE id=%s", (session_id, application_id))
        # experience_yrs isn't a session column; store on the session's
        # jd_text-adjacent bookkeeping via skills_tested is enough for
        # question selection — Round 2/3 re-read resume/job data directly.

        trigger_mode = job.get("test_trigger_mode") or "manual"
        if trigger_mode == "immediate":
            sent = mailer.send_test_invite_email(
                candidate["email"], candidate["full_name"], job["title"], job["company_name"],
                invite_token, application_id=application_id,
            )
            db.execute(
                "UPDATE applications SET invite_status=%s, invite_sent_at=NOW() WHERE id=%s",
                ("sent" if sent else "pending", application_id),
            )
        elif trigger_mode in TRIGGER_HOURS:
            scheduled_at = _schedule_invite_time(trigger_mode)
            db.execute(
                "UPDATE applications SET invite_status='scheduled', invite_scheduled_at=%s WHERE id=%s",
                (scheduled_at, application_id),
            )
        else:  # manual
            db.execute(
                "UPDATE applications SET invite_status='manual_hold' WHERE id=%s", (application_id,)
            )

    @app.route("/employer/applicants/<int:app_id>/send-invite", methods=["POST"])
    @login_required(role="employer")
    def send_invite_now(app_id):
        employer_id = session["user_id"]
        row = db.query_one(
            """SELECT a.*, u.full_name, u.email, j.title, j.company_name, j.employer_id
               FROM applications a
               JOIN users u ON u.id = a.candidate_id
               JOIN jobs j ON j.id = a.job_id
               WHERE a.id=%s""",
            (app_id,),
        )
        if not row or row["employer_id"] != employer_id:
            flash("Not found.", "error")
            return redirect(url_for("employer_applicants"))
        if not row["passed_ats"]:
            flash("This candidate didn't clear the match-score baseline.", "error")
            return redirect(url_for("employer_applicants"))

        sess = db.query_one("SELECT invite_token FROM sessions WHERE id=%s", (row["round1_session_id"],))
        if not sess:
            flash("No Round 1 session found for this application.", "error")
            return redirect(url_for("employer_applicants"))

        sent = mailer.send_test_invite_email(
            row["email"], row["full_name"], row["title"], row["company_name"],
            sess["invite_token"], application_id=app_id,
        )
        db.execute(
            "UPDATE applications SET invite_status=%s, invite_sent_at=NOW() WHERE id=%s",
            ("sent" if sent else "pending", app_id),
        )
        flash("Test invite sent." if sent else "Invite queued, but email sending failed — check SMTP settings.",
              "success" if sent else "error")
        return redirect(url_for("employer_applicants"))

    @app.route("/candidate/jobs/<int:job_id>/save", methods=["POST"])
    @login_required(role="candidate")
    def save_job(job_id):
        user_id = session["user_id"]
        existing = db.query_one(
            "SELECT id FROM saved_jobs WHERE job_id=%s AND candidate_id=%s", (job_id, user_id)
        )
        if existing:
            db.execute("DELETE FROM saved_jobs WHERE id = %s", (existing["id"],))
            flash("Removed from saved jobs.", "success")
        else:
            db.execute(
                "INSERT INTO saved_jobs (job_id, candidate_id) VALUES (%s,%s)", (job_id, user_id)
            )
            flash("Job saved!", "success")
        return redirect(request.referrer or url_for("candidate_jobs"))

    @app.route("/candidate/saved-jobs")
    @login_required(role="candidate")
    def saved_jobs_page():
        user_id = session["user_id"]
        jobs = db.query_all(
            """SELECT j.* FROM jobs j
               JOIN saved_jobs s ON s.job_id = j.id
               WHERE s.candidate_id = %s ORDER BY s.saved_at DESC""",
            (user_id,),
        )
        applied_job_ids = {
            row["job_id"] for row in db.query_all(
                "SELECT job_id FROM applications WHERE candidate_id = %s", (user_id,)
            )
        }
        return render_template("candidate_saved_jobs.html", jobs=jobs, saved_job_ids={j["id"] for j in jobs},
                                applied_job_ids=applied_job_ids)

    @app.route("/candidate/applications")
    @login_required(role="candidate")
    def candidate_applications():
        user_id = session["user_id"]
        apps = db.query_all(
            """SELECT a.*, j.title, j.company_name, j.location
               FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE a.candidate_id = %s ORDER BY a.applied_at DESC""",
            (user_id,),
        )
        return render_template("candidate_applications.html", apps=apps)

    @app.route("/candidate/applications/<int:app_id>")
    @login_required(role="candidate")
    def candidate_application_detail(app_id):
        user_id = session["user_id"]
        application = db.query_one(
            """SELECT a.*, j.title, j.company_name, j.location, j.job_type,
                      j.description, j.min_match_score, j.id AS job_id,
                      r.filename AS resume_filename, r.id AS resume_id_ref
               FROM applications a
               JOIN jobs j ON j.id = a.job_id
               LEFT JOIN resumes r ON r.id = a.resume_id
               WHERE a.id = %s AND a.candidate_id = %s""",
            (app_id, user_id),
        )
        if not application:
            abort(404)
        for key in ("matched_skills", "missing_skills", "red_flags"):
            if isinstance(application.get(key), str):
                try:
                    application[key] = json.loads(application[key])
                except (TypeError, ValueError):
                    application[key] = []
        return render_template("candidate_application_detail.html", a=application)

    @app.route("/candidate/resumes/<int:resume_id>/file")
    @login_required(role="candidate")
    def view_own_resume_file(resume_id):
        user_id = session["user_id"]
        resume_row = resume_bank.get(resume_id, candidate_id=user_id)
        if not resume_row or not resume_row.get("file_path") or not os.path.exists(resume_row["file_path"]):
            abort(404)
        download = request.args.get("download") == "1"
        return send_file(resume_row["file_path"], as_attachment=download,
                          download_name=resume_row.get("filename") or "resume.pdf")

    @app.route("/employer/resumes/<int:resume_id>/file")
    @login_required(role="employer")
    def view_candidate_resume_file(resume_id):
        employer_id = session["user_id"]
        # Employers may only view resumes attached to an application on
        # one of THEIR OWN job postings — never any resume by id.
        owns = db.query_one(
            """SELECT 1 FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE a.resume_id = %s AND j.employer_id = %s LIMIT 1""",
            (resume_id, employer_id),
        )
        if not owns:
            abort(403)
        resume_row = resume_bank.get(resume_id)
        if not resume_row or not resume_row.get("file_path") or not os.path.exists(resume_row["file_path"]):
            abort(404)
        download = request.args.get("download") == "1"
        return send_file(resume_row["file_path"], as_attachment=download,
                          download_name=resume_row.get("filename") or "resume.pdf")

    @app.route("/candidate/resume-score", methods=["GET", "POST"])
    @login_required(role="candidate")
    def resume_score():
        user_id = session["user_id"]
        if request.method == "POST":
            file = request.files.get("resume")
            if not file or not file.filename or not allowed_file(file.filename, app):
                flash("Please upload a PDF, DOC, or DOCX file.", "error")
                return redirect(url_for("resume_score"))

            filename = secure_filename(f"user{user_id}_{int(datetime.now().timestamp())}_{file.filename}")
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(file_path)

            try:
                with open(file_path, "rb") as f:
                    result = resume_score_engine.score_resume_file(f)
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("resume_score"))
            except Exception as e:
                print(f"Resume scoring error: {e}")
                flash("We couldn't analyze that resume right now. Please try again.", "error")
                return redirect(url_for("resume_score"))

            # Save into the resume bank too — same file becomes selectable
            # at apply time without re-uploading.
            resume_parsed_for_save = {
                "masked_resume": result["_resume_masked_text"],
                "skills": result["_resume_skills"],
                "experience": result["_resume_experience"],
                "projects": result["_resume_projects"],
                "name_found": result["_resume_name_found"],
                "raw_text": result.get("_resume_raw_text", ""),
            }
            resume_id = resume_bank.save(user_id, filename, file_path, resume_parsed_for_save)

            breakdown = {
                "skills_depth": result["skills_depth"],
                "experience_clarity": result["experience_clarity"],
                "impact_score": result["impact_score"],
                "ats_friendliness": result["ats_friendliness"],
            }
            db.execute(
                """UPDATE candidate_profiles
                   SET resume_score=%s, resume_filename=%s, resume_id=%s,
                       resume_score_breakdown=%s, resume_strengths=%s,
                       resume_weaknesses=%s, resume_suggested_skills=%s,
                       resume_score_summary=%s,
                       tech_stack = COALESCE(NULLIF(%s, ''), tech_stack)
                   WHERE user_id=%s""",
                (result["resume_score"], filename, resume_id,
                 json.dumps(breakdown), json.dumps(result["strengths"]),
                 json.dumps(result["weaknesses"]), json.dumps(result["suggested_skills"]),
                 result["summary"], ", ".join(result["_resume_skills"] or []), user_id),
            )
            flash(f"Resume analyzed — your score is {result['resume_score']}/100.", "success")
            return redirect(url_for("resume_score"))

        profile = db.query_one("SELECT * FROM candidate_profiles WHERE user_id = %s", (user_id,))
        breakdown = profile.get("resume_score_breakdown") if profile else None
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except (TypeError, ValueError):
                breakdown = {}
        for key in ("strengths", "weaknesses", "suggested_skills"):
            if profile and isinstance(profile.get(f"resume_{key}"), str):
                try:
                    profile[f"resume_{key}"] = json.loads(profile[f"resume_{key}"])
                except (TypeError, ValueError):
                    profile[f"resume_{key}"] = []
        return render_template("candidate_resume_score.html", profile=profile, breakdown=breakdown or {})

    @app.route("/candidate/profile", methods=["GET", "POST"])
    @login_required(role="candidate")
    def candidate_profile():
        user_id = session["user_id"]
        if request.method == "POST":
            headline = request.form.get("headline", "").strip()
            location = request.form.get("location", "").strip()
            experience = request.form.get("experience_yrs") or 0
            tech_stack = request.form.get("tech_stack", "").strip()
            interests = request.form.get("interests", "").strip()
            preferred_locations = request.form.get("preferred_locations", "").strip()
            preferred_job_types = ",".join(request.form.getlist("preferred_job_types"))

            db.execute(
                """UPDATE candidate_profiles
                   SET headline=%s, location=%s, experience_yrs=%s, tech_stack=%s,
                       interests=%s, preferred_locations=%s, preferred_job_types=%s
                   WHERE user_id=%s""",
                (headline, location, experience, tech_stack, interests,
                 preferred_locations, preferred_job_types, user_id),
            )
            flash("Profile updated. Your job feed will now reflect these interests.", "success")
            return redirect(url_for("candidate_profile"))

        profile = db.query_one("SELECT * FROM candidate_profiles WHERE user_id = %s", (user_id,))
        user = db.query_one("SELECT * FROM users WHERE id = %s", (user_id,))
        return render_template("candidate_profile.html", profile=profile, user=user)

    # ---------------- Employer dashboard ----------------

    @app.route("/employer/dashboard")
    @login_required(role="employer")
    def employer_dashboard():
        employer_id = session["user_id"]

        totals = db.query_one(
            "SELECT COUNT(*) AS total_jobs FROM jobs WHERE employer_id=%s", (employer_id,)
        )
        applicant_totals = db.query_one(
            """SELECT
                 COUNT(*) AS total_applicants,
                 COUNT(*) FILTER (WHERE a.status='Shortlisted') AS shortlisted,
                 COUNT(*) FILTER (WHERE a.status='Interview') AS interviews,
                 COUNT(*) FILTER (WHERE a.status='Offered') AS offers
               FROM applications a JOIN jobs j ON j.id = a.job_id
               WHERE j.employer_id = %s""",
            (employer_id,),
        ) or {}

        recent_applicants = db.query_all(
            """SELECT a.*, u.full_name, u.avatar_initials, j.title AS job_title
               FROM applications a
               JOIN jobs j ON j.id = a.job_id
               JOIN users u ON u.id = a.candidate_id
               WHERE j.employer_id = %s
               ORDER BY a.applied_at DESC LIMIT 4""",
            (employer_id,),
        )

        recent_jobs = db.query_all(
            """SELECT j.*,
                   (SELECT COUNT(*) FROM applications a WHERE a.job_id = j.id) AS applicant_count
               FROM jobs j WHERE j.employer_id = %s
               ORDER BY j.posted_at DESC LIMIT 3""",
            (employer_id,),
        )

        # Top skills in demand — aggregate tech_stack across this employer's jobs
        jobs_for_skills = db.query_all(
            "SELECT tech_stack FROM jobs WHERE employer_id = %s", (employer_id,)
        )
        skill_counts = {}
        for row in jobs_for_skills:
            if row["tech_stack"]:
                for skill in row["tech_stack"].split(","):
                    skill = skill.strip()
                    if skill:
                        skill_counts[skill] = skill_counts.get(skill, 0) + 1
        top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        stats = {
            "total_jobs": totals["total_jobs"] if totals else 0,
            "total_applicants": applicant_totals.get("total_applicants") or 0,
            "shortlisted": applicant_totals.get("shortlisted") or 0,
            "interviews": applicant_totals.get("interviews") or 0,
            "offers": applicant_totals.get("offers") or 0,
        }

        return render_template(
            "employer_dashboard.html",
            stats=stats,
            recent_applicants=recent_applicants,
            recent_jobs=recent_jobs,
            top_skills=top_skills,
        )

    @app.route("/employer/jobs/create", methods=["GET", "POST"])
    @login_required(role="employer")
    def create_job():
        employer_id = session["user_id"]
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            location = request.form.get("location", "").strip()
            job_type = request.form.get("job_type", "Full-time")
            description = request.form.get("description", "").strip()
            
            # Extract tech stack + cache structured JD skills from description
            # using AI — ONE Gemini call at posting time. Every applicant
            # reuses this instead of the JD being re-parsed per application.
            jd_required_skills, jd_preferred_skills = [], []
            try:
                jd_skills = gemini_client.parse_jd_skills(description)
                jd_required_skills = jd_skills.get("required_skills", [])
                jd_preferred_skills = jd_skills.get("preferred_skills", [])
                tech_stack = ", ".join(jd_required_skills + jd_preferred_skills)
            except Exception as e:
                print(f"JD parsing error: {e}")
                tech_stack = request.form.get("tech_stack", "").strip()

            salary_min = request.form.get("salary_min") or None
            salary_max = request.form.get("salary_max") or None
            min_match_score = request.form.get("min_match_score") or 60
            test_trigger_mode = request.form.get("test_trigger_mode", "manual")
            if test_trigger_mode not in ("immediate", "12h", "24h", "manual"):
                test_trigger_mode = "manual"

            company = db.query_one(
                "SELECT company_name FROM company_profiles WHERE user_id=%s", (employer_id,)
            )
            company_name = (company["company_name"] if company else None) or session["full_name"]

            if not title or not location:
                flash("Job title and location are required.", "error")
                return redirect(url_for("create_job"))

            db.execute(
                """INSERT INTO jobs
                   (employer_id, title, company_name, location, job_type, tech_stack,
                    salary_min_lpa, salary_max_lpa, description, min_match_score, test_trigger_mode,
                    jd_required_skills, jd_preferred_skills)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (employer_id, title, company_name, location, job_type, tech_stack,
                 salary_min, salary_max, description, min_match_score, test_trigger_mode,
                 json.dumps(jd_required_skills), json.dumps(jd_preferred_skills)),
            )
            flash("Job posted successfully!", "success")
            return redirect(url_for("manage_jobs"))

        return render_template("employer_create_job.html")

    @app.route("/employer/jobs")
    @login_required(role="employer")
    def manage_jobs():
        employer_id = session["user_id"]
        jobs = db.query_all(
            """SELECT j.*,
                   (SELECT COUNT(*) FROM applications a WHERE a.job_id=j.id) AS applicant_count
               FROM jobs j WHERE j.employer_id=%s ORDER BY j.posted_at DESC""",
            (employer_id,),
        )
        return render_template("employer_manage_jobs.html", jobs=jobs)

    @app.route("/employer/jobs/<int:job_id>/close", methods=["POST"])
    @login_required(role="employer")
    def close_job(job_id):
        employer_id = session["user_id"]
        db.execute(
            "UPDATE jobs SET status='Closed' WHERE id=%s AND employer_id=%s",
            (job_id, employer_id),
        )
        flash("Job closed.", "success")
        return redirect(url_for("manage_jobs"))

    @app.route("/employer/applicants")
    @login_required(role="employer")
    def employer_applicants():
        employer_id = session["user_id"]
        applicants = db.query_all(
            """SELECT a.*, u.full_name, u.email, u.avatar_initials,
                      j.title AS job_title, j.min_match_score, j.test_trigger_mode,
                      cp.headline, cp.tech_stack, cp.resume_score,
                      s.integrity_score AS session_integrity_score, s.warnings_count,
                      s.round1_result, s.mcq_pct, s.part_b_score
               FROM applications a
               JOIN jobs j ON j.id = a.job_id
               JOIN users u ON u.id = a.candidate_id
               LEFT JOIN candidate_profiles cp ON cp.user_id = u.id
               LEFT JOIN sessions s ON s.id = a.round1_session_id
               WHERE j.employer_id = %s
               ORDER BY a.applied_at DESC""",
            (employer_id,),
        )
        for a in applicants:
            for key in ("matched_skills", "missing_skills", "red_flags"):
                if isinstance(a.get(key), str):
                    try:
                        a[key] = json.loads(a[key])
                    except (TypeError, ValueError):
                        a[key] = []
        return render_template("employer_applicants.html", applicants=applicants)

    @app.route("/employer/applicants/<int:app_id>")
    @login_required(role="employer")
    def employer_applicant_detail(app_id):
        employer_id = session["user_id"]
        a = db.query_one(
            """SELECT a.*, u.full_name, u.email, u.avatar_initials,
                      j.title AS job_title, j.min_match_score, j.test_trigger_mode, j.id AS job_id,
                      s.integrity_score AS session_integrity_score, s.warnings_count,
                      s.mcq_pct, s.mcq_total, s.mcq_correct,
                      r.id AS resume_id_ref, r.filename AS resume_filename,
                      r.skills AS resume_skills, r.experience AS resume_experience,
                      r.projects AS resume_projects, r.experience_yrs AS resume_experience_yrs
               FROM applications a
               JOIN jobs j ON j.id = a.job_id
               JOIN users u ON u.id = a.candidate_id
               LEFT JOIN sessions s ON s.id = a.round1_session_id
               LEFT JOIN resumes r ON r.id = a.resume_id
               WHERE a.id = %s AND j.employer_id = %s""",
            (app_id, employer_id),
        )
        if not a:
            abort(404)
        for key in ("matched_skills", "missing_skills", "red_flags",
                    "resume_skills", "resume_experience", "resume_projects"):
            if isinstance(a.get(key), str):
                try:
                    a[key] = json.loads(a[key])
                except (TypeError, ValueError):
                    a[key] = []
        return render_template("employer_applicant_detail.html", a=a)

    @app.route("/employer/applicants/<int:app_id>/status", methods=["POST"])
    @login_required(role="employer")
    def update_applicant_status(app_id):
        employer_id = session["user_id"]
        new_status = request.form.get("status")
        valid = {"Applied", "Shortlisted", "Interview", "Rejected", "Offered"}
        if new_status not in valid:
            flash("Invalid status.", "error")
            return redirect(url_for("employer_applicants"))

        # make sure this application belongs to one of this employer's jobs
        row = db.query_one(
            """SELECT a.id, a.invite_status, a.rejection_emailed_at, u.full_name, u.email,
                      j.title, j.company_name
               FROM applications a JOIN jobs j ON j.id=a.job_id
               JOIN users u ON u.id = a.candidate_id
               WHERE a.id=%s AND j.employer_id=%s""",
            (app_id, employer_id),
        )
        if row:
            db.execute("UPDATE applications SET status=%s WHERE id=%s", (new_status, app_id))
            if new_status == "Rejected":
                # Cancel any pending scheduled/manual invite, and send the
                # same polite rejection email a failed-baseline candidate
                # would get, if we haven't already emailed them.
                db.execute(
                    "UPDATE applications SET invite_status='withdrawn' WHERE id=%s "
                    "AND invite_status IN ('scheduled','manual_hold','pending')",
                    (app_id,),
                )
                if not row["rejection_emailed_at"]:
                    sent = mailer.send_rejection_email(
                        row["email"], row["full_name"], row["title"], row["company_name"],
                        application_id=app_id,
                    )
                    if sent:
                        db.execute(
                            "UPDATE applications SET rejection_emailed_at=NOW() WHERE id=%s", (app_id,)
                        )
            flash("Applicant status updated.", "success")
        return redirect(url_for("employer_applicants"))

    @app.route("/employer/applicants/<int:app_id>/integrity-report")
    @login_required(role="employer")
    def applicant_integrity_report(app_id):
        employer_id = session["user_id"]
        row = db.query_one(
            """SELECT a.round1_session_id, a.id FROM applications a JOIN jobs j ON j.id=a.job_id
               WHERE a.id=%s AND j.employer_id=%s""", (app_id, employer_id),
        )
        if not row or not row["round1_session_id"]:
            abort(404)
        report = integrity.get_integrity_report(row["round1_session_id"])
        return render_template("integrity_report.html", report=report, app_id=app_id)

    @app.route("/employer/company-profile", methods=["GET", "POST"])
    @login_required(role="employer")
    def company_profile():
        employer_id = session["user_id"]
        if request.method == "POST":
            company_name = request.form.get("company_name", "").strip()
            industry = request.form.get("industry", "").strip()
            website = request.form.get("website", "").strip()
            location = request.form.get("location", "").strip()
            about = request.form.get("about", "").strip()
            company_size = request.form.get("company_size", "").strip()
            founded_year = request.form.get("founded_year") or None
            linkedin_url = request.form.get("linkedin_url", "").strip()
            twitter_url = request.form.get("twitter_url", "").strip()
            benefits = request.form.get("benefits", "").strip()
            tech_stack = request.form.get("tech_stack", "").strip()

            logo_path = None
            logo_file = request.files.get("logo")
            if logo_file and logo_file.filename and allowed_image_file(logo_file.filename):
                logo_filename = secure_filename(f"logo_{employer_id}_{int(datetime.now().timestamp())}_{logo_file.filename}")
                logo_full_path = os.path.join(app.config["UPLOAD_FOLDER"], logo_filename)
                logo_file.save(logo_full_path)
                logo_path = logo_filename

            if logo_path:
                db.execute(
                    """UPDATE company_profiles
                       SET company_name=%s, industry=%s, website=%s, location=%s, about=%s,
                           company_size=%s, founded_year=%s, linkedin_url=%s, twitter_url=%s,
                           benefits=%s, tech_stack=%s, logo_path=%s
                       WHERE user_id=%s""",
                    (company_name, industry, website, location, about, company_size, founded_year,
                     linkedin_url, twitter_url, benefits, tech_stack, logo_path, employer_id),
                )
            else:
                db.execute(
                    """UPDATE company_profiles
                       SET company_name=%s, industry=%s, website=%s, location=%s, about=%s,
                           company_size=%s, founded_year=%s, linkedin_url=%s, twitter_url=%s,
                           benefits=%s, tech_stack=%s
                       WHERE user_id=%s""",
                    (company_name, industry, website, location, about, company_size, founded_year,
                     linkedin_url, twitter_url, benefits, tech_stack, employer_id),
                )
            flash("Company profile updated.", "success")
            return redirect(url_for("company_profile"))

        profile = db.query_one("SELECT * FROM company_profiles WHERE user_id=%s", (employer_id,))
        return render_template("employer_company_profile.html", profile=profile)

    # ---------------- Shared settings page ----------------

    @app.route("/settings", methods=["GET", "POST"])
    @login_required()
    def settings():
        user_id = session["user_id"]
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            if full_name:
                db.execute("UPDATE users SET full_name=%s WHERE id=%s", (full_name, user_id))
                session["full_name"] = full_name
                session["initials"] = initials_from_name(full_name)
                flash("Settings saved.", "success")
            return redirect(url_for("settings"))

        user = db.query_one("SELECT * FROM users WHERE id = %s", (user_id,))
        return render_template("settings.html", user=user)



    # ══════════════════════════════════════════════════════════
    #  ROUND 1 — Skills Assessment (Part A: DB MCQ, Part B: Gemini subjective)
    #  Round 2 — Project/Experience deep dive (employer-unlocked, Gemini)
    #  Round 3 — JD-fit testing (employer-unlocked, Gemini)
    #  + anti-cheat: fullscreen/tab/copy-paste events, keystroke dynamics,
    #    desktop-only enforcement.
    #
    #  Per-question banks/progress live in the `test_state` table (see
    #  models.get_test_state / update_test_state), NOT the Flask cookie
    #  session — 10-15 MCQs (or Gemini-generated subjective questions)
    #  comfortably exceed the ~4KB signed-cookie limit, and cookie state
    #  also breaks across tabs/devices.
    # ══════════════════════════════════════════════════════════

    def _is_mobile_ua(user_agent_string):
        ua = (user_agent_string or "").lower()
        return any(tok in ua for tok in ["iphone", "android", "ipad", "mobile", "blackberry"])

    def _resume_for_application(application_id):
        row = db.query_one(
            "SELECT projects, experience, skills, experience_yrs FROM resumes r "
            "JOIN applications a ON a.resume_id = r.id WHERE a.id=%s",
            (application_id,),
        )
        if not row:
            return {"projects": [], "experience": [], "skills": [], "experience_yrs": 0}
        for key in ("projects", "experience", "skills"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except (TypeError, ValueError):
                    row[key] = []
        return row

    @app.route("/test/start/<invite_token>")
    def test_start(invite_token):
        test_session = models.get_session_by_token(invite_token)
        if not test_session:
            return render_template("test_invalid_link.html"), 404

        if _is_mobile_ua(request.headers.get("User-Agent")):
            models.update_session(test_session["id"], device_checked="mobile_blocked")
            return render_template("test_desktop_only.html")

        if test_session["status"] not in ("invited", "part_a", "part_b"):
            return render_template("test_already_done.html", test_session=test_session)

        return render_template("test_preflight.html", test_session=test_session, invite_token=invite_token)

    @app.route("/test/<session_id>/preflight", methods=["POST"])
    def submit_preflight(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)

        flight_times = json.loads(request.form.get("flight_times", "[]") or "[]")
        dwell_times = json.loads(request.form.get("dwell_times", "[]") or "[]")
        typed_text = request.form.get("typed_text", "")
        elapsed_ms = float(request.form.get("elapsed_ms", "1") or 1)
        wpm = (len(typed_text.split()) / (elapsed_ms / 1000 / 60)) if elapsed_ms > 0 else 0

        integrity.save_keystroke_baseline(session_id, {
            "flight_times": flight_times, "dwell_times": dwell_times, "wpm": round(wpm, 1),
        })
        models.update_session(session_id, device_checked="desktop_confirmed",
                               started_at=datetime.now(), status="part_a")
        return redirect(url_for("round1_part_a", session_id=session_id))

    # ---- Round 1 / Part A : DB-backed MCQ ----

    @app.route("/test/<session_id>/part-a", methods=["GET"])
    def round1_part_a(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)
        if test_session["status"] != "part_a":
            return redirect(url_for("test_start", invite_token=test_session["invite_token"]))

        state = models.get_test_state(session_id)
        if not state["mcq_bank"] and state["mcq_idx"] == 0:
            candidate = db.query_one(
                "SELECT technical_match_score, experience_match_score FROM applications WHERE id=%s",
                (test_session["application_id"],),
            )
            seniority = ats_engine.experience_level_from_score(
                candidate["technical_match_score"] or 50, candidate["experience_match_score"] or 50
            ) if candidate else "mid"
            questions = question_bank.select_round1_mcqs(test_session["skills_tested"], seniority)

            if not questions:
                # No MCQs available for this candidate's skills (an unseeded
                # or thin question bank) — this is a gap in the employer's
                # data, not a reason to penalize or reject the candidate.
                # Skip sub-round 1 entirely and move straight to sub-round 2.
                logger.warning(
                    "No MCQs found for session %s (skills=%s) — skipping sub-round 1",
                    session_id, test_session["skills_tested"],
                )
                models.update_session(session_id, mcq_total=0, mcq_pct=None, status="subround2")
                return redirect(url_for("round2_test", session_id=session_id))

            models.update_test_state(
                session_id, mcq_bank=json.dumps([dict(q) for q in questions]),
                mcq_idx=0, mcq_extended=False, mcq_seniority=seniority,
            )
            state = models.get_test_state(session_id)

        bank = state["mcq_bank"]
        idx = state["mcq_idx"]
        if idx >= len(bank):
            return redirect(url_for("round1_part_a_gate", session_id=session_id))

        return render_template("round1_part_a.html", test_session=test_session, question=bank[idx],
                                q_number=idx + 1, q_total=len(bank))

    @app.route("/test/<session_id>/part-a/answer", methods=["POST"])
    def round1_part_a_answer(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)

        state = models.get_test_state(session_id)
        bank, idx = state["mcq_bank"], state["mcq_idx"]
        if idx >= len(bank):
            return redirect(url_for("round1_part_a_gate", session_id=session_id))

        q = bank[idx]
        selected = request.form.get("selected_option", "")
        time_taken = int(request.form.get("time_taken_seconds", 0) or 0)
        is_correct = (selected == q.get("correct_option"))

        models.add_response(
            session_id=session_id, round_name="round1_mcq", skill=q.get("skill"),
            question=q.get("question"), candidate_answer=selected,
            score=100.0 if is_correct else 0.0,
            options=q.get("options"), correct_index=None,
            model_answer=q.get("correct_option_text"),
            time_taken_seconds=time_taken, is_correct=is_correct,
            question_bank_id=q.get("id"),
        )
        models.update_test_state(session_id, mcq_idx=idx + 1)
        return redirect(url_for("round1_part_a", session_id=session_id))

    @app.route("/test/<session_id>/part-a/gate")
    def round1_part_a_gate(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)

        responses = models.get_responses(session_id, round_name="round1_mcq")
        pct = round1_engine.score_part_a([r["is_correct"] for r in responses])
        state = models.get_test_state(session_id)
        decision = round1_engine.part_a_gate(pct, state["mcq_extended"])

        models.update_session(session_id, mcq_total=len(responses),
                               mcq_correct=sum(1 for r in responses if r["is_correct"]), mcq_pct=pct)

        if decision == "extend":
            exclude_ids = [r["question_bank_id"] for r in responses if r.get("question_bank_id")]
            bonus = question_bank.select_bonus_questions(
                test_session["skills_tested"], state["mcq_seniority"], exclude_ids, round1_engine.BONUS_QUESTIONS
            )
            if not bonus:
                # Bank exhausted — can't extend further, just make the call
                # with what we have rather than getting stuck.
                decision = "advance" if pct >= round1_engine.BORDERLINE_REJECT_PCT else "reject"
            else:
                models.update_test_state(
                    session_id, mcq_bank=json.dumps(state["mcq_bank"] + [dict(q) for q in bonus]),
                    mcq_extended=True,
                )
                return redirect(url_for("round1_part_a", session_id=session_id))

        if decision == "reject":
            models.update_session(session_id, status="rejected", round1_result="rejected",
                                   round1_verdict="reject", completed_at=datetime.now())
            db.execute(
                "UPDATE applications SET status='Rejected', round1_verdict='reject', round1_score=%s, "
                "round1_completed_at=NOW() WHERE id=%s", (pct, test_session["application_id"]),
            )
            app_row = db.query_one(
                """SELECT u.full_name, u.email, j.title, j.company_name FROM applications a
                   JOIN users u ON u.id=a.candidate_id JOIN jobs j ON j.id=a.job_id WHERE a.id=%s""",
                (test_session["application_id"],),
            )
            if app_row:
                mailer.send_rejection_email(app_row["email"], app_row["full_name"], app_row["title"],
                                             app_row["company_name"], application_id=test_session["application_id"])
            return redirect(url_for("test_graceful_exit", session_id=session_id))

        # advance -> sub-round 2 (Project & Experience deep dive)
        models.update_session(session_id, status="subround2")
        return redirect(url_for("round2_test", session_id=session_id))

    @app.route("/test/<session_id>/graceful-exit")
    def test_graceful_exit(session_id):
        return render_template("test_graceful_exit.html")

    # ---- Sub-round 2 : Project & Experience deep dive (Gemini, dynamic) ----
    # Runs automatically right after sub-round 1 — no employer action needed.

    @app.route("/test/<session_id>/round2", methods=["GET"])
    def round2_test(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)
        if test_session["status"] != "subround2":
            return redirect(url_for("test_start", invite_token=test_session["invite_token"]))

        state = models.get_test_state(session_id)
        if not state["round2_bank"]:
            resume = _resume_for_application(test_session["application_id"])
            questions = round2_engine.generate_round2_questions(resume["projects"], resume["experience"])
            models.update_test_state(session_id, round2_bank=json.dumps(questions), round2_idx=0)
            state = models.get_test_state(session_id)

        bank, idx = state["round2_bank"], state["round2_idx"]
        if idx >= len(bank):
            return redirect(url_for("round2_complete", session_id=session_id))
        return render_template("round2_test.html", test_session=test_session, question=bank[idx],
                                q_number=idx + 1, q_total=len(bank))

    @app.route("/test/<session_id>/round2/answer", methods=["POST"])
    def round2_answer(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)
        state = models.get_test_state(session_id)
        bank, idx = state["round2_bank"], state["round2_idx"]
        if idx >= len(bank):
            return redirect(url_for("round2_complete", session_id=session_id))
        q = bank[idx]
        answer = request.form.get("answer", "")
        time_taken = int(request.form.get("time_taken_seconds", 0) or 0)
        keystroke_metrics = json.loads(request.form.get("keystroke_metrics", "{}") or "{}")
        paste_detected, _flags = integrity.analyze_answer_keystrokes(
            session_id, test_session.get("keystroke_baseline"), keystroke_metrics, answer, time_taken
        )
        score, justification = round2_engine.grade_round2(
            q["question"], q.get("rubric", []), q.get("model_answer", ""), answer
        )
        models.add_response(session_id=session_id, round_name="round2", skill=q.get("topic"),
                             question=q["question"], candidate_answer=answer, score=score,
                             model_answer=q.get("model_answer"), rubric=q.get("rubric"),
                             justification=justification,
                             time_taken_seconds=time_taken, paste_detected=paste_detected,
                             keystroke_metrics=keystroke_metrics)
        models.update_test_state(session_id, round2_idx=idx + 1)
        return redirect(url_for("round2_test", session_id=session_id))

    @app.route("/test/<session_id>/round2/complete")
    def round2_complete(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)
        responses = models.get_responses(session_id, round_name="round2")
        pct = round2_engine.score_round2([r["score"] for r in responses]) if responses else 0.0
        models.update_session(session_id, round2_score=pct, status="subround3")
        db.execute("UPDATE applications SET round2_score=%s WHERE id=%s", (pct, test_session["application_id"]))
        # Straight into sub-round 3 — one continuous test, no waiting on
        # anyone to unlock the next part.
        return redirect(url_for("round3_test", session_id=session_id))

    # ---- Sub-round 3 : JD-fit testing (Gemini, dynamic) ----
    # The final sub-round — completing this finishes Round 1 entirely.

    @app.route("/test/<session_id>/round3", methods=["GET"])
    def round3_test(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)
        if test_session["status"] != "subround3":
            return redirect(url_for("test_start", invite_token=test_session["invite_token"]))

        state = models.get_test_state(session_id)
        if not state["round3_bank"]:
            resume = _resume_for_application(test_session["application_id"])
            questions = round3_engine.generate_round3_questions(
                test_session.get("jd_text", ""), resume["experience_yrs"], test_session.get("skills_tested")
            )
            models.update_test_state(session_id, round3_bank=json.dumps(questions), round3_idx=0)
            state = models.get_test_state(session_id)

        bank, idx = state["round3_bank"], state["round3_idx"]
        if idx >= len(bank):
            return redirect(url_for("round3_complete", session_id=session_id))
        return render_template("round3_test.html", test_session=test_session, question=bank[idx],
                                q_number=idx + 1, q_total=len(bank))

    @app.route("/test/<session_id>/round3/answer", methods=["POST"])
    def round3_answer(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)
        state = models.get_test_state(session_id)
        bank, idx = state["round3_bank"], state["round3_idx"]
        if idx >= len(bank):
            return redirect(url_for("round3_complete", session_id=session_id))
        q = bank[idx]
        answer = request.form.get("answer", "")
        time_taken = int(request.form.get("time_taken_seconds", 0) or 0)
        keystroke_metrics = json.loads(request.form.get("keystroke_metrics", "{}") or "{}")
        paste_detected, _flags = integrity.analyze_answer_keystrokes(
            session_id, test_session.get("keystroke_baseline"), keystroke_metrics, answer, time_taken
        )
        score, justification = round3_engine.grade_round3(
            q["question"], q.get("rubric", []), q.get("model_answer", ""), answer
        )
        models.add_response(session_id=session_id, round_name="round3", skill=None,
                             question=q["question"], candidate_answer=answer, score=score,
                             model_answer=q.get("model_answer"), rubric=q.get("rubric"),
                             justification=justification,
                             time_taken_seconds=time_taken, paste_detected=paste_detected,
                             keystroke_metrics=keystroke_metrics)
        models.update_test_state(session_id, round3_idx=idx + 1)
        return redirect(url_for("round3_test", session_id=session_id))

    @app.route("/test/<session_id>/round3/complete")
    def round3_complete(session_id):
        test_session = models.get_session(session_id)
        if not test_session:
            abort(404)

        responses = models.get_responses(session_id, round_name="round3")
        round3_pct = round3_engine.score_round3([r["score"] for r in responses]) if responses else 0.0
        mcq_pct = test_session.get("mcq_pct")  # may be None — skipped sub-round 1
        round2_pct = test_session.get("round2_score") or 0.0

        result = round1_engine.compute_final_round1_score(mcq_pct, round2_pct, round3_pct)

        integ = db.query_one("SELECT integrity_score FROM sessions WHERE id=%s", (session_id,))
        models.update_session(session_id, round3_score=round3_pct, status="completed",
                               round1_verdict=result["verdict"], round1_result="completed",
                               completed_at=datetime.now())
        db.execute(
            "UPDATE applications SET round1_score=%s, round1_verdict=%s, round2_score=%s, round3_score=%s, "
            "round1_completed_at=NOW(), integrity_score=%s WHERE id=%s",
            (result["round1_score"], result["verdict"], round2_pct, round3_pct,
             integ["integrity_score"] if integ else None, test_session["application_id"]),
        )

        # The exact verdict is for the employer's scorecard, not the
        # candidate — everyone who finishes the full test gets the same
        # warm, generic completion screen (see spec: don't ghost people
        # who gave you 20+ minutes). A below-threshold overall score still
        # gets flagged to the employer as 'reject', but we don't put the
        # candidate through a harsh rejection screen after they completed
        # every sub-round in good faith.
        if result["verdict"] == "reject":
            db.execute("UPDATE applications SET status='Rejected' WHERE id=%s", (test_session["application_id"],))

        top_skills = (test_session.get("skills_tested") or [])[:2]
        return render_template("test_complete.html", top_skills=top_skills)

    # ---- Anti-cheat API: browser -> backend event/keystroke beacons ----

    @app.route("/api/test/<session_id>/integrity-event", methods=["POST"])
    def api_integrity_event(session_id):
        data = request.get_json(silent=True) or {}
        event_type = data.get("event_type")
        detail = data.get("detail")
        if event_type not in ("tab_switch", "fullscreen_exit", "copy", "paste", "right_click", "devtools_open"):
            return jsonify({"error": "invalid event_type"}), 400
        result = integrity.log_event(session_id, event_type, detail)
        if result["should_terminate"]:
            models.update_session(session_id, status="terminated")
        return jsonify(result)

    @app.route("/employer/applicants/<int:app_id>/integrity-report-json")
    @login_required(role="employer")
    def applicant_integrity_report_json(app_id):
        row = db.query_one(
            """SELECT a.round1_session_id FROM applications a JOIN jobs j ON j.id=a.job_id
               WHERE a.id=%s AND j.employer_id=%s""", (app_id, session["user_id"]),
        )
        if not row or not row["round1_session_id"]:
            return jsonify({"error": "not found"}), 404
        return jsonify(integrity.get_integrity_report(row["round1_session_id"]))

app = create_app()

if __name__ == "__main__":
    app.run(debug=app.debug, port=int(os.environ.get("PORT", 5001)))