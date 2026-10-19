CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    project_code TEXT NOT NULL UNIQUE,
    project_name TEXT NOT NULL
);

CREATE TABLE project_versions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    imported_at TEXT NOT NULL,
    config_item_number TEXT NOT NULL DEFAULT '0.0',
    project_leader TEXT,
    qa_person TEXT,
    raw_metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(project_id, version_no)
);

CREATE TABLE phases (
    id INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    wbs TEXT NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    sort_order INTEGER NOT NULL,
    UNIQUE(project_version_id, wbs)
);

CREATE TABLE tracking_nodes (
    id INTEGER PRIMARY KEY,
    phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    source_wbs TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK (node_type IN ('双周', '阶段')),
    node_date TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    UNIQUE(phase_id, source_wbs)
);

CREATE TABLE config_items (
    id INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    source_seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    identifier TEXT,
    version TEXT,
    item_type TEXT,
    phase_wbs TEXT,
    review_date TEXT,
    UNIQUE(project_version_id, source_seq)
);

CREATE TABLE quality_activities (
    id INTEGER PRIMARY KEY,
    phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    source_wbs TEXT NOT NULL,
    name TEXT NOT NULL,
    audit_type TEXT NOT NULL CHECK (audit_type IN ('product', 'process', 'company')),
    start_date TEXT,
    end_date TEXT,
    auditor TEXT,
    linked_items_json TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL,
    UNIQUE(phase_id, source_wbs)
);

CREATE TABLE checklist_templates (
    id INTEGER PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    audit_kind TEXT NOT NULL CHECK (audit_kind IN ('product', 'process')),
    builtin_version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    user_modified INTEGER NOT NULL DEFAULT 0 CHECK (user_modified IN (0, 1)),
    source_sheet TEXT,
    display_order INTEGER NOT NULL
);

CREATE TABLE checklist_groups (
    id INTEGER PRIMARY KEY,
    checklist_template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE RESTRICT,
    stable_key TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    user_modified INTEGER NOT NULL DEFAULT 0 CHECK (user_modified IN (0, 1)),
    display_order INTEGER NOT NULL,
    UNIQUE(checklist_template_id, stable_key)
);

CREATE TABLE checklist_items (
    id INTEGER PRIMARY KEY,
    checklist_group_id INTEGER NOT NULL REFERENCES checklist_groups(id) ON DELETE RESTRICT,
    stable_key TEXT NOT NULL,
    display_no TEXT NOT NULL,
    content TEXT NOT NULL,
    starred INTEGER NOT NULL DEFAULT 0 CHECK (starred IN (0, 1)),
    version TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    user_modified INTEGER NOT NULL DEFAULT 0 CHECK (user_modified IN (0, 1)),
    display_order INTEGER NOT NULL,
    UNIQUE(checklist_group_id, stable_key)
);

CREATE TABLE issue_templates (
    id INTEGER PRIMARY KEY,
    checklist_item_id INTEGER NOT NULL REFERENCES checklist_items(id) ON DELETE RESTRICT,
    stable_key TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    corrective_suggestion TEXT NOT NULL,
    default_severity TEXT NOT NULL CHECK (default_severity IN ('轻微', '一般', '严重')),
    placeholders_json TEXT NOT NULL DEFAULT '[]',
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    user_modified INTEGER NOT NULL DEFAULT 0 CHECK (user_modified IN (0, 1)),
    display_order INTEGER NOT NULL,
    UNIQUE(checklist_item_id, stable_key)
);

CREATE UNIQUE INDEX uq_issue_templates_one_enabled_default
ON issue_templates(checklist_item_id)
WHERE enabled = 1 AND is_default = 1;

CREATE TABLE mapping_rules (
    id INTEGER PRIMARY KEY,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('product', 'process', 'company')),
    match_kind TEXT NOT NULL CHECK (match_kind IN ('exact', 'alias', 'normalized')),
    pattern TEXT NOT NULL,
    checklist_template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE RESTRICT,
    priority INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL CHECK (source IN ('builtin', 'user')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    UNIQUE(target_kind, match_kind, pattern, source)
);

CREATE TABLE seed_imports (
    id INTEGER PRIMARY KEY,
    seed_version TEXT NOT NULL UNIQUE,
    seed_sha256 TEXT NOT NULL CHECK (length(seed_sha256) = 64),
    applied_at TEXT NOT NULL
);

CREATE TABLE audit_instances (
    id INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    activity_id INTEGER NOT NULL REFERENCES quality_activities(id) ON DELETE CASCADE,
    config_item_id INTEGER REFERENCES config_items(id) ON DELETE RESTRICT,
    audit_kind TEXT NOT NULL CHECK (audit_kind IN ('product', 'process', 'company')),
    report_scope TEXT NOT NULL CHECK (report_scope IN ('phase', 'company')),
    process_code TEXT,
    checklist_template_id INTEGER NOT NULL REFERENCES checklist_templates(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    audit_date TEXT NOT NULL,
    tracking_node_id INTEGER REFERENCES tracking_nodes(id) ON DELETE RESTRICT,
    auditor TEXT,
    confirmation_state TEXT NOT NULL DEFAULT 'draft' CHECK (confirmation_state IN ('draft', 'confirmed')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_version_id, sequence_no),
    CHECK ((audit_kind = 'product' AND config_item_id IS NOT NULL) OR
           (audit_kind IN ('process', 'company') AND config_item_id IS NULL)),
    CHECK ((audit_kind = 'company' AND report_scope = 'company') OR
           (audit_kind != 'company' AND report_scope = 'phase'))
);

CREATE TABLE checklist_instance_groups (
    id INTEGER PRIMARY KEY,
    audit_instance_id INTEGER NOT NULL REFERENCES audit_instances(id) ON DELETE CASCADE,
    source_group_id INTEGER REFERENCES checklist_groups(id) ON DELETE SET NULL,
    stable_key TEXT NOT NULL,
    name_snapshot TEXT NOT NULL,
    version_snapshot TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    UNIQUE(audit_instance_id, stable_key)
);

CREATE TABLE checklist_instance_items (
    id INTEGER PRIMARY KEY,
    instance_group_id INTEGER NOT NULL REFERENCES checklist_instance_groups(id) ON DELETE CASCADE,
    source_item_id INTEGER REFERENCES checklist_items(id) ON DELETE SET NULL,
    stable_key TEXT NOT NULL,
    display_no_snapshot TEXT NOT NULL,
    content_snapshot TEXT NOT NULL,
    starred_snapshot INTEGER NOT NULL CHECK (starred_snapshot IN (0, 1)),
    conclusion TEXT CHECK (conclusion IN ('符合', '不符合', '不适用')),
    display_order INTEGER NOT NULL,
    UNIQUE(instance_group_id, stable_key)
);

CREATE TABLE checklist_instance_issue_templates (
    id INTEGER PRIMARY KEY,
    checklist_instance_item_id INTEGER NOT NULL REFERENCES checklist_instance_items(id) ON DELETE CASCADE,
    source_issue_template_id INTEGER REFERENCES issue_templates(id) ON DELETE SET NULL,
    stable_key TEXT NOT NULL,
    template_version_snapshot TEXT NOT NULL,
    title_snapshot TEXT NOT NULL,
    description_snapshot TEXT NOT NULL,
    corrective_suggestion_snapshot TEXT NOT NULL,
    default_severity_snapshot TEXT NOT NULL CHECK (default_severity_snapshot IN ('轻微', '一般', '严重')),
    placeholders_snapshot_json TEXT NOT NULL DEFAULT '[]',
    is_default_snapshot INTEGER NOT NULL CHECK (is_default_snapshot IN (0, 1)),
    display_order INTEGER NOT NULL,
    UNIQUE(checklist_instance_item_id, stable_key)
);

CREATE TABLE nonconformities (
    id INTEGER PRIMARY KEY,
    checklist_instance_item_id INTEGER NOT NULL REFERENCES checklist_instance_items(id) ON DELETE CASCADE,
    instance_issue_template_id INTEGER REFERENCES checklist_instance_issue_templates(id) ON DELETE SET NULL,
    template_version_snapshot TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('轻微', '一般', '严重')),
    responsible_person TEXT,
    submitted_date TEXT,
    planned_close_date TEXT,
    corrective_action TEXT,
    actual_close_date TEXT,
    verification_date TEXT,
    verification_conclusion TEXT,
    suggested_closed INTEGER NOT NULL DEFAULT 0 CHECK (suggested_closed IN (0, 1)),
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK (confirmed IN (0, 1))
);

CREATE TABLE project_version_settings (
    project_version_id INTEGER PRIMARY KEY REFERENCES project_versions(id) ON DELETE CASCADE,
    config_item_number TEXT NOT NULL DEFAULT '0.0',
    trend_conclusion_override TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE phase_detection_overrides (
    id INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    source_wbs TEXT NOT NULL,
    detected_as_phase INTEGER NOT NULL CHECK (detected_as_phase IN (0, 1)),
    user_selected_as_phase INTEGER NOT NULL CHECK (user_selected_as_phase IN (0, 1)),
    source_reason TEXT,
    UNIQUE(project_version_id, source_wbs)
);

CREATE TABLE import_previews (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64),
    temp_relative_path TEXT NOT NULL,
    parsed_payload_json TEXT NOT NULL,
    overrides_json TEXT NOT NULL DEFAULT '{}',
    diagnostics_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pending', 'consumed', 'expired')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE export_jobs (
    id INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    progress_step TEXT,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    output_dir TEXT,
    error_json TEXT,
    UNIQUE(project_version_id, idempotency_key)
);

CREATE TABLE export_artifacts (
    id INTEGER PRIMARY KEY,
    export_job_id INTEGER NOT NULL REFERENCES export_jobs(id) ON DELETE CASCADE,
    artifact_kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    UNIQUE(export_job_id, filename)
);

CREATE INDEX idx_project_versions_project ON project_versions(project_id, version_no);
CREATE INDEX idx_phases_version_sort ON phases(project_version_id, sort_order);
CREATE INDEX idx_tracking_nodes_phase_date ON tracking_nodes(phase_id, node_date, sort_order);
CREATE INDEX idx_config_items_version_seq ON config_items(project_version_id, source_seq);
CREATE INDEX idx_quality_activities_phase_sort ON quality_activities(phase_id, sort_order);
CREATE INDEX idx_audit_instances_version_sequence ON audit_instances(project_version_id, sequence_no);
CREATE INDEX idx_audit_instances_phase_scope ON audit_instances(phase_id, report_scope, audit_date);
CREATE INDEX idx_export_jobs_version_status ON export_jobs(project_version_id, status);
