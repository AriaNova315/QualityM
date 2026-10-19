UPDATE checklist_instance_items
SET conclusion = '符合'
WHERE conclusion IS NULL;
