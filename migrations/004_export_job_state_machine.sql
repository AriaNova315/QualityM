DROP INDEX IF EXISTS idx_export_jobs_version_status;

CREATE TABLE export_jobs_next (
    id INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'snapshotting', 'building', 'validating', 'completed', 'failed'
    )),
    progress_step TEXT,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    output_dir TEXT,
    error_json TEXT,
    UNIQUE(project_version_id, idempotency_key)
);

INSERT INTO export_jobs_next (
    id, project_version_id, idempotency_key, status, progress_step,
    requested_at, started_at, finished_at, output_dir, error_json
)
SELECT
    id, project_version_id, idempotency_key,
    CASE status WHEN 'running' THEN 'building' ELSE status END,
    progress_step, requested_at, started_at, finished_at, output_dir, error_json
FROM export_jobs;

CREATE TABLE export_artifacts_next (
    id INTEGER PRIMARY KEY,
    export_job_id INTEGER NOT NULL REFERENCES export_jobs_next(id) ON DELETE CASCADE,
    artifact_kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    UNIQUE(export_job_id, filename)
);

INSERT INTO export_artifacts_next (id, export_job_id, artifact_kind, filename, sha256, byte_size)
SELECT id, export_job_id, artifact_kind, filename, sha256, byte_size
FROM export_artifacts;

DROP TABLE export_artifacts;
DROP TABLE export_jobs;
ALTER TABLE export_jobs_next RENAME TO export_jobs;
ALTER TABLE export_artifacts_next RENAME TO export_artifacts;

CREATE INDEX idx_export_jobs_version_status ON export_jobs(project_version_id, status);
CREATE UNIQUE INDEX idx_export_jobs_one_active_per_version
ON export_jobs(project_version_id)
WHERE status IN ('queued', 'snapshotting', 'building', 'validating');
