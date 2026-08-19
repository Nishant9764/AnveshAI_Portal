-- ============================================================
-- MIGRATION v2 — ATS scoring, resume bank, DB-backed Round 1
-- testing engine, anti-cheat / integrity tracking, email triggers.
-- Safe to re-run (all statements are idempotent).
-- ============================================================

-- ------------------------------------------------------------
-- RESUMES  (a candidate can keep several; pick one at apply time)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resumes (
    id              SERIAL PRIMARY KEY,
    candidate_id    INT NOT NULL,
    filename        VARCHAR(255) NOT NULL,
    file_path       VARCHAR(500) NOT NULL,
    raw_text        TEXT,
    masked_text     TEXT,
    skills          JSONB DEFAULT '[]',
    experience      JSONB DEFAULT '[]',
    projects        JSONB DEFAULT '[]',
    experience_yrs  DECIMAL(4,1) DEFAULT 0,
    name_found      VARCHAR(255),
    is_default      BOOLEAN DEFAULT FALSE,
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (candidate_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_resumes_candidate ON resumes (candidate_id);

-- ------------------------------------------------------------
-- QUESTIONS  (the DB-backed question bank, per given schema)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS questions (
    id                      SERIAL PRIMARY KEY,
    question_id             VARCHAR(64) UNIQUE,
    skill                   VARCHAR(100) NOT NULL,
    category                VARCHAR(100),
    topic                   VARCHAR(150),
    subtopic                VARCHAR(150),
    difficulty              VARCHAR(20) DEFAULT 'medium' CHECK (difficulty IN ('easy','medium','hard')),
    experience_level        VARCHAR(20) DEFAULT 'mid' CHECK (experience_level IN ('junior','mid','senior')),
    question_type           VARCHAR(20) DEFAULT 'mcq' CHECK (question_type IN ('mcq','code','true_false')),
    question                TEXT NOT NULL,
    code                    TEXT,
    options                 JSONB,
    correct_option          VARCHAR(10),
    correct_option_text     TEXT,
    explanation             TEXT,
    learning_objective      TEXT,
    estimated_time_seconds  INT DEFAULT 60,
    resume_relevance        TEXT,
    company_frequency       VARCHAR(20),
    tags                    JSONB DEFAULT '[]',
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_questions_skill ON questions (skill);
CREATE INDEX IF NOT EXISTS idx_questions_skill_diff ON questions (skill, difficulty);

-- ------------------------------------------------------------
-- JOBS — employer-set baseline + test-invite trigger policy
-- ------------------------------------------------------------
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS min_match_score INT DEFAULT 60;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS test_trigger_mode VARCHAR(20) DEFAULT 'manual'
    CHECK (test_trigger_mode IN ('immediate','12h','24h','manual'));

-- Cache the JD's parsed skills on the job itself — parsing costs one Gemini
-- call, and the JD text never changes between applicants, so re-parsing it
-- on every single application (as the original apply flow did) is pure
-- wasted spend. Parse once at job creation, reuse on every apply.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS jd_required_skills JSONB DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS jd_preferred_skills JSONB DEFAULT '[]';

-- ------------------------------------------------------------
-- CANDIDATE PROFILES — interests / preferred roles for feed personalization
-- + standalone resume-quality score breakdown (no JD involved — the
-- other scorer, ats_engine.py, handles the JD-matched one)
-- ------------------------------------------------------------
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS interests VARCHAR(255) DEFAULT NULL;
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS preferred_locations VARCHAR(255) DEFAULT NULL;
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS preferred_job_types VARCHAR(255) DEFAULT NULL;
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS resume_id INT REFERENCES resumes(id) ON DELETE SET NULL;
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS resume_score_breakdown JSONB DEFAULT '{}';
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS resume_strengths JSONB DEFAULT '[]';
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS resume_weaknesses JSONB DEFAULT '[]';
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS resume_suggested_skills JSONB DEFAULT '[]';
ALTER TABLE candidate_profiles ADD COLUMN IF NOT EXISTS resume_score_summary TEXT;

-- ------------------------------------------------------------
-- APPLICATIONS — real ATS scoring + email-trigger state machine
-- ------------------------------------------------------------
ALTER TABLE applications ADD COLUMN IF NOT EXISTS resume_id INT REFERENCES resumes(id) ON DELETE SET NULL;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS technical_match_score INT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS experience_match_score INT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS soft_skills_score INT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS impact_score INT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS matched_skills JSONB DEFAULT '[]';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS missing_skills JSONB DEFAULT '[]';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS red_flags JSONB DEFAULT '[]';
ALTER TABLE applications ADD COLUMN IF NOT EXISTS ats_summary TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS passed_ats BOOLEAN DEFAULT FALSE;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round1_session_id VARCHAR(36);
ALTER TABLE applications ADD COLUMN IF NOT EXISTS invite_status VARCHAR(20) DEFAULT 'pending'
    CHECK (invite_status IN ('pending','rejected','scheduled','sent','manual_hold','withdrawn'));
ALTER TABLE applications ADD COLUMN IF NOT EXISTS invite_scheduled_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS invite_sent_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS rejected_reason VARCHAR(255);
ALTER TABLE applications ADD COLUMN IF NOT EXISTS rejection_emailed_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round1_verdict VARCHAR(20);
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round1_score FLOAT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round1_completed_at TIMESTAMP;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round2_unlocked BOOLEAN DEFAULT FALSE;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round3_unlocked BOOLEAN DEFAULT FALSE;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round2_score FLOAT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS round3_score FLOAT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS integrity_score INT;

-- ------------------------------------------------------------
-- SESSIONS — extend the existing assessment session table to carry
-- Round 1/2/3, anti-cheat state, and email-invite metadata.
-- ------------------------------------------------------------
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS application_id INT REFERENCES applications(id) ON DELETE CASCADE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS candidate_id INT REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS job_id INT REFERENCES jobs(id) ON DELETE CASCADE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS invite_token VARCHAR(64) UNIQUE;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_checked VARCHAR(20);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS keystroke_baseline JSONB;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS warnings_count INT DEFAULT 0;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS integrity_score INT DEFAULT 100;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS integrity_flags JSONB DEFAULT '[]';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mcq_total INT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mcq_correct INT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mcq_pct FLOAT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS part_b_score FLOAT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS round1_verdict VARCHAR(20);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS round1_result VARCHAR(20);
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS round2_score FLOAT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS round3_score FLOAT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS started_at TIMESTAMP;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

-- responses table: keystroke + timing + per-question grading metadata
ALTER TABLE responses ADD COLUMN IF NOT EXISTS time_taken_seconds INT;
ALTER TABLE responses ADD COLUMN IF NOT EXISTS is_correct BOOLEAN;
ALTER TABLE responses ADD COLUMN IF NOT EXISTS paste_detected BOOLEAN DEFAULT FALSE;
ALTER TABLE responses ADD COLUMN IF NOT EXISTS keystroke_metrics JSONB;
ALTER TABLE responses ADD COLUMN IF NOT EXISTS question_bank_id INT REFERENCES questions(id) ON DELETE SET NULL;

-- ------------------------------------------------------------
-- INTEGRITY EVENTS — every anti-cheat signal fired during a test
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS integrity_events (
    id          SERIAL PRIMARY KEY,
    session_id  VARCHAR(36) NOT NULL,
    event_type  VARCHAR(30) NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_integrity_session ON integrity_events (session_id);

-- ------------------------------------------------------------
-- TEST STATE — server-side scratch state for an in-progress Round 1/2/3
-- session (question banks + current index). NOT stored in the Flask
-- cookie session: question banks (10-15 MCQs + full text/options, or
-- Gemini-generated subjective questions) comfortably exceed the ~4KB
-- signed-cookie limit, and cookie state also breaks across devices/tabs.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS test_state (
    session_id      VARCHAR(36) PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    mcq_bank        JSONB DEFAULT '[]',
    mcq_idx         INT DEFAULT 0,
    mcq_extended    BOOLEAN DEFAULT FALSE,
    mcq_seniority   VARCHAR(20) DEFAULT 'mid',
    partb_bank      JSONB DEFAULT '[]',
    partb_idx       INT DEFAULT 0,
    round2_bank     JSONB DEFAULT '[]',
    round2_idx      INT DEFAULT 0,
    round3_bank     JSONB DEFAULT '[]',
    round3_idx      INT DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- widen a couple of columns whose original size was tuned for the old
-- basic/medium/advanced flow and don't fit the new status vocabulary
ALTER TABLE sessions ALTER COLUMN status TYPE VARCHAR(30);
ALTER TABLE sessions ALTER COLUMN current_round TYPE VARCHAR(30);

-- ------------------------------------------------------------
-- EMAIL LOG — audit trail for automated emails (SMTP send record)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_log (
    id              SERIAL PRIMARY KEY,
    application_id  INT REFERENCES applications(id) ON DELETE CASCADE,
    email_type      VARCHAR(30) NOT NULL,
    to_address      VARCHAR(255),
    status          VARCHAR(20),
    error           TEXT,
    sent_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------------
-- WIDEN COLUMNS that were VARCHAR(255) but now hold variable-length,
-- AI-derived or joined strings (a resume with 20+ skills, or a JD with
-- a long skills list, easily exceeds 255 chars and was throwing
-- StringDataRightTruncation). Safe to re-run.
-- ------------------------------------------------------------
ALTER TABLE candidate_profiles ALTER COLUMN tech_stack TYPE TEXT;
ALTER TABLE jobs ALTER COLUMN tech_stack TYPE TEXT;
ALTER TABLE resumes ALTER COLUMN filename TYPE TEXT;
ALTER TABLE resumes ALTER COLUMN name_found TYPE TEXT;
ALTER TABLE applications ALTER COLUMN rejected_reason TYPE TEXT;

-- ------------------------------------------------------------
-- ASYNC SCREENING — apply now returns instantly; the actual Gemini
-- scoring runs in a background job. This tracks where a given
-- application is in that pipeline.
-- ------------------------------------------------------------
ALTER TABLE applications ADD COLUMN IF NOT EXISTS screening_status VARCHAR(20) DEFAULT 'pending'
    CHECK (screening_status IN ('pending', 'processing', 'done', 'failed'));
ALTER TABLE applications ADD COLUMN IF NOT EXISTS screening_error TEXT;

-- ------------------------------------------------------------
-- COMPANY PROFILE — richer employer branding (was just 5 fields:
-- name/industry/website/location/about). Candidates deciding whether to
-- take a 20-minute assessment want to see who they're dealing with.
-- ------------------------------------------------------------
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS company_size VARCHAR(30);
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS founded_year INT;
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS logo_path TEXT;
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS linkedin_url TEXT;
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS twitter_url TEXT;
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS benefits TEXT;
ALTER TABLE company_profiles ADD COLUMN IF NOT EXISTS tech_stack TEXT;

-- ------------------------------------------------------------
-- RELAX the `questions` table's vocabulary assumptions. It was created
-- with CHECK constraints assuming difficulty/experience_level/question_type
-- values in a specific lowercase set — any real-world/custom dataset that
-- uses different casing or a slightly different vocabulary (e.g. "Easy",
-- "MCQ", "Intermediate") had every one of those rows silently REJECTED at
-- insert time. Also widening question_id, which was too narrow for some
-- externally-generated question IDs. Safe to re-run.
-- ------------------------------------------------------------
ALTER TABLE questions DROP CONSTRAINT IF EXISTS questions_difficulty_check;
ALTER TABLE questions DROP CONSTRAINT IF EXISTS questions_experience_level_check;
ALTER TABLE questions DROP CONSTRAINT IF EXISTS questions_question_type_check;
ALTER TABLE questions ALTER COLUMN question_id TYPE VARCHAR(150);
ALTER TABLE questions ALTER COLUMN difficulty TYPE VARCHAR(50);
ALTER TABLE questions ALTER COLUMN experience_level TYPE VARCHAR(50);
ALTER TABLE questions ALTER COLUMN question_type TYPE VARCHAR(50);

-- Normalize whatever is already in there to the lowercase vocabulary the
-- app's difficulty-calibration logic (question_bank.py) expects, so
-- existing rows start matching immediately without a re-import.
UPDATE questions SET difficulty = lower(trim(difficulty)) WHERE difficulty IS NOT NULL;
UPDATE questions SET experience_level = lower(trim(experience_level)) WHERE experience_level IS NOT NULL;
UPDATE questions SET question_type = lower(trim(question_type)) WHERE question_type IS NOT NULL;
UPDATE questions SET question_type = 'mcq' WHERE question_type IN ('multiple_choice', 'multiple choice', 'mcqs');
UPDATE questions SET difficulty = 'medium' WHERE difficulty IN ('intermediate', 'moderate');
UPDATE questions SET difficulty = 'easy' WHERE difficulty IN ('beginner', 'basic');
UPDATE questions SET difficulty = 'hard' WHERE difficulty IN ('advanced', 'expert');