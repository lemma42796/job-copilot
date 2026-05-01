-- JobCopilot Postgres init
-- Runs once when the data dir is empty (Postgres official image convention).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- pgmq is added in M2 (see ROADMAP) — the M0 image (pgvector/pgvector:pg16)
-- does not bundle it. When queues are first needed we'll switch to a custom
-- Dockerfile that adds pgmq on top of pgvector.

DO $$
BEGIN
    RAISE NOTICE 'JobCopilot extensions enabled: %',
        (SELECT string_agg(extname, ', ') FROM pg_extension);
END $$;
