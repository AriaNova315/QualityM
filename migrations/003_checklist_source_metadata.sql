ALTER TABLE checklist_templates ADD COLUMN source_workbook TEXT;
ALTER TABLE checklist_templates ADD COLUMN source_max_row INTEGER;
ALTER TABLE checklist_templates ADD COLUMN source_max_column INTEGER;
ALTER TABLE checklist_templates ADD COLUMN source_print_area TEXT;
ALTER TABLE checklist_templates ADD COLUMN source_freeze_panes TEXT;

ALTER TABLE checklist_groups ADD COLUMN display_heading INTEGER NOT NULL DEFAULT 0
    CHECK (display_heading IN (0, 1));
ALTER TABLE checklist_groups ADD COLUMN source_row INTEGER;

ALTER TABLE checklist_items ADD COLUMN source_row INTEGER;
ALTER TABLE checklist_items ADD COLUMN source_cell TEXT;

