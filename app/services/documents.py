from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Tuple

from docx import Document
from fastapi import UploadFile

from ..config import settings


class DocumentService:
    def extract_from_upload(self, upload: UploadFile) -> Tuple[str, str]:
        filename = upload.filename or "document.txt"
        suffix = Path(filename).suffix.lower()
        if suffix not in {".docx", ".txt"}:
            raise ValueError("仅支持 .docx 或 .txt 文件")
        payload = upload.file.read()
        max_bytes = settings.max_upload_mb * 1024 * 1024
        if max_bytes and len(payload) > max_bytes:
            raise ValueError(f"上传文件超过 {settings.max_upload_mb} MB 上限")
        if suffix == ".docx":
            return self._extract_docx(payload), filename
        return self._decode_text(payload), filename

    def _extract_docx(self, payload: bytes) -> str:
        document = Document(BytesIO(payload))
        text = "\n".join(paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip())
        return text

    def _decode_text(self, payload: bytes) -> str:
        for encoding in ("utf-8", "gb18030"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")

    def persist_upload(self, file_name: str, payload: bytes) -> Path:
        safe_name = file_name.replace("/", "_").replace("\\", "_")
        target = settings.upload_dir / safe_name
        target.write_bytes(payload)
        return target


document_service = DocumentService()
