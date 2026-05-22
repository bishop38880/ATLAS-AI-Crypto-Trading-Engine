-- Phase 4: Chaos Drill Results Table
-- Stores results from weekly infrastructure chaos drills.

CREATE TABLE IF NOT EXISTS chaos_drill_results (
    drill_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scenario        TEXT NOT NULL,
    rto_ms          INTEGER NOT NULL,
    rpo_minutes     REAL NOT NULL,
    passed          BOOLEAN NOT NULL,
    logs            JSONB NOT NULL DEFAULT '{}',
    environment     TEXT NOT NULL DEFAULT 'staging',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chaos_drill_scenario
    ON chaos_drill_results (scenario);

CREATE INDEX IF NOT EXISTS idx_chaos_drill_run_at
    ON chaos_drill_results (run_at DESC);

-- Quality gate view: last 3 weeks
CREATE OR REPLACE VIEW chaos_drill_quality_gate AS
SELECT
    COUNT(*) AS total_drills,
    SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS passed_drills,
    COUNT(*) = SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS gate_open
FROM chaos_drill_results
WHERE run_at >= NOW() - INTERVAL '21 days';
