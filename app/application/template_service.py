from __future__ import annotations

import hashlib, json, os, secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.database import connect, transaction
from app.domain.issue_template_policy import (
    IssuePolicyViolation,
    coverage_diagnostic,
    validate_enabled_template_addition,
    validate_issue_severity,
)
from app.excel.template_library import read_template_library
from app.safety import UploadLimits, validate_xlsx
from app.seeds import STABLE_KEY_PATTERN


class TemplateServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None: super().__init__(message); self.code = code


def _policy(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except IssuePolicyViolation as exc:
        raise TemplateServiceError(exc.code, str(exc)) from exc


def _now() -> str: return datetime.now(UTC).isoformat()
def _hash(token: str) -> str: return hashlib.sha256(token.encode()).hexdigest()
def _bool(value) -> int:
    if value in (True, 1, "1", "是", "true", "True"): return 1
    if value in (False, 0, "0", "否", "false", "False"): return 0
    raise TemplateServiceError("TEMPLATE_IMPORT_INVALID", "启用、星标和默认问题必须为布尔值")


def _required(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TemplateServiceError("TEMPLATE_INVALID", f"{field}不能为空")
    return text


def _key(value, field: str) -> str:
    key = _required(value, field)
    if STABLE_KEY_PATTERN.fullmatch(key) is None:
        raise TemplateServiceError("TEMPLATE_STABLE_KEY_INVALID", f"{field}格式无效")
    return key


def _next_version(version: str) -> str:
    if "+user." in version:
        base, counter = version.rsplit("+user.", 1)
        return f"{base}+user.{int(counter) + 1}"
    return f"{version}+user.1"


def _library_digest(rows: tuple[dict, ...]) -> str:
    normalized = [
        {key: value for key, value in row.items() if not key.endswith("_id") and key != "placeholders_json"}
        for row in rows
    ]
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_import_rows(rows: tuple[dict, ...]) -> None:
    seen: set[str] = set()
    checklists: dict[str, tuple] = {}
    groups: dict[tuple[str, str], tuple] = {}
    items: dict[tuple[str, str, str], tuple] = {}
    for row_number, row in enumerate(rows, 2):
        for field in ("checklist_key", "group_key", "item_key", "issue_key"):
            _key(row.get(field), f"第{row_number}行 {field}")
        for field in ("checklist_name", "checklist_version", "group_name", "group_version", "content", "item_version", "issue_version", "title", "description", "corrective_suggestion"):
            _required(row.get(field), f"第{row_number}行 {field}")
        if row.get("audit_kind") not in {"product", "process"}:
            raise TemplateServiceError("TEMPLATE_IMPORT_INVALID", f"第{row_number}行审核类型无效")
        if row.get("default_severity") not in {"轻微", "一般", "严重"}:
            raise TemplateServiceError("TEMPLATE_IMPORT_INVALID", f"第{row_number}行默认严重度无效")
        for field in ("checklist_enabled", "group_enabled", "starred", "item_enabled", "is_default", "issue_enabled"):
            _bool(row.get(field))
        for field in ("checklist_order", "group_order", "item_order", "issue_order"):
            try:
                if int(row.get(field)) < 1:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise TemplateServiceError("TEMPLATE_IMPORT_INVALID", f"第{row_number}行 {field} 必须为正整数") from exc
        issue_key = str(row["issue_key"])
        if issue_key in seen:
            raise TemplateServiceError("TEMPLATE_IMPORT_DUPLICATE_KEY", f"问题稳定键重复：{issue_key}")
        seen.add(issue_key)
        checklist_key = str(row["checklist_key"])
        group_key = str(row["group_key"])
        item_key = str(row["item_key"])
        checklist_signature = (str(row["checklist_name"]), str(row["audit_kind"]), _bool(row["checklist_enabled"]), int(row["checklist_order"]))
        group_signature = (str(row["group_name"]), str(row["group_version"]), _bool(row["group_enabled"]), int(row["group_order"]))
        item_signature = (str(row["display_no"]), str(row["content"]), _bool(row["starred"]), str(row["item_version"]), _bool(row["item_enabled"]), int(row["item_order"]))
        _policy(
            validate_issue_severity,
            starred=bool(_bool(row["starred"])),
            severity=str(row["default_severity"]),
        )
        if checklists.setdefault(checklist_key, checklist_signature) != checklist_signature:
            raise TemplateServiceError("TEMPLATE_IMPORT_HIERARCHY_CONFLICT", f"检查单重复行内容不一致：{checklist_key}")
        if groups.setdefault((checklist_key, group_key), group_signature) != group_signature:
            raise TemplateServiceError("TEMPLATE_IMPORT_HIERARCHY_CONFLICT", f"分组重复行内容不一致：{group_key}")
        if items.setdefault((checklist_key, group_key, item_key), item_signature) != item_signature:
            raise TemplateServiceError("TEMPLATE_IMPORT_HIERARCHY_CONFLICT", f"检查项重复行内容不一致：{item_key}")
        try:
            placeholders = json.loads(str(row.get("placeholders") or "[]"))
        except json.JSONDecodeError as exc:
            raise TemplateServiceError("TEMPLATE_IMPORT_INVALID", f"第{row_number}行占位符不是 JSON 数组") from exc
        if not isinstance(placeholders, list):
            raise TemplateServiceError("TEMPLATE_IMPORT_INVALID", f"第{row_number}行占位符不是 JSON 数组")


async def create_issue(database_path: Path, item_id: int, values: dict) -> dict:
    key = _key(values.get("stable_key"), "问题稳定键")
    title = _required(values.get("title"), "问题标题")
    description = _required(values.get("description"), "问题描述")
    suggestion = _required(values.get("corrective_suggestion"), "整改建议")
    severity = values.get("default_severity")
    placeholders = values.get("placeholders", [])
    if not isinstance(placeholders, list) or any(not isinstance(value, str) for value in placeholders):
        raise TemplateServiceError("TEMPLATE_INVALID", "占位符必须为字符串数组")
    async with connect(database_path) as connection:
        async with transaction(connection):
            item = await (await connection.execute(
                "SELECT id, starred FROM checklist_items WHERE id=?", (item_id,)
            )).fetchone()
            if item is None:
                raise TemplateServiceError("TEMPLATE_ITEM_NOT_FOUND", "检查项不存在")
            _policy(validate_issue_severity, starred=bool(item["starred"]), severity=severity)
            enabled_count = int((await (await connection.execute(
                "SELECT COUNT(*) FROM issue_templates WHERE checklist_item_id=? AND enabled=1",
                (item_id,),
            )).fetchone())[0])
            _policy(validate_enabled_template_addition, enabled_count)
            duplicate = await (await connection.execute(
                "SELECT 1 FROM issue_templates WHERE stable_key=?",
                (key,),
            )).fetchone()
            if duplicate:
                raise TemplateServiceError("TEMPLATE_STABLE_KEY_CONFLICT", "问题稳定键已存在")
            order = values.get("display_order")
            if order is None:
                row = await (await connection.execute(
                    "SELECT COALESCE(MAX(display_order),0)+1 FROM issue_templates WHERE checklist_item_id=?",
                    (item_id,),
                )).fetchone()
                order = int(row[0])
            cursor = await connection.execute(
                """
                INSERT INTO issue_templates(
                    checklist_item_id,stable_key,version,title,description,
                    corrective_suggestion,default_severity,placeholders_json,
                    is_default,enabled,user_modified,display_order
                ) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)
                """,
                (item_id,key,"user.1",title,description,suggestion,severity,
                 json.dumps(placeholders,ensure_ascii=False),
                 0,1,int(order)),
            )
            row = await (await connection.execute(
                "SELECT * FROM issue_templates WHERE id=?", (cursor.lastrowid,)
            )).fetchone()
    return dict(row)


async def copy_issue(database_path: Path, issue_id: int, stable_key: str) -> dict:
    async with connect(database_path) as connection:
        source = await (await connection.execute(
            "SELECT * FROM issue_templates WHERE id=?", (issue_id,)
        )).fetchone()
    if source is None:
        raise TemplateServiceError("TEMPLATE_ISSUE_NOT_FOUND", "问题模板不存在")
    return await create_issue(database_path, int(source["checklist_item_id"]), {
        "stable_key": stable_key,
        "title": f"{source['title']}（副本）",
        "description": source["description"],
        "corrective_suggestion": source["corrective_suggestion"],
        "default_severity": source["default_severity"],
        "placeholders": json.loads(source["placeholders_json"]),
        "is_default": False,
    })


async def save_audit_issue_as_template(
    database_path: Path, issue_id: int, values: dict
) -> dict:
    title = _required(values.get("title"), "问题标题")
    description = _required(values.get("description"), "问题描述")
    suggestion = _required(values.get("corrective_suggestion"), "整改建议")
    async with connect(database_path) as connection:
        async with transaction(connection):
            source = await (
                await connection.execute(
                    """
                    SELECT nc.severity, cii.source_item_id
                    FROM nonconformities nc
                    JOIN checklist_instance_items cii
                      ON cii.id = nc.checklist_instance_item_id
                    WHERE nc.id = ?
                    """,
                    (issue_id,),
                )
            ).fetchone()
            if source is None:
                raise TemplateServiceError("AUDIT_ISSUE_NOT_FOUND", "审核问题不存在")
            if source["source_item_id"] is None:
                raise TemplateServiceError(
                    "TEMPLATE_SOURCE_ITEM_NOT_FOUND", "原检查项已不存在，不能保存为模板"
                )
            item_id = int(source["source_item_id"])
            item = await (
                await connection.execute(
                    "SELECT starred FROM checklist_items WHERE id=?", (item_id,)
                )
            ).fetchone()
            if item is None:
                raise TemplateServiceError(
                    "TEMPLATE_SOURCE_ITEM_NOT_FOUND", "原检查项已不存在，不能保存为模板"
                )
            severity = values.get("default_severity", source["severity"])
            _policy(
                validate_issue_severity,
                starred=bool(item["starred"]),
                severity=severity,
            )
            enabled_count = int(
                (
                    await (
                        await connection.execute(
                            "SELECT COUNT(*) FROM issue_templates "
                            "WHERE checklist_item_id=? AND enabled=1",
                            (item_id,),
                        )
                    ).fetchone()
                )[0]
            )
            _policy(validate_enabled_template_addition, enabled_count)
            order = int(
                (
                    await (
                        await connection.execute(
                            "SELECT COALESCE(MAX(display_order),0)+1 FROM issue_templates "
                            "WHERE checklist_item_id=?",
                            (item_id,),
                        )
                    ).fetchone()
                )[0]
            )
            stable_key = f"user.issue.{secrets.token_hex(12)}"
            cursor = await connection.execute(
                """
                INSERT INTO issue_templates(
                    checklist_item_id, stable_key, version, title, description,
                    corrective_suggestion, default_severity, placeholders_json,
                    is_default, enabled, user_modified, display_order
                ) VALUES (?, ?, 'user.1', ?, ?, ?, ?, '[]', 0, 1, 1, ?)
                """,
                (item_id, stable_key, title, description, suggestion, severity, order),
            )
            created = await (
                await connection.execute(
                    "SELECT * FROM issue_templates WHERE id=?", (cursor.lastrowid,)
                )
            ).fetchone()
    return dict(created)


async def create_template_entity(
    database_path: Path, entity_type: str, parent_id: int | None, values: dict
) -> dict:
    key = _key(values.get("stable_key"), "稳定键")
    async with connect(database_path) as connection:
        async with transaction(connection):
            duplicate = await (await connection.execute(
                "SELECT 1 FROM checklist_templates WHERE stable_key=?", (key,)
            )).fetchone() if entity_type == "checklists" else None
            if entity_type == "checklists":
                if duplicate:
                    raise TemplateServiceError("TEMPLATE_STABLE_KEY_CONFLICT", "稳定键已存在")
                audit_kind = values.get("audit_kind")
                if audit_kind not in {"product", "process"}:
                    raise TemplateServiceError("TEMPLATE_INVALID", "审核类型无效")
                table = "checklist_templates"
                cursor = await connection.execute(
                    "INSERT INTO checklist_templates(stable_key,name,audit_kind,builtin_version,enabled,user_modified,display_order) VALUES(?,?,?,?,0,1,?)",
                    (key,_required(values.get("name"),"检查单名称"),audit_kind,"user.1",int(values.get("display_order",1))),
                )
            elif entity_type == "groups":
                if parent_id is None:
                    raise TemplateServiceError("TEMPLATE_PARENT_REQUIRED", "新增分组必须指定检查单")
                parent = await (await connection.execute("SELECT 1 FROM checklist_templates WHERE id=?", (parent_id,))).fetchone()
                if parent is None:
                    raise TemplateServiceError("TEMPLATE_PARENT_NOT_FOUND", "检查单不存在")
                duplicate = await (await connection.execute("SELECT 1 FROM checklist_groups WHERE checklist_template_id=? AND stable_key=?", (parent_id,key))).fetchone()
                if duplicate:
                    raise TemplateServiceError("TEMPLATE_STABLE_KEY_CONFLICT", "稳定键已存在")
                table = "checklist_groups"
                cursor = await connection.execute(
                    "INSERT INTO checklist_groups(checklist_template_id,stable_key,name,version,enabled,user_modified,display_order) VALUES(?,?,?,?,0,1,?)",
                    (parent_id,key,_required(values.get("name"),"分组名称"),"user.1",int(values.get("display_order",1))),
                )
            elif entity_type == "items":
                if parent_id is None:
                    raise TemplateServiceError("TEMPLATE_PARENT_REQUIRED", "新增检查项必须指定分组")
                parent = await (await connection.execute("SELECT 1 FROM checklist_groups WHERE id=?", (parent_id,))).fetchone()
                if parent is None:
                    raise TemplateServiceError("TEMPLATE_PARENT_NOT_FOUND", "分组不存在")
                duplicate = await (await connection.execute("SELECT 1 FROM checklist_items WHERE checklist_group_id=? AND stable_key=?", (parent_id,key))).fetchone()
                if duplicate:
                    raise TemplateServiceError("TEMPLATE_STABLE_KEY_CONFLICT", "稳定键已存在")
                table = "checklist_items"
                cursor = await connection.execute(
                    "INSERT INTO checklist_items(checklist_group_id,stable_key,display_no,content,starred,version,enabled,user_modified,display_order) VALUES(?,?,?,?,?, 'user.1',0,1,?)",
                    (parent_id,key,_required(values.get("display_no"),"显示序号"),_required(values.get("content"),"检查项内容"),_bool(values.get("starred",False)),int(values.get("display_order",1))),
                )
            else:
                raise TemplateServiceError("TEMPLATE_ENTITY_INVALID", "模板实体类型无效")
            row = await (await connection.execute(
                f"SELECT * FROM {table} WHERE id=?", (cursor.lastrowid,)
            )).fetchone()
    return dict(row)


async def update_template_entity(
    database_path: Path, entity_type: str, entity_id: int, values: dict
) -> dict:
    if entity_type == "items" and "starred" in values:
        raise TemplateServiceError(
            "TEMPLATE_ITEM_STARRED_IMMUTABLE",
            "星标由检查单定义，不能在普通编辑中单独修改",
        )
    definitions = {
        "checklists": ("checklist_templates", {"name", "audit_kind", "enabled", "display_order"}, "builtin_version"),
        "groups": ("checklist_groups", {"name", "enabled", "display_order"}, "version"),
        "items": ("checklist_items", {"display_no", "content", "enabled", "display_order"}, "version"),
    }
    if entity_type not in definitions:
        raise TemplateServiceError("TEMPLATE_ENTITY_INVALID", "模板实体类型无效")
    table, allowed, version_column = definitions[entity_type]
    changes = {key: value for key, value in values.items() if key in allowed}
    if not changes:
        raise TemplateServiceError("TEMPLATE_UPDATE_EMPTY", "未提供可更新字段")
    for key in ("enabled",):
        if key in changes:
            changes[key] = _bool(changes[key])
    for key in ("name", "display_no", "content"):
        if key in changes:
            changes[key] = _required(changes[key], key)
    if "audit_kind" in changes and changes["audit_kind"] not in {"product", "process"}:
        raise TemplateServiceError("TEMPLATE_INVALID", "审核类型无效")
    async with connect(database_path) as connection:
        async with transaction(connection):
            row = await (await connection.execute(
                f"SELECT * FROM {table} WHERE id=?", (entity_id,)
            )).fetchone()
            if row is None:
                raise TemplateServiceError("TEMPLATE_ENTITY_NOT_FOUND", "模板实体不存在")
            changes[version_column] = _next_version(str(row[version_column]))
            fields = [f"{key}=?" for key in changes] + ["user_modified=1"]
            if entity_type == "items":
                fields.append("revision=revision+1")
            await connection.execute(
                f"UPDATE {table} SET {','.join(fields)} WHERE id=?",
                (*changes.values(), entity_id),
            )
            changed = await (await connection.execute(
                f"SELECT * FROM {table} WHERE id=?", (entity_id,)
            )).fetchone()
    return dict(changed)


async def _flat_template_rows_connection(c) -> tuple[dict, ...]:
    rows = await (await c.execute("""
            SELECT ct.id checklist_id,ct.stable_key checklist_key,ct.name checklist_name,ct.audit_kind,ct.builtin_version checklist_version,ct.enabled checklist_enabled,ct.user_modified checklist_modified,ct.display_order checklist_order,
              cg.id group_id,cg.stable_key group_key,cg.name group_name,cg.version group_version,cg.enabled group_enabled,cg.user_modified group_modified,cg.display_order group_order,
              ci.id item_id,ci.stable_key item_key,ci.display_no,ci.content,ci.starred,ci.version item_version,ci.enabled item_enabled,ci.user_modified item_modified,ci.display_order item_order,
              it.id issue_id,it.stable_key issue_key,it.version issue_version,it.title,it.description,it.corrective_suggestion,it.default_severity,it.placeholders_json,it.is_default,it.enabled issue_enabled,it.user_modified issue_modified,it.display_order issue_order
            FROM checklist_templates ct JOIN checklist_groups cg ON cg.checklist_template_id=ct.id
            JOIN checklist_items ci ON ci.checklist_group_id=cg.id JOIN issue_templates it ON it.checklist_item_id=ci.id
            ORDER BY ct.display_order,cg.display_order,ci.display_order,it.display_order
        """)).fetchall()
    return tuple({**dict(row), "placeholders": row["placeholders_json"]} for row in rows)


async def flat_template_rows(database_path: Path) -> tuple[dict, ...]:
    async with connect(database_path) as c:
        return await _flat_template_rows_connection(c)


async def checklist_library(database_path: Path) -> list[dict]:
    async with connect(database_path) as c:
        checklists = await (await c.execute("SELECT * FROM checklist_templates ORDER BY display_order,id")).fetchall()
        groups = await (await c.execute("SELECT * FROM checklist_groups ORDER BY display_order,id")).fetchall()
        items = await (await c.execute("SELECT * FROM checklist_items ORDER BY display_order,id")).fetchall()
        issues = await (await c.execute("SELECT * FROM issue_templates ORDER BY display_order,id")).fetchall()
    result: list[dict] = []
    checklist_by_id: dict[int, dict] = {}
    group_by_id: dict[int, dict] = {}
    item_by_id: dict[int, dict] = {}
    for row in checklists:
        value = {"id":row["id"],"stable_key":row["stable_key"],"name":row["name"],"audit_kind":row["audit_kind"],"version":row["builtin_version"],"enabled":bool(row["enabled"]),"user_modified":bool(row["user_modified"]),"display_order":row["display_order"],"groups":[]}
        result.append(value); checklist_by_id[int(row["id"])] = value
    for row in groups:
        value = {"id":row["id"],"stable_key":row["stable_key"],"name":row["name"],"version":row["version"],"enabled":bool(row["enabled"]),"user_modified":bool(row["user_modified"]),"display_order":row["display_order"],"items":[]}
        checklist_by_id[int(row["checklist_template_id"])]["groups"].append(value); group_by_id[int(row["id"])] = value
    for row in items:
        value = {"id":row["id"],"stable_key":row["stable_key"],"content":row["content"],"display_no":row["display_no"],"version":row["version"],"revision":row["revision"],"starred":bool(row["starred"]),"enabled":bool(row["enabled"]),"user_modified":bool(row["user_modified"]),"display_order":row["display_order"],"issues":[]}
        group_by_id[int(row["checklist_group_id"])]["items"].append(value); item_by_id[int(row["id"])] = value
    for row in issues:
        item_by_id[int(row["checklist_item_id"])]["issues"].append({"id":row["id"],"stable_key":row["stable_key"],"version":row["version"],"revision":row["revision"],"title":row["title"],"description":row["description"],"corrective_suggestion":row["corrective_suggestion"],"default_severity":row["default_severity"],"placeholders":json.loads(row["placeholders_json"]),"enabled":bool(row["enabled"]),"is_default":False,"user_modified":bool(row["user_modified"]),"display_order":row["display_order"]})
    for item in item_by_id.values():
        enabled_count = sum(1 for issue in item["issues"] if issue["enabled"])
        item["coverage_diagnostic"] = coverage_diagnostic(enabled_count)
    return result


async def update_issue(database_path: Path, issue_id: int, values: dict) -> dict:
    allowed={"title","description","corrective_suggestion","default_severity","enabled","display_order"}; values={k:v for k,v in values.items() if k in allowed}
    if not values: raise TemplateServiceError("TEMPLATE_UPDATE_EMPTY", "未提供可更新字段")
    for key in ("title", "description", "corrective_suggestion"):
        if key in values:
            values[key] = _required(values[key], key)
    async with connect(database_path) as c:
        async with transaction(c):
            row=await (await c.execute("SELECT it.*,ci.starred FROM issue_templates it JOIN checklist_items ci ON ci.id=it.checklist_item_id WHERE it.id=?",(issue_id,))).fetchone()
            if row is None: raise TemplateServiceError("TEMPLATE_ISSUE_NOT_FOUND","问题模板不存在")
            severity = values.get("default_severity", row["default_severity"])
            _policy(validate_issue_severity, starred=bool(row["starred"]), severity=severity)
            enabled = _bool(values.get("enabled", row["enabled"]))
            count=await (await c.execute("SELECT COUNT(*) FROM issue_templates WHERE checklist_item_id=? AND enabled=1 AND id<>?",(row["checklist_item_id"],issue_id))).fetchone()
            if enabled and not bool(row["enabled"]):
                _policy(validate_enabled_template_addition, int(count[0]))
            fields=["enabled=?","is_default=0","user_modified=1","version=?","revision=revision+1"]; params=[enabled, _next_version(str(row["version"]))]
            for key,value in values.items():
                if key != "enabled": fields.append(f"{key}=?"); params.append(value)
            params.append(issue_id); await c.execute(f"UPDATE issue_templates SET {','.join(fields)} WHERE id=?",params)
            changed=await (await c.execute("SELECT * FROM issue_templates WHERE id=?",(issue_id,))).fetchone()
    return dict(changed)


async def restore_builtin_entity(
    database_path: Path, seed_path: Path, entity_type: str, entity_id: int
) -> dict:
    if entity_type not in {"checklists", "groups", "items", "issues"}:
        raise TemplateServiceError("TEMPLATE_ENTITY_INVALID", "模板实体类型无效")
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    seed_version = str(payload["seed_version"])
    async with connect(database_path) as c:
        table = {
            "checklists": "checklist_templates", "groups": "checklist_groups",
            "items": "checklist_items", "issues": "issue_templates",
        }[entity_type]
        current = await (await c.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,))).fetchone()
        if current is None:
            raise TemplateServiceError("TEMPLATE_ENTITY_NOT_FOUND", "模板实体不存在")
        target = None
        parent_item_id = None
        for checklist_order, checklist in enumerate(payload["checklists"], start=1):
            if entity_type == "checklists" and checklist["stable_key"] == current["stable_key"]:
                target = (checklist, checklist_order)
            for group_order, group in enumerate(checklist["groups"], start=1):
                if entity_type == "groups" and group["stable_key"] == current["stable_key"]:
                    parent = await (await c.execute("SELECT stable_key FROM checklist_templates WHERE id=?", (current["checklist_template_id"],))).fetchone()
                    if parent and parent["stable_key"] == checklist["stable_key"]:
                        target = (group, group_order)
                for item_order, item in enumerate(group["items"], start=1):
                    if entity_type == "items" and item["stable_key"] == current["stable_key"]:
                        parent = await (await c.execute("SELECT cg.stable_key group_key,ct.stable_key checklist_key FROM checklist_groups cg JOIN checklist_templates ct ON ct.id=cg.checklist_template_id WHERE cg.id=?", (current["checklist_group_id"],))).fetchone()
                        if parent and (parent["checklist_key"], parent["group_key"]) == (checklist["stable_key"], group["stable_key"]):
                            target = (item, item_order)
                    for issue_order, issue in enumerate(item["issue_templates"], start=1):
                        if entity_type == "issues" and issue["stable_key"] == current["stable_key"]:
                            parent = await (await c.execute("SELECT ci.id,ci.starred,ci.stable_key item_key,cg.stable_key group_key,ct.stable_key checklist_key FROM checklist_items ci JOIN checklist_groups cg ON cg.id=ci.checklist_group_id JOIN checklist_templates ct ON ct.id=cg.checklist_template_id WHERE ci.id=?", (current["checklist_item_id"],))).fetchone()
                            if parent and (parent["checklist_key"], parent["group_key"], parent["item_key"]) == (checklist["stable_key"], group["stable_key"], item["stable_key"]):
                                target = (issue, issue_order)
                                parent_item_id = int(parent["id"])
        if target is None:
            raise TemplateServiceError("TEMPLATE_BUILTIN_NOT_FOUND", "该实体没有可恢复的内置版本")
        value, order = target
        async with transaction(c):
            if entity_type == "checklists":
                await c.execute("UPDATE checklist_templates SET name=?,audit_kind=?,builtin_version=?,enabled=1,user_modified=0,display_order=? WHERE id=?", (value["name"], value["audit_kind"], seed_version, order, entity_id))
            elif entity_type == "groups":
                await c.execute("UPDATE checklist_groups SET name=?,version=?,enabled=1,user_modified=0,display_order=? WHERE id=?", (value["name"], seed_version, order, entity_id))
            elif entity_type == "items":
                await c.execute("UPDATE checklist_items SET display_no=?,content=?,starred=?,version=?,enabled=1,user_modified=0,display_order=?,revision=revision+1 WHERE id=?", (str(value.get("display_no", order)), value["content"], int(bool(value.get("starred", False))), seed_version, order, entity_id))
            else:
                _policy(
                    validate_issue_severity,
                    starred=bool(parent["starred"]),
                    severity=value["default_severity"],
                )
                if not bool(current["enabled"]):
                    enabled_count = int((await (await c.execute(
                        "SELECT COUNT(*) FROM issue_templates WHERE checklist_item_id=? AND enabled=1",
                        (parent_item_id,),
                    )).fetchone())[0])
                    _policy(validate_enabled_template_addition, enabled_count)
                await c.execute("UPDATE issue_templates SET version=?,title=?,description=?,corrective_suggestion=?,default_severity=?,placeholders_json=?,is_default=0,enabled=1,user_modified=0,display_order=?,revision=revision+1 WHERE id=?", (seed_version,value["title"],value["description"],value["corrective_suggestion"],value["default_severity"],json.dumps(value.get("placeholders", []),ensure_ascii=False),order,entity_id))
            restored = await (await c.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,))).fetchone()
    return dict(restored)


async def create_import_preview(
    database_path: Path, content: bytes, ttl_seconds: int = 3600,
    max_bytes: int = 25 * 1024 * 1024,
) -> dict:
    import tempfile
    descriptor, raw_path = tempfile.mkstemp(suffix=".xlsx"); os.close(descriptor); path=Path(raw_path)
    try:
        path.write_bytes(content)
        validate_xlsx(path, UploadLimits(max_bytes=max_bytes))
        incoming=read_template_library(path); _validate_import_rows(incoming); current=await flat_template_rows(database_path)
    finally: path.unlink(missing_ok=True)
    by_key={row["issue_key"]:row for row in current}; incoming_keys=set(); changes=[]
    for row in incoming:
        key=str(row["issue_key"]); incoming_keys.add(key); old=by_key.get(key)
        if old is None: changes.append({"kind":"added","stable_key":key})
        elif tuple(str(row[name]) for name in ("checklist_key","group_key","item_key")) != tuple(str(old[name]) for name in ("checklist_key","group_key","item_key")): changes.append({"kind":"conflict","stable_key":key,"reason":"hierarchy_changed"})
        elif any(str(row[k]) != str(old[k]) for k in ("checklist_name","checklist_enabled","checklist_order","group_name","group_enabled","group_order","display_no","content","starred","item_enabled","item_order","title","description","corrective_suggestion","default_severity","issue_enabled","is_default","issue_order")): changes.append({"kind":"modified","stable_key":key})
    current_enabled: dict[tuple[str, str, str], set[str]] = {}
    incoming_enabled: dict[tuple[str, str, str], set[str]] = {}
    for row in current:
        if _bool(row["issue_enabled"]):
            item_key = (str(row["checklist_key"]), str(row["group_key"]), str(row["item_key"]))
            current_enabled.setdefault(item_key, set()).add(str(row["issue_key"]))
    for row in incoming:
        if _bool(row["issue_enabled"]):
            item_key = (str(row["checklist_key"]), str(row["group_key"]), str(row["item_key"]))
            incoming_enabled.setdefault(item_key, set()).add(str(row["issue_key"]))
    for item_key, enabled_keys in incoming_enabled.items():
        baseline_keys = current_enabled.get(item_key, set())
        if len(enabled_keys) > 4 and not enabled_keys.issubset(baseline_keys):
            changes.append({"kind":"conflict","stable_key":"/".join(item_key),"reason":"enabled_limit"})
    changes.extend({"kind":"disabled","stable_key":key} for key in by_key if key not in incoming_keys)
    token=secrets.token_urlsafe(32); expires=(datetime.now(UTC)+timedelta(seconds=ttl_seconds)).isoformat()
    async with connect(database_path) as c:
        async with transaction(c): await c.execute("INSERT INTO template_import_previews(token_hash,source_sha256,payload_json,diff_json,status,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",(_hash(token),hashlib.sha256(content).hexdigest(),json.dumps({"baseline":_library_digest(current),"rows":incoming},ensure_ascii=False),json.dumps(changes,ensure_ascii=False),"pending",expires,_now()))
    return {"preview_token":token,"changes":changes}


async def commit_import_preview(database_path: Path, token: str) -> dict:
    async with connect(database_path) as c:
        async with transaction(c):
            preview = await (await c.execute(
                "SELECT * FROM template_import_previews WHERE token_hash=?", (_hash(token),)
            )).fetchone()
            if preview is None or preview["status"] != "pending":
                raise TemplateServiceError("TEMPLATE_IMPORT_PREVIEW_INVALID", "模板导入预览不存在或已使用")
            if preview["expires_at"] <= _now():
                raise TemplateServiceError("TEMPLATE_IMPORT_PREVIEW_EXPIRED", "模板导入预览已过期")
            stored = json.loads(preview["payload_json"])
            incoming = tuple(stored["rows"])
            _validate_import_rows(incoming)
            current = await _flat_template_rows_connection(c)
            if _library_digest(current) != stored["baseline"]:
                raise TemplateServiceError("TEMPLATE_IMPORT_STALE_PREVIEW", "模板库在预览后已发生变化，请重新预览")
            changes = json.loads(preview["diff_json"])
            if any(change["kind"] == "conflict" for change in changes):
                raise TemplateServiceError("TEMPLATE_IMPORT_CONFLICT", "导入差异包含层级冲突")
            if not changes:
                await c.execute("UPDATE template_import_previews SET status='consumed' WHERE id=?", (preview["id"],))
                return {"applied_changes":0}

            checklist_rows = await (await c.execute("SELECT * FROM checklist_templates")).fetchall()
            group_rows = await (await c.execute("SELECT cg.*,ct.stable_key checklist_key FROM checklist_groups cg JOIN checklist_templates ct ON ct.id=cg.checklist_template_id")).fetchall()
            item_rows = await (await c.execute("SELECT ci.*,cg.stable_key group_key,ct.stable_key checklist_key FROM checklist_items ci JOIN checklist_groups cg ON cg.id=ci.checklist_group_id JOIN checklist_templates ct ON ct.id=cg.checklist_template_id")).fetchall()
            issue_rows = await (await c.execute("SELECT it.*,ci.id item_id FROM issue_templates it JOIN checklist_items ci ON ci.id=it.checklist_item_id")).fetchall()
            checklists = {row["stable_key"]: dict(row) for row in checklist_rows}
            groups = {(row["checklist_key"], row["stable_key"]): dict(row) for row in group_rows}
            items = {(row["checklist_key"], row["group_key"], row["stable_key"]): dict(row) for row in item_rows}
            existing = {row["stable_key"]: dict(row) for row in issue_rows}
            incoming_keys: set[str] = set()
            incoming_checklists: set[str] = set()
            incoming_groups: set[tuple[str, str]] = set()
            incoming_items: set[tuple[str, str, str]] = set()
            processed_checklists: set[str] = set()
            processed_groups: set[tuple[str, str]] = set()
            processed_items: set[tuple[str, str, str]] = set()
            await c.execute("UPDATE issue_templates SET is_default=0")
            for row in incoming:
                checklist_key = str(row["checklist_key"])
                incoming_checklists.add(checklist_key)
                checklist = checklists.get(checklist_key)
                if checklist is None:
                    cursor = await c.execute("INSERT INTO checklist_templates(stable_key,name,audit_kind,builtin_version,enabled,user_modified,display_order) VALUES(?,?,?,?,?,?,?)", (checklist_key,str(row["checklist_name"]),str(row["audit_kind"]),str(row["checklist_version"]),_bool(row["checklist_enabled"]),1,int(row["checklist_order"])))
                    checklist = {"id": int(cursor.lastrowid), "stable_key": checklist_key}
                    checklists[checklist_key] = checklist
                elif checklist_key not in processed_checklists:
                    expected = (str(row["checklist_name"]),str(row["audit_kind"]),_bool(row["checklist_enabled"]),int(row["checklist_order"]))
                    actual = (checklist["name"],checklist["audit_kind"],checklist["enabled"],checklist["display_order"])
                    if actual != expected:
                        await c.execute("UPDATE checklist_templates SET name=?,audit_kind=?,enabled=?,display_order=?,builtin_version=?,user_modified=1 WHERE id=?", (*expected,_next_version(str(checklist["builtin_version"])),checklist["id"]))
                processed_checklists.add(checklist_key)
                checklist_id = int(checklist["id"])

                group_key = (checklist_key, str(row["group_key"]))
                incoming_groups.add(group_key)
                group = groups.get(group_key)
                if group is None:
                    cursor = await c.execute("INSERT INTO checklist_groups(checklist_template_id,stable_key,name,version,enabled,user_modified,display_order) VALUES(?,?,?,?,?,?,?)", (checklist_id,group_key[1],str(row["group_name"]),str(row["group_version"]),_bool(row["group_enabled"]),1,int(row["group_order"])))
                    group = {"id": int(cursor.lastrowid), "stable_key": group_key[1]}
                    groups[group_key] = group
                elif group_key not in processed_groups:
                    expected = (str(row["group_name"]),_bool(row["group_enabled"]),int(row["group_order"]))
                    actual = (group["name"],group["enabled"],group["display_order"])
                    if actual != expected:
                        await c.execute("UPDATE checklist_groups SET name=?,enabled=?,display_order=?,version=?,user_modified=1 WHERE id=?", (*expected,_next_version(str(group["version"])),group["id"]))
                processed_groups.add(group_key)
                group_id = int(group["id"])

                item_key = (checklist_key, group_key[1], str(row["item_key"]))
                incoming_items.add(item_key)
                item = items.get(item_key)
                if item is None:
                    cursor = await c.execute("INSERT INTO checklist_items(checklist_group_id,stable_key,display_no,content,starred,version,enabled,user_modified,display_order) VALUES(?,?,?,?,?,?,?,?,?)", (group_id,item_key[2],str(row["display_no"]),str(row["content"]),_bool(row["starred"]),str(row["item_version"]),_bool(row["item_enabled"]),1,int(row["item_order"])))
                    item = {"id": int(cursor.lastrowid), "stable_key": item_key[2]}
                    items[item_key] = item
                elif item_key not in processed_items:
                    expected = (str(row["display_no"]),str(row["content"]),_bool(row["starred"]),_bool(row["item_enabled"]),int(row["item_order"]))
                    actual = (item["display_no"],item["content"],item["starred"],item["enabled"],item["display_order"])
                    if actual != expected:
                        await c.execute("UPDATE checklist_items SET display_no=?,content=?,starred=?,enabled=?,display_order=?,version=?,user_modified=1 WHERE id=?", (*expected,_next_version(str(item["version"])),item["id"]))
                processed_items.add(item_key)
                item_id = int(item["id"])

                key = str(row["issue_key"])
                incoming_keys.add(key)
                old = existing.get(key)
                if old is not None and int(old["item_id"]) != item_id:
                    raise TemplateServiceError("TEMPLATE_IMPORT_HIERARCHY_CONFLICT", "问题稳定键不能移动到其他检查项")
                values = (str(row["title"]),str(row["description"]),str(row["corrective_suggestion"]),str(row["default_severity"]),str(row["placeholders"]),0,_bool(row["issue_enabled"]),int(row["issue_order"]))
                if old is None:
                    await c.execute("INSERT INTO issue_templates(version,title,description,corrective_suggestion,default_severity,placeholders_json,is_default,enabled,user_modified,display_order,checklist_item_id,stable_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (str(row["issue_version"]),*values[:5],values[5],values[6],1,values[7],item_id,key))
                else:
                    actual = (old["title"],old["description"],old["corrective_suggestion"],old["default_severity"],old["placeholders_json"],old["is_default"],old["enabled"],old["display_order"])
                    changed = actual != values
                    version = _next_version(str(old["version"])) if changed else str(old["version"])
                    await c.execute("UPDATE issue_templates SET version=?,title=?,description=?,corrective_suggestion=?,default_severity=?,placeholders_json=?,is_default=?,enabled=?,display_order=?,user_modified=CASE WHEN ? THEN 1 ELSE user_modified END,revision=revision+CASE WHEN ? THEN 1 ELSE 0 END WHERE id=?", (version,*values,int(changed),int(changed),old["id"]))
            for key in existing:
                if key not in incoming_keys and existing[key]["enabled"]:
                    await c.execute("UPDATE issue_templates SET enabled=0,is_default=0,user_modified=1,version=?,revision=revision+1 WHERE id=?", (_next_version(str(existing[key]["version"])),existing[key]["id"]))
            for key, item in items.items():
                if key not in incoming_items and item.get("enabled"):
                    await c.execute("UPDATE checklist_items SET enabled=0,user_modified=1,version=?,revision=revision+1 WHERE id=?", (_next_version(str(item["version"])),item["id"]))
            for key, group in groups.items():
                if key not in incoming_groups and group.get("enabled"):
                    await c.execute("UPDATE checklist_groups SET enabled=0,user_modified=1,version=? WHERE id=?", (_next_version(str(group["version"])),group["id"]))
            for key, checklist in checklists.items():
                if key not in incoming_checklists and checklist.get("enabled"):
                    await c.execute("UPDATE checklist_templates SET enabled=0,user_modified=1,builtin_version=? WHERE id=?", (_next_version(str(checklist["builtin_version"])),checklist["id"]))
            await c.execute("UPDATE template_import_previews SET status='consumed' WHERE id=?", (preview["id"],))
    return {"applied_changes": len(changes)}
