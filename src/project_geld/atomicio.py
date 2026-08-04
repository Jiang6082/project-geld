"""Atomic writes for paper-critical state and output files.

A crash or interruption partway through writing a state file (rebalance state,
performance history, intraday/shadow cycle state) can corrupt live paper state.
These helpers write to a temporary file in the same directory, flush + fsync, then
``os.replace`` onto the target — an atomic rename on the same filesystem, so the
target is never left half-written.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_write_dataframe_csv(frame: Any, path: str | Path, **to_csv_kwargs: Any) -> None:
    """Atomically write a pandas DataFrame to CSV.

    ``append_performance_snapshot`` does a read-modify-write of the whole history
    file, so a partial write there would lose the entire performance log. Rendering
    to a string first and replacing atomically avoids that.
    """
    to_csv_kwargs.setdefault("index", False)
    atomic_write_text(path, frame.to_csv(**to_csv_kwargs))
