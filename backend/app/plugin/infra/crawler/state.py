"""私有状态原子写入；仅依赖标准库。"""

import json
import os
from pathlib import Path
from uuid import uuid4


def write_private_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("私有状态不能使用符号链接")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_private_json(path: Path, *, max_bytes=262144):
    if path.is_symlink():
        raise ValueError("私有状态不能使用符号链接")
    if not path.exists():
        return None
    if path.stat().st_size > max_bytes:
        raise ValueError("私有状态文件过大")
    return json.loads(path.read_text(encoding="utf-8"))
