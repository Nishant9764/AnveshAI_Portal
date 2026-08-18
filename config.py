import os


class Config:
    # NOTE: was reading "SECRET_KEY" while .env defines "FLASK_SECRET_KEY" —
    # silently falling back to the dev default every run. Fixed to check both.
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.environ.get(
        "SECRET_KEY", "dev-secret-key-change-this-in-production"
    )

    # ---- PostgreSQL connection settings ----
    POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
    POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
    POSTGRES_DB = os.environ.get("POSTGRES_DB", "resume_assessment")
    POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", 5432))

    # ---- File uploads (resumes) ----
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
    ALLOWED_EXTENSIONS = {"pdf", "doc", "docx"}
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB

    # ---- SMTP (rejection + test-invite emails) ----
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true")

    # ---- App base URL (used to build the test-invite link) ----
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5001")

    # ---- Background scheduler (delayed test-invite triggers) ----
    SCHEDULER_CHECK_INTERVAL_MINUTES = int(os.environ.get("SCHEDULER_CHECK_INTERVAL_MINUTES", 5))
