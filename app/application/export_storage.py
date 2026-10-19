from __future__ import annotations

import json
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


QUARANTINE_DIRNAME = ".delete-quarantine"
STAGING_DIRNAME = ".staging"
MANIFEST_FILENAME = "manifest.json"


class ExportStorageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class QuarantinedDirectory:
    original_name: str
    isolated_name: str


@dataclass(frozen=True, slots=True)
class DeleteQuarantine:
    path: Path
    project_version_id: int
    directories: tuple[QuarantinedDirectory, ...]


def _direct_child(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    if not candidate.is_absolute():
        raise ExportStorageError(
            "EXPORT_STORAGE_PATH_INVALID", "导出目录不是绝对路径"
        )
    if candidate.is_symlink() or candidate.parent.resolve() != resolved_root:
        raise ExportStorageError(
            "EXPORT_STORAGE_PATH_INVALID", "导出目录不在受控输出根目录中"
        )
    return candidate


def _safe_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ExportStorageError(
            "EXPORT_STORAGE_MANIFEST_INVALID", f"隔离清单中的 {field} 不安全"
        )
    return value


def _manifest_payload(
    project_version_id: int, directories: tuple[QuarantinedDirectory, ...]
) -> dict:
    return {
        "version": 1,
        "project_version_id": project_version_id,
        "directories": [
            {
                "original_name": item.original_name,
                "isolated_name": item.isolated_name,
            }
            for item in directories
        ],
    }


def _write_manifest(quarantine: DeleteQuarantine) -> None:
    temporary = quarantine.path / f".{MANIFEST_FILENAME}.tmp"
    temporary.write_text(
        json.dumps(
            _manifest_payload(
                quarantine.project_version_id, quarantine.directories
            ),
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, quarantine.path / MANIFEST_FILENAME)


def load_quarantine(path: Path) -> DeleteQuarantine:
    if path.is_symlink() or not path.is_dir():
        raise ExportStorageError(
            "EXPORT_STORAGE_MANIFEST_INVALID", "隔离记录不是受控目录"
        )
    try:
        payload = json.loads((path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("unsupported manifest version")
        project_version_id = int(payload["project_version_id"])
        raw_directories = payload["directories"]
        if project_version_id < 1 or not isinstance(raw_directories, list):
            raise ValueError("invalid manifest fields")
        directories = tuple(
            QuarantinedDirectory(
                _safe_name(item["original_name"], "original_name"),
                _safe_name(item["isolated_name"], "isolated_name"),
            )
            for item in raw_directories
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise ExportStorageError(
            "EXPORT_STORAGE_MANIFEST_INVALID", "无法读取导出隔离恢复清单"
        ) from exc
    return DeleteQuarantine(path, project_version_id, directories)


def prepare_delete_quarantine(
    generated_dir: Path,
    project_version_id: int,
    output_dirs: Iterable[str],
) -> DeleteQuarantine | None:
    generated_dir.mkdir(parents=True, exist_ok=True)
    existing: list[Path] = []
    seen: set[Path] = set()
    for raw_path in output_dirs:
        candidate = _direct_child(generated_dir, Path(raw_path))
        if candidate in seen or not candidate.exists():
            continue
        if not candidate.is_dir():
            raise ExportStorageError(
                "EXPORT_STORAGE_PATH_INVALID", "导出记录指向的不是目录"
            )
        seen.add(candidate)
        existing.append(candidate)
    if not existing:
        return None

    quarantine_root = generated_dir / QUARANTINE_DIRNAME
    quarantine_root.mkdir(parents=True, exist_ok=True)
    operation_dir = quarantine_root / (
        f"V{project_version_id}-{secrets.token_hex(8)}"
    )
    isolated_root = operation_dir / "files"
    directories = tuple(
        QuarantinedDirectory(path.name, f"{index:04d}")
        for index, path in enumerate(existing, start=1)
    )
    quarantine = DeleteQuarantine(operation_dir, project_version_id, directories)
    try:
        isolated_root.mkdir(parents=True, exist_ok=False)
        _write_manifest(quarantine)
    except OSError as exc:
        shutil.rmtree(operation_dir, ignore_errors=True)
        raise ExportStorageError(
            "EXPORT_STORAGE_MANIFEST_WRITE_FAILED", "无法创建导出隔离恢复清单"
        ) from exc

    moved: list[QuarantinedDirectory] = []
    try:
        for original, item in zip(existing, directories, strict=True):
            os.replace(original, isolated_root / item.isolated_name)
            moved.append(item)
    except OSError as exc:
        recovery_failed = False
        for item in reversed(moved):
            try:
                os.replace(
                    isolated_root / item.isolated_name,
                    generated_dir / item.original_name,
                )
            except OSError:
                recovery_failed = True
        if not recovery_failed:
            shutil.rmtree(operation_dir, ignore_errors=True)
        code = (
            "EXPORT_STORAGE_RECOVERY_PENDING"
            if recovery_failed
            else "EXPORT_STORAGE_ISOLATION_FAILED"
        )
        raise ExportStorageError(code, "无法隔离项目的历史导出目录") from exc
    return quarantine


def restore_quarantine(generated_dir: Path, quarantine: DeleteQuarantine) -> None:
    isolated_root = quarantine.path / "files"
    try:
        for item in quarantine.directories:
            original = generated_dir / item.original_name
            isolated = isolated_root / item.isolated_name
            if isolated.exists():
                if original.exists() or original.is_symlink():
                    raise ExportStorageError(
                        "EXPORT_STORAGE_RESTORE_CONFLICT", "恢复导出目录时目标已存在"
                    )
                os.replace(isolated, original)
            elif not original.exists():
                raise ExportStorageError(
                    "EXPORT_STORAGE_RESTORE_MISSING", "隔离目录和原导出目录均已缺失"
                )
        shutil.rmtree(quarantine.path)
    except OSError as exc:
        raise ExportStorageError(
            "EXPORT_STORAGE_RESTORE_FAILED", "无法恢复隔离的导出目录"
        ) from exc


def cleanup_quarantine(quarantine: DeleteQuarantine) -> bool:
    try:
        shutil.rmtree(quarantine.path)
    except OSError:
        return False
    return True


def list_quarantines(generated_dir: Path) -> tuple[DeleteQuarantine, ...]:
    root = generated_dir / QUARANTINE_DIRNAME
    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise ExportStorageError(
            "EXPORT_STORAGE_QUARANTINE_INVALID", "导出隔离根目录不安全"
        )
    try:
        paths = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ExportStorageError(
            "EXPORT_STORAGE_QUARANTINE_READ_FAILED", "无法扫描导出隔离目录"
        ) from exc
    return tuple(load_quarantine(path) for path in paths)


def cleanup_abandoned_staging(generated_dir: Path) -> tuple[int, int]:
    staging_root = generated_dir / STAGING_DIRNAME
    if not staging_root.exists():
        return 0, 0
    if staging_root.is_symlink() or not staging_root.is_dir():
        raise ExportStorageError(
            "EXPORT_STORAGE_STAGING_INVALID", "导出 staging 根目录不安全"
        )
    removed = 0
    pending = 0
    for path in tuple(staging_root.iterdir()):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
            removed += 1
        except OSError:
            pending += 1
    return removed, pending
