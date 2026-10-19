CREATE TABLE project_delete_previews (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64),
    confirmation_text TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'consumed', 'expired')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_project_delete_previews_version_status
ON project_delete_previews(project_version_id, status, expires_at);
