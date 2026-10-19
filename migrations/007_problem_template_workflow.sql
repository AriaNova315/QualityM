ALTER TABLE checklist_items
ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0);

ALTER TABLE issue_templates
ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0);

ALTER TABLE audit_instances
ADD COLUMN snapshot_schema_version INTEGER NOT NULL DEFAULT 1
CHECK (snapshot_schema_version > 0);

ALTER TABLE audit_instances
ADD COLUMN user_edited INTEGER NOT NULL DEFAULT 0
CHECK (user_edited IN (0, 1));

UPDATE audit_instances
SET user_edited = 1
WHERE revision > 1 OR confirmation_state = 'confirmed';

UPDATE issue_templates SET is_default = 0 WHERE is_default <> 0;

DROP INDEX uq_issue_templates_one_enabled_default;
