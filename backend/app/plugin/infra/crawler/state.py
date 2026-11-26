"""私有状态原子写入；仅依赖标准库。"""

import json
import os
import time
from pathlib import Path
from uuid import uuid4


def write_private_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("私有状态不能使用符号链接")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if os.name != 'nt' or getattr(error, 'winerror', None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def write_private_json(path: Path, value):
    write_private_bytes(path, json.dumps(value, ensure_ascii=False).encode('utf-8'))


def read_private_json(path: Path, *, max_bytes=262144):
    if path.is_symlink():
        raise ValueError("私有状态不能使用符号链接")
    if not path.exists():
        return None
    if path.stat().st_size > max_bytes:
        raise ValueError("私有状态文件过大")
    return json.loads(path.read_text(encoding="utf-8"))
