from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.database import connect, transaction


STABLE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
VALID_AUDIT_KINDS = {"product", "process"}
VALID_SEVERITIES = {"轻微", "一般", "严重"}
ALLOWED_PLACEHOLDERS = {"项目名称", "阶段名称", "工作产品名称", "审核日期", "责任人"}
MECHANICAL_TEMPLATE_MARKERS = (
    "检查项要求未满足",
    "未能确认“",
    "现有记录不足以证明",
)


class SeedValidationError(ValueError):
    """Raised when seed content is structurally invalid or ambiguous."""


@dataclass(frozen=True, slots=True)
class SeedImportResult:
    seed_version: str
    checksum: str
    applied: bool
    preserved_user_modified: int = 0


@dataclass(frozen=True, slots=True)
class SeedUpgradeConflict:
    entity_type: str
    stable_key: str


@dataclass(frozen=True, slots=True)
class SeedUpgradePreview:
    from_version: str | None
    to_version: str
    conflicts: tuple[SeedUpgradeConflict, ...]


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _normalized_similarity_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def analyze_seed_quality(payload: dict[str, Any]) -> dict[str, Any]:
    """Return stable content-quality findings without mutating the seed payload."""
    findings: list[dict[str, str]] = []
    template_counts: Counter[int] = Counter()
    family_counts: Counter[str] = Counter()
    item_count = 0
    for checklist in payload.get("checklists", []):
        for group in checklist.get("groups", []):
            for item in group.get("items", []):
                item_count += 1
                item_key = item.get("stable_key", "unknown")
                issues = item.get("issue_templates", [])
                template_counts[len(issues)] += 1
                if not 2 <= len(issues) <= 6:
                    findings.append(
                        {
                            "code": "SEED_TEMPLATE_COUNT_OUT_OF_RANGE",
                            "location": item_key,
                            "message": f"expected 2..6 templates, found {len(issues)}",
                        }
                    )
                seen_texts: list[tuple[str, str]] = []
                for issue in issues:
                    issue_key = issue.get("stable_key", "unknown")
                    family_counts[issue_key.rsplit(".", maxsplit=1)[-1]] += 1
                    combined = " ".join(
                        str(issue.get(field, ""))
                        for field in ("title", "description", "corrective_suggestion")
                    )
                    if any(marker in combined for marker in MECHANICAL_TEMPLATE_MARKERS):
                        findings.append(
                            {
                                "code": "SEED_MECHANICAL_NEGATION",
                                "location": issue_key,
                                "message": "template repeats the checklist requirement without a useful defect model",
                            }
                        )
                    normalized = _normalized_similarity_text(combined)
                    for previous_key, previous in seen_texts:
                        if normalized == previous or SequenceMatcher(
                            None, normalized, previous, autojunk=False
                        ).ratio() >= 0.92:
                            findings.append(
                                {
                                    "code": "SEED_NEAR_DUPLICATE_TEMPLATE",
                                    "location": issue_key,
                                    "message": f"content is too similar to {previous_key}",
                                }
                            )
                            break
                    seen_texts.append((issue_key, normalized))
    return {
        "item_count": item_count,
        "issue_template_count": sum(count * items for count, items in template_counts.items()),
        "template_count_distribution": {
            str(count): template_counts.get(count, 0) for count in range(2, 7)
        },
        "issue_family_counts": dict(sorted(family_counts.items())),
        "finding_count": len(findings),
        "findings": findings,
    }


def _read_seed(seed_path: Path) -> tuple[dict[str, Any], str]:
    raw = seed_path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedValidationError(f"Invalid seed JSON: {seed_path.name}") from exc
    if not isinstance(payload, dict):
        raise SeedValidationError("Seed root must be an object")
    return payload, hashlib.sha256(raw).hexdigest()


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SeedValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _stable_key(value: Any, field: str) -> str:
    key = _require_string(value, field)
    if STABLE_KEY_PATTERN.fullmatch(key) is None:
        raise SeedValidationError(f"{field} has an invalid stable key: {key}")
    return key


def validate_json_schema(payload: dict[str, Any], schema_path: Path) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise SeedValidationError(f"Invalid seed JSON Schema: {schema_path}") from exc
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(value) for value in error.absolute_path) or "root"
        raise SeedValidationError(f"JSON Schema violation at {location}: {error.message}")


def validate_seed_payload(payload: dict[str, Any], schema_path: Path | None = None) -> None:
    if schema_path is not None:
        validate_json_schema(payload, schema_path)
    seed_version = _require_string(payload.get("seed_version"), "seed_version")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", seed_version):
        raise SeedValidationError("seed_version must use numeric semantic version form")
    checklists = payload.get("checklists")
    if not isinstance(checklists, list):
        raise SeedValidationError("checklists must be an array")

    checklist_keys: set[str] = set()
    global_issue_keys: set[str] = set()
    for checklist_index, checklist in enumerate(checklists):
        if not isinstance(checklist, dict):
            raise SeedValidationError(f"checklists[{checklist_index}] must be an object")
        checklist_key = _stable_key(checklist.get("stable_key"), "checklist stable_key")
        if checklist_key in checklist_keys:
            raise SeedValidationError(f"Duplicate checklist stable key: {checklist_key}")
        checklist_keys.add(checklist_key)
        _require_string(checklist.get("name"), "checklist name")
        if checklist.get("audit_kind") not in VALID_AUDIT_KINDS:
            raise SeedValidationError(f"Invalid audit_kind for {checklist_key}")
        groups = checklist.get("groups")
        if not isinstance(groups, list):
            raise SeedValidationError(f"groups must be an array for {checklist_key}")

        group_keys: set[str] = set()
        for group in groups:
            if not isinstance(group, dict):
                raise SeedValidationError(f"group in {checklist_key} must be an object")
            group_key = _stable_key(group.get("stable_key"), "group stable_key")
            if group_key in group_keys:
                raise SeedValidationError(f"Duplicate group stable key in {checklist_key}: {group_key}")
            group_keys.add(group_key)
            _require_string(group.get("name"), "group name")
            items = group.get("items")
            if not isinstance(items, list):
                raise SeedValidationError(f"items must be an array for {group_key}")

            item_keys: set[str] = set()
            for item in items:
                if not isinstance(item, dict):
                    raise SeedValidationError(f"item in {group_key} must be an object")
                item_key = _stable_key(item.get("stable_key"), "item stable_key")
                if item_key in item_keys:
                    raise SeedValidationError(f"Duplicate item stable key in {group_key}: {item_key}")
                item_keys.add(item_key)
                _require_string(item.get("content"), "item content")
                issue_templates = item.get("issue_templates")
                if not isinstance(issue_templates, list):
                    raise SeedValidationError(f"issue_templates must be an array for {item_key}")
                if not 2 <= len(issue_templates) <= 6:
                    raise SeedValidationError(
                        f"Issue template count for {item_key} must be between 2 and 6"
                    )
                issue_keys: set[str] = set()
                for issue in issue_templates:
                    if not isinstance(issue, dict):
                        raise SeedValidationError(f"issue template in {item_key} must be an object")
                    issue_key = _stable_key(issue.get("stable_key"), "issue stable_key")
                    if issue_key in issue_keys:
                        raise SeedValidationError(
                            f"Duplicate issue stable key in {item_key}: {issue_key}"
                        )
                    issue_keys.add(issue_key)
                    if issue_key in global_issue_keys:
                        raise SeedValidationError(f"Duplicate global issue stable key: {issue_key}")
                    global_issue_keys.add(issue_key)
                    _require_string(issue.get("title"), "issue title")
                    _require_string(issue.get("description"), "issue description")
                    _require_string(issue.get("corrective_suggestion"), "corrective_suggestion")
                    if issue.get("default_severity") not in VALID_SEVERITIES:
                        raise SeedValidationError(f"Invalid default severity for {issue_key}")
                    placeholders = issue.get("placeholders", [])
                    if not isinstance(placeholders, list) or any(
                        not isinstance(value, str) for value in placeholders
                    ):
                        raise SeedValidationError(f"placeholders must be a string array for {issue_key}")
                    unknown_placeholders = set(placeholders) - ALLOWED_PLACEHOLDERS
                    if unknown_placeholders:
                        raise SeedValidationError(
                            f"Unknown placeholders for {issue_key}: {sorted(unknown_placeholders)}"
                        )


async def preview_seed_upgrade(
    database_path: Path,
    seed_path: Path,
    schema_path: Path | None = None,
) -> SeedUpgradePreview:
    payload, _ = _read_seed(seed_path)
    validate_seed_payload(payload, schema_path)
    async with connect(database_path) as connection:
        versions = await (await connection.execute("SELECT seed_version FROM seed_imports")).fetchall()
        from_version = max(
            (row["seed_version"] for row in versions), key=_version_tuple, default=None
        )
        conflicts: list[SeedUpgradeConflict] = []
        queries = (
            ("checklist", "SELECT stable_key FROM checklist_templates WHERE user_modified = 1"),
            (
                "group",
                """
                SELECT ct.stable_key || '/' || cg.stable_key AS stable_key
                FROM checklist_groups cg
                JOIN checklist_templates ct ON ct.id = cg.checklist_template_id
                WHERE cg.user_modified = 1
                """,
            ),
            (
                "item",
                """
                SELECT ct.stable_key || '/' || cg.stable_key || '/' || ci.stable_key AS stable_key
                FROM checklist_items ci
                JOIN checklist_groups cg ON cg.id = ci.checklist_group_id
                JOIN checklist_templates ct ON ct.id = cg.checklist_template_id
                WHERE ci.user_modified = 1
                """,
            ),
            (
                "issue",
                "SELECT stable_key FROM issue_templates WHERE user_modified = 1",
            ),
        )
        for entity_type, query in queries:
            rows = await (await connection.execute(query)).fetchall()
            conflicts.extend(
                SeedUpgradeConflict(entity_type, row["stable_key"]) for row in rows
            )
    return SeedUpgradePreview(
        from_version,
        payload["seed_version"],
        tuple(sorted(conflicts, key=lambda value: (value.entity_type, value.stable_key))),
    )


async def import_seed(
    database_path: Path,
    seed_path: Path,
    schema_path: Path | None = None,
    *,
    preserve_user_modified: bool = False,
) -> SeedImportResult:
    payload, checksum = _read_seed(seed_path)
    validate_seed_payload(payload, schema_path)
    seed_version = payload["seed_version"]
    preview = await preview_seed_upgrade(database_path, seed_path, schema_path)

    async with connect(database_path) as connection:
        existing = await (
            await connection.execute(
                "SELECT seed_sha256 FROM seed_imports WHERE seed_version = ?", (seed_version,)
            )
        ).fetchone()
        if existing is not None:
            if existing["seed_sha256"] != checksum:
                raise SeedValidationError(f"Seed version {seed_version} content changed")
            return SeedImportResult(seed_version, checksum, False)
        if preview.from_version is not None and _version_tuple(seed_version) <= _version_tuple(
            preview.from_version
        ):
            raise SeedValidationError(
                f"Seed version {seed_version} must be newer than {preview.from_version}"
            )
        if preview.conflicts and not preserve_user_modified:
            raise SeedValidationError(
                f"Seed upgrade has {len(preview.conflicts)} user-modified conflicts; "
                "preview and explicitly preserve them before applying"
            )

        seen_checklist_ids: set[int] = set()
        seen_group_ids: set[int] = set()
        seen_item_ids: set[int] = set()
        seen_issue_ids: set[int] = set()
        async with transaction(connection):
            for checklist_order, checklist in enumerate(payload["checklists"], start=1):
                existing_checklist = await (
                    await connection.execute(
                        "SELECT * FROM checklist_templates WHERE stable_key = ?",
                        (checklist["stable_key"],),
                    )
                ).fetchone()
                checklist_values = (
                    checklist["name"], checklist["audit_kind"], seed_version,
                    checklist.get("source_sheet"), checklist_order,
                    checklist.get("source_workbook"), checklist.get("source", {}).get("max_row"),
                    checklist.get("source", {}).get("max_column"),
                    checklist.get("source", {}).get("print_area"),
                    checklist.get("source", {}).get("freeze_panes"),
                )
                if existing_checklist is None:
                    cursor = await connection.execute(
                        """
                        INSERT INTO checklist_templates(
                            name, audit_kind, builtin_version, source_sheet, display_order,
                            source_workbook, source_max_row, source_max_column, source_print_area,
                            source_freeze_panes, stable_key
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (*checklist_values, checklist["stable_key"]),
                    )
                    checklist_id = int(cursor.lastrowid)
                else:
                    checklist_id = int(existing_checklist["id"])
                    if not existing_checklist["user_modified"]:
                        await connection.execute(
                            """
                            UPDATE checklist_templates SET
                                name = ?, audit_kind = ?, builtin_version = ?, source_sheet = ?,
                                display_order = ?, source_workbook = ?, source_max_row = ?,
                                source_max_column = ?, source_print_area = ?, source_freeze_panes = ?,
                                enabled = 1
                            WHERE id = ?
                            """,
                            (*checklist_values, checklist_id),
                        )
                seen_checklist_ids.add(checklist_id)

                for group_order, group in enumerate(checklist["groups"], start=1):
                    existing_group = await (
                        await connection.execute(
                            """
                            SELECT * FROM checklist_groups
                            WHERE checklist_template_id = ? AND stable_key = ?
                            """,
                            (checklist_id, group["stable_key"]),
                        )
                    ).fetchone()
                    group_values = (
                        group["name"], seed_version, group_order,
                        int(bool(group.get("display_heading", False))),
                        group.get("source", {}).get("row"),
                    )
                    if existing_group is None:
                        cursor = await connection.execute(
                            """
                            INSERT INTO checklist_groups(
                                name, version, display_order, display_heading, source_row,
                                checklist_template_id, stable_key
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (*group_values, checklist_id, group["stable_key"]),
                        )
                        group_id = int(cursor.lastrowid)
                    else:
                        group_id = int(existing_group["id"])
                        if not existing_group["user_modified"]:
                            await connection.execute(
                                """
                                UPDATE checklist_groups SET name = ?, version = ?, display_order = ?,
                                    display_heading = ?, source_row = ?, enabled = 1
                                WHERE id = ?
                                """,
                                (*group_values, group_id),
                            )
                    seen_group_ids.add(group_id)

                    for item_order, item in enumerate(group["items"], start=1):
                        existing_item = await (
                            await connection.execute(
                                """
                                SELECT * FROM checklist_items
                                WHERE checklist_group_id = ? AND stable_key = ?
                                """,
                                (group_id, item["stable_key"]),
                            )
                        ).fetchone()
                        item_values = (
                            str(item.get("display_no", item_order)), item["content"],
                            int(bool(item.get("starred", False))), seed_version, item_order,
                            item.get("source", {}).get("row"),
                            item.get("source", {}).get("content_cell"),
                        )
                        if existing_item is None:
                            cursor = await connection.execute(
                                """
                                INSERT INTO checklist_items(
                                    display_no, content, starred, version, display_order,
                                    source_row, source_cell, checklist_group_id, stable_key
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (*item_values, group_id, item["stable_key"]),
                            )
                            item_id = int(cursor.lastrowid)
                            subtree_modified = False
                        else:
                            item_id = int(existing_item["id"])
                            modified_issue = await (
                                await connection.execute(
                                    """
                                    SELECT 1 FROM issue_templates
                                    WHERE checklist_item_id = ? AND user_modified = 1 LIMIT 1
                                    """,
                                    (item_id,),
                                )
                            ).fetchone()
                            subtree_modified = bool(existing_item["user_modified"] or modified_issue)
                            if not subtree_modified:
                                await connection.execute(
                                    """
                                    UPDATE checklist_items SET display_no = ?, content = ?, starred = ?,
                                        version = ?, display_order = ?, source_row = ?, source_cell = ?,
                                        enabled = 1, revision = revision + 1
                                    WHERE id = ?
                                    """,
                                    (*item_values, item_id),
                                )
                        seen_item_ids.add(item_id)
                        if subtree_modified:
                            rows = await (
                                await connection.execute(
                                    "SELECT id FROM issue_templates WHERE checklist_item_id = ?",
                                    (item_id,),
                                )
                            ).fetchall()
                            seen_issue_ids.update(int(row["id"]) for row in rows)
                            continue

                        await connection.execute(
                            "UPDATE issue_templates SET is_default = 0 WHERE checklist_item_id = ?",
                            (item_id,),
                        )
                        for issue_order, issue in enumerate(item["issue_templates"], start=1):
                            existing_issue = await (
                                await connection.execute(
                                    """
                                    SELECT id FROM issue_templates
                                    WHERE checklist_item_id = ? AND stable_key = ?
                                    """,
                                    (item_id, issue["stable_key"]),
                                )
                            ).fetchone()
                            issue_values = (
                                seed_version, issue["title"], issue["description"],
                                issue["corrective_suggestion"], issue["default_severity"],
                                json.dumps(issue.get("placeholders", []), ensure_ascii=False),
                                0, issue_order,
                            )
                            if existing_issue is None:
                                cursor = await connection.execute(
                                    """
                                    INSERT INTO issue_templates(
                                        version, title, description, corrective_suggestion,
                                        default_severity, placeholders_json, is_default,
                                        display_order, checklist_item_id, stable_key
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (*issue_values, item_id, issue["stable_key"]),
                                )
                                issue_id = int(cursor.lastrowid)
                            else:
                                issue_id = int(existing_issue["id"])
                                await connection.execute(
                                    """
                                    UPDATE issue_templates SET version = ?, title = ?, description = ?,
                                        corrective_suggestion = ?, default_severity = ?,
                                        placeholders_json = ?, is_default = ?, display_order = ?,
                                        enabled = 1, revision = revision + 1
                                    WHERE id = ?
                                    """,
                                    (*issue_values, issue_id),
                                )
                            seen_issue_ids.add(issue_id)

            protected_rows = await (
                await connection.execute(
                    """
                    SELECT
                        ct.id AS checklist_id,
                        cg.id AS group_id,
                        ci.id AS item_id,
                        it.id AS issue_id
                    FROM checklist_templates ct
                    LEFT JOIN checklist_groups cg ON cg.checklist_template_id = ct.id
                    LEFT JOIN checklist_items ci ON ci.checklist_group_id = cg.id
                    LEFT JOIN issue_templates it ON it.checklist_item_id = ci.id
                    WHERE ct.user_modified = 1
                       OR cg.user_modified = 1
                       OR ci.user_modified = 1
                       OR it.user_modified = 1
                       OR EXISTS (
                            SELECT 1 FROM issue_templates modified_issue
                            WHERE modified_issue.checklist_item_id = ci.id
                              AND modified_issue.user_modified = 1
                       )
                    """
                )
            ).fetchall()
            for row in protected_rows:
                seen_checklist_ids.add(int(row["checklist_id"]))
                if row["group_id"] is not None:
                    seen_group_ids.add(int(row["group_id"]))
                if row["item_id"] is not None:
                    seen_item_ids.add(int(row["item_id"]))
                if row["issue_id"] is not None:
                    seen_issue_ids.add(int(row["issue_id"]))

            if seen_issue_ids:
                placeholders = ",".join("?" for _ in seen_issue_ids)
                await connection.execute(
                    f"UPDATE issue_templates SET enabled = 0, is_default = 0 "
                    f"WHERE user_modified = 0 AND id NOT IN ({placeholders})",
                    tuple(seen_issue_ids),
                )
            if seen_item_ids:
                placeholders = ",".join("?" for _ in seen_item_ids)
                await connection.execute(
                    f"UPDATE checklist_items SET enabled = 0 WHERE user_modified = 0 "
                    f"AND id NOT IN ({placeholders})",
                    tuple(seen_item_ids),
                )
            if seen_group_ids:
                placeholders = ",".join("?" for _ in seen_group_ids)
                await connection.execute(
                    f"UPDATE checklist_groups SET enabled = 0 WHERE user_modified = 0 "
                    f"AND id NOT IN ({placeholders})",
                    tuple(seen_group_ids),
                )
            if seen_checklist_ids:
                placeholders = ",".join("?" for _ in seen_checklist_ids)
                await connection.execute(
                    f"UPDATE checklist_templates SET enabled = 0 WHERE user_modified = 0 "
                    f"AND id NOT IN ({placeholders})",
                    tuple(seen_checklist_ids),
                )
            await connection.execute(
                "INSERT INTO seed_imports(seed_version, seed_sha256, applied_at) VALUES (?, ?, ?)",
                (seed_version, checksum, datetime.now(UTC).isoformat()),
            )
    return SeedImportResult(seed_version, checksum, True, len(preview.conflicts))
