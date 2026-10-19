CREATE TABLE template_import_previews (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    payload_json TEXT NOT NULL,
    diff_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'consumed', 'expired')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_template_import_previews_status_expiry
ON template_import_previews(status, expires_at);
