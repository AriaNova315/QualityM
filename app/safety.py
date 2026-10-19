from __future__ import annotations

import hashlib
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path


XLSX_MAGIC = b"PK\x03\x04"


class UploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_entries: int = 5_000
    max_uncompressed_bytes: int = 150 * 1024 * 1024
    max_compression_ratio: float = 250.0


def safe_upload_filename(filename: str) -> str:
    if not filename or Path(filename).name != filename or "\\" in filename or "\x00" in filename:
        raise UploadValidationError("UPLOAD_UNSAFE_FILENAME", "上传文件名不安全")
    if Path(filename).suffix.lower() != ".xlsx":
        raise UploadValidationError("UPLOAD_UNSUPPORTED_TYPE", "仅支持 .xlsx 文件")
    return filename


def validate_xlsx(path: Path, limits: UploadLimits = UploadLimits()) -> str:
    size = path.stat().st_size
    if size > limits.max_bytes:
        raise UploadValidationError("UPLOAD_TOO_LARGE", "文件超过上传大小限制")
    if size < len(XLSX_MAGIC) or path.read_bytes()[:4] != XLSX_MAGIC:
        raise UploadValidationError("UPLOAD_INVALID_XLSX", "文件不是有效的 XLSX 压缩包")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > limits.max_entries:
                raise UploadValidationError("UPLOAD_ZIP_TOO_MANY_ENTRIES", "XLSX 内部文件数量异常")
            total_uncompressed = sum(member.file_size for member in members)
            total_compressed = sum(max(member.compress_size, 1) for member in members)
            if total_uncompressed > limits.max_uncompressed_bytes:
                raise UploadValidationError("UPLOAD_ZIP_TOO_LARGE", "XLSX 解压后大小异常")
            if total_uncompressed / max(total_compressed, 1) > limits.max_compression_ratio:
                raise UploadValidationError("UPLOAD_ZIP_BOMB", "XLSX 压缩比异常")
            names = {member.filename for member in members}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UploadValidationError("UPLOAD_INVALID_XLSX", "XLSX 缺少必要结构")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise UploadValidationError("UPLOAD_CORRUPT_XLSX", f"XLSX 内容损坏：{bad_member}")
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("UPLOAD_INVALID_XLSX", "文件不是有效的 XLSX") from exc
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
