-- ============================================================
-- GovtPrep v2 Schema
-- ============================================================

-- Drop existing
DROP TABLE IF EXISTS referrals CASCADE;
DROP TABLE IF EXISTS usage_logs CASCADE;
DROP TABLE IF EXISTS subscriptions CASCADE;
DROP TABLE IF EXISTS question_translations CASCADE;
DROP TABLE IF EXISTS questions CASCADE;
DROP TABLE IF EXISTS leaderboard CASCADE;
DROP TABLE IF EXISTS attempts CASCADE;
DROP TABLE IF EXISTS topic_stats CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;

-- ============================================================
-- SUBSCRIPTIONS AND TIERS
-- ============================================================

CREATE TABLE subscriptions (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id         TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'trial'
                    CHECK (tier IN ('trial','free','tier1','tier2','tier3')),
    tier_expires_at TIMESTAMPTZ,
    trial_used      BOOLEAN DEFAULT FALSE,
    gpay_ref        TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- tier1 = 399/yr, 1hr/day, English only
-- tier2 = 699/yr, 2hr/day, all languages
-- tier3 = 1999/yr, unlimited, AI mentor
-- trial = 7 days full tier3 access

CREATE INDEX idx_sub_user ON subscriptions(user_id);

-- ============================================================
-- REFERRALS
-- ============================================================

CREATE TABLE referrals (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    referrer_id     TEXT NOT NULL,
    referee_id      TEXT NOT NULL UNIQUE,
    referral_code   TEXT NOT NULL,
    rewarded_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ref_code    ON referrals(referral_code);
CREATE INDEX idx_ref_referrer ON referrals(referrer_id);

-- ============================================================
-- DAILY USAGE TRACKING
-- ============================================================

CREATE TABLE usage_logs (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id         TEXT NOT NULL,
    date            DATE NOT NULL DEFAULT CURRENT_DATE,
    seconds_used    INTEGER DEFAULT 0,
    UNIQUE (user_id, date)
);

CREATE INDEX idx_usage_user_date ON usage_logs(user_id, date);

-- ============================================================
-- QUESTION BANK
-- ============================================================

CREATE TABLE questions (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    exam_id         TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    topic_id        TEXT NOT NULL,
    difficulty      INTEGER CHECK (difficulty BETWEEN 1 AND 5),
    question_en     TEXT NOT NULL,
    option_a_en     TEXT NOT NULL,
    option_b_en     TEXT NOT NULL,
    option_c_en     TEXT NOT NULL,
    option_d_en     TEXT NOT NULL,
    correct_answer  TEXT NOT NULL CHECK (correct_answer IN ('A','B','C','D')),
    explanation_en  TEXT NOT NULL,
    is_mock_test    BOOLEAN DEFAULT FALSE,
    mock_test_id    TEXT,
    is_verified     BOOLEAN DEFAULT FALSE,
    creator_output  TEXT,
    critic_output   TEXT,
    auditor_output  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_q_exam    ON questions(exam_id);
CREATE INDEX idx_q_subject ON questions(subject_id);
CREATE INDEX idx_q_topic   ON questions(topic_id);
CREATE INDEX idx_q_mock    ON questions(mock_test_id);
CREATE INDEX idx_q_verified ON questions(is_verified);

-- Translations stored as separate rows per language
CREATE TABLE question_translations (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    question_id     TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    language        TEXT NOT NULL
                    CHECK (language IN ('hi','bn','pa','ta','te','kn','ml','or')),
    question_text   TEXT NOT NULL,
    option_a        TEXT NOT NULL,
    option_b        TEXT NOT NULL,
    option_c        TEXT NOT NULL,
    option_d        TEXT NOT NULL,
    explanation     TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (question_id, language)
);

CREATE INDEX idx_trans_q    ON question_translations(question_id);
CREATE INDEX idx_trans_lang ON question_translations(language);

-- Mock test grouping
CREATE TABLE mock_tests (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    exam_id         TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    question_count  INTEGER DEFAULT 10,
    time_mins       INTEGER DEFAULT 15,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SESSIONS
-- ============================================================

CREATE TABLE sessions (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id          TEXT NOT NULL,
    exam_id          TEXT NOT NULL,
    subject_id       TEXT NOT NULL,
    topic_id         TEXT,
    mock_test_id     TEXT,
    mode             TEXT DEFAULT 'practice' CHECK (mode IN ('practice','mock')),
    language         TEXT DEFAULT 'en',
    started_at       TIMESTAMPTZ DEFAULT NOW(),
    ended_at         TIMESTAMPTZ,
    questions_total  INTEGER DEFAULT 0,
    questions_correct INTEGER DEFAULT 0,
    score_pct        FLOAT,
    duration_secs    INTEGER,
    completed        BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_sess_user ON sessions(user_id);
CREATE INDEX idx_sess_exam ON sessions(exam_id);

CREATE TABLE attempts (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL,
    question_id     TEXT REFERENCES questions(id),
    user_answer     TEXT,
    is_correct      BOOLEAN,
    time_secs       FLOAT,
    mentor_feedback TEXT,
    attempted_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_att_session  ON attempts(session_id);
CREATE INDEX idx_att_user     ON attempts(user_id);
CREATE INDEX idx_att_question ON attempts(question_id);

-- ============================================================
-- TOPIC STATS AND LEADERBOARD
-- ============================================================

CREATE TABLE topic_stats (
    id               TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id          TEXT NOT NULL,
    exam_id          TEXT NOT NULL,
    subject_id       TEXT NOT NULL,
    topic_id         TEXT NOT NULL,
    attempts_total   INTEGER DEFAULT 0,
    attempts_correct INTEGER DEFAULT 0,
    accuracy_pct     FLOAT DEFAULT 0,
    avg_time_secs    FLOAT,
    last_attempted   TIMESTAMPTZ,
    UNIQUE (user_id, exam_id, subject_id, topic_id)
);

CREATE TABLE leaderboard (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    user_id         TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    exam_id         TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    topic_id        TEXT,
    mock_test_id    TEXT,
    mode            TEXT DEFAULT 'practice',
    score_pct       FLOAT NOT NULL,
    correct         INTEGER,
    total           INTEGER,
    avg_time_secs   FLOAT,
    percentile      FLOAT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_lb_exam    ON leaderboard(exam_id, subject_id);
CREATE INDEX idx_lb_score   ON leaderboard(score_pct DESC);
CREATE INDEX idx_lb_mock    ON leaderboard(mock_test_id);

-- ============================================================
-- PERCENTILE UPDATE (run every 6 hours via Supabase cron)
-- ============================================================

CREATE OR REPLACE FUNCTION update_percentiles()
RETURNS void AS $$
BEGIN
    UPDATE leaderboard l
    SET percentile = (
        SELECT ROUND(
            100.0 * COUNT(l2.id) / NULLIF(COUNT(*) OVER (
                PARTITION BY l.exam_id, l.subject_id, l.topic_id
            ), 0), 1
        )
        FROM leaderboard l2
        WHERE l2.exam_id    = l.exam_id
          AND l2.subject_id = l.subject_id
          AND l2.topic_id   IS NOT DISTINCT FROM l.topic_id
          AND l2.score_pct  <= l.score_pct
    );
END;
$$ LANGUAGE plpgsql;
