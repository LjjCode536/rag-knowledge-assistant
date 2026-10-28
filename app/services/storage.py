"""上传文件落盘:位置由配置决定,运行时产物不入库(gitignore data/)。"""

from __future__ import annotations

from pathlib import Path


class LocalDocumentStorage:
    """按文档 ID 命名落盘,规避文件名冲突与路径穿越。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def save(self, document_id: str, *, file_name: str, content: bytes) -> Path:
        suffix = Path(file_name).suffix.lower()
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{document_id}{suffix}"
        target.write_bytes(content)
        return target
