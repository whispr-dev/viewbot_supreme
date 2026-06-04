from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Any


@contextmanager
def atomic_text_writer(path: Path, encoding: str = "utf-8") -> Iterator[object]:
    """Transaction-like text file writer: complete temp write or original remains."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_json_atomic(path: Path, payload: Any) -> None:
    with atomic_text_writer(path) as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")
