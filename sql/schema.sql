-- StarSage database schema.
-- The knowledge base is NOT stored here — it lives in knowledge_base.md.
-- This schema covers users, conversations, the prompt/config version store,
-- and detailed per-step pipeline run logging (including token usage and cost).

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Users ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    birth_data  TEXT,           -- JSON blob of birth details
    astro_path  TEXT,           -- path to complete_astro_data.json
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Conversations & messages --------------------------------------------------

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(user_id),
    title           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pipeline run logging ------------------------------------------------------

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id),
    user_id         TEXT REFERENCES users(user_id),
    user_message    TEXT,
    final_response  TEXT,
    classification  TEXT,
    kb_used         INTEGER NOT NULL DEFAULT 0,
    fast_model      TEXT,
    reason_model    TEXT,
    prompt_hash     TEXT,
    config_hash     TEXT,
    total_tokens_in  INTEGER NOT NULL DEFAULT 0,
    total_tokens_out INTEGER NOT NULL DEFAULT 0,
    total_cost_usd   REAL NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT DEFAULT 'running'
);

-- One row per LLM call (or notable step) in a run, with full detail + cost.
CREATE TABLE IF NOT EXISTS pipeline_steps (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL REFERENCES pipeline_runs(id),
    step_index    INTEGER NOT NULL DEFAULT 0,
    step_name     TEXT NOT NULL,
    prompt_module TEXT,            -- which prompt module(s) drove this call
    model         TEXT,
    request_text  TEXT,            -- full constructed request (system + user)
    input_text    TEXT,            -- the salient input handed to this step
    output_text   TEXT,            -- full response returned by the model
    tokens_in     INTEGER DEFAULT 0,
    tokens_out    INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    duration_ms   INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS answer_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES pipeline_runs(id),
    version     INTEGER NOT NULL DEFAULT 1,
    stage       TEXT NOT NULL,   -- 'draft', 'critic', 'rewrite', 'format', 'final'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Prompt modules (versioned) ------------------------------------------------

CREATE TABLE IF NOT EXISTS prompt_modules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_key  TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    category    TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active_version_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id   INTEGER NOT NULL REFERENCES prompt_modules(id),
    version     INTEGER NOT NULL DEFAULT 1,
    content     TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Config options (versioned) ------------------------------------------------

CREATE TABLE IF NOT EXISTS config_options (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_key  TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT,
    value_type  TEXT NOT NULL DEFAULT 'string',
    active_version_id INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS config_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER NOT NULL REFERENCES config_options(id),
    version     INTEGER NOT NULL DEFAULT 1,
    value       TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Snapshots of active prompt/config state -----------------------------------

CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    comment         TEXT,
    prompt_hash     TEXT,
    config_hash     TEXT,
    active_modules  TEXT,   -- JSON list of {module_key, version_id}
    active_configs  TEXT,   -- JSON list of {option_key, version_id}
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Test-version answer feedback (collected under each bot answer) -------------

CREATE TABLE IF NOT EXISTS answer_feedback (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    user_id               TEXT,
    username              TEXT,
    session_id            TEXT,
    conversation_id       TEXT,
    user_message_id       INTEGER,
    bot_message_id        INTEGER,
    user_input            TEXT NOT NULL,
    bot_answer            TEXT NOT NULL,
    user_feedback         TEXT NOT NULL,
    answer_style          TEXT,
    answer_depth          TEXT,
    classifier_intent     TEXT,
    classifier_life_area  TEXT,
    emotional_risk        TEXT,
    model_used            TEXT,
    app_version           TEXT,
    metadata_json         TEXT
);
