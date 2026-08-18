-- SmartHire AI — PostgreSQL schema
-- Run this once to create the database and tables:
--   psql -U postgres -d resume_assessment -f schema.sql
-- (Make sure you have created the database `resume_assessment` first!)

-- ============================================================
-- USERS  (both candidates and employers live here, role flag splits them)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    full_name       VARCHAR(120)        NOT NULL,
    email           VARCHAR(150)        NOT NULL UNIQUE,
    password_hash   VARCHAR(255)        NOT NULL,
    role            VARCHAR(20)         NOT NULL CHECK (role IN ('candidate','employer')),
    avatar_initials VARCHAR(4)          DEFAULT NULL,
    created_at      TIMESTAMP           DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- CANDIDATE PROFILE
-- ============================================================
CREATE TABLE IF NOT EXISTS candidate_profiles (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL UNIQUE,
    headline        VARCHAR(150) DEFAULT NULL,
    location        VARCHAR(120) DEFAULT NULL,
    experience_yrs  DECIMAL(3,1) DEFAULT 0,
    tech_stack      VARCHAR(255) DEFAULT NULL,
    resume_score    INT DEFAULT 0,
    resume_filename VARCHAR(255) DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ============================================================
-- EMPLOYER / COMPANY PROFILE
-- ============================================================
CREATE TABLE IF NOT EXISTS company_profiles (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL UNIQUE,
    company_name    VARCHAR(150) DEFAULT NULL,
    industry        VARCHAR(120) DEFAULT NULL,
    website         VARCHAR(200) DEFAULT NULL,
    location        VARCHAR(120) DEFAULT NULL,
    about           TEXT DEFAULT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ============================================================
-- JOBS  (posted by employers)
-- ============================================================
CREATE TABLE IF NOT EXISTS jobs (
    id              SERIAL PRIMARY KEY,
    employer_id     INT NOT NULL,
    title           VARCHAR(150) NOT NULL,
    company_name    VARCHAR(150) NOT NULL,
    location        VARCHAR(120) NOT NULL,
    job_type        VARCHAR(50) DEFAULT 'Full-time' CHECK (job_type IN ('Full-time','Part-time','Internship','Contract','Remote')),
    tech_stack      VARCHAR(255) DEFAULT NULL,
    salary_min_lpa  DECIMAL(5,1) DEFAULT NULL,
    salary_max_lpa  DECIMAL(5,1) DEFAULT NULL,
    description     TEXT DEFAULT NULL,
    status          VARCHAR(20) DEFAULT 'Active' CHECK (status IN ('Active','Closed','Draft')),
    posted_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (employer_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ============================================================
-- APPLICATIONS  (candidate applies to a job)
-- ============================================================
CREATE TABLE IF NOT EXISTS applications (
    id              SERIAL PRIMARY KEY,
    job_id          INT NOT NULL,
    candidate_id    INT NOT NULL,
    match_score     INT DEFAULT 0,
    status          VARCHAR(50) DEFAULT 'Applied' CHECK (status IN ('Applied','Shortlisted','Interview','Rejected','Offered')),
    applied_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (job_id, candidate_id)
);

-- ============================================================
-- SAVED JOBS  (candidate bookmarks)
-- ============================================================
CREATE TABLE IF NOT EXISTS saved_jobs (
    id              SERIAL PRIMARY KEY,
    job_id          INT NOT NULL,
    candidate_id    INT NOT NULL,
    saved_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (job_id, candidate_id)
);

-- ============================================================
-- AI ASSESSMENT SESSIONS & RESPONSES
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    id VARCHAR(36) PRIMARY KEY,
    candidate_name VARCHAR(255),
    resume_skills JSONB,
    jd_text TEXT,
    jd_required_skills JSONB,
    jd_preferred_skills JSONB,
    skills_tested JSONB,
    current_round VARCHAR(20) DEFAULT 'basic',
    status VARCHAR(20) DEFAULT 'in_progress',
    basic_score FLOAT,
    medium_score FLOAT,
    advanced_score FLOAT,
    final_score FLOAT,
    verdict VARCHAR(50),
    missing_required_skills JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS responses (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL,
    round_name VARCHAR(20) NOT NULL,
    skill VARCHAR(255),
    question TEXT,
    options JSONB,
    correct_index INT,
    model_answer TEXT,
    rubric JSONB,
    candidate_answer TEXT,
    score FLOAT,
    justification TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_round ON responses (session_id, round_name);
