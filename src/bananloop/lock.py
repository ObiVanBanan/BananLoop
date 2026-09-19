from __future__ import annotations

import json
import os
from pathlib import Path
from typing import BinaryIO

from .contracts import utc_now


class CampaignLockError(RuntimeError):
    pass


class CampaignLock:
    """Non-blocking single-writer lock for one campaign state directory."""

    def __init__(self, path: Path):
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> "CampaignLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fh = self.path.open("a+b")
        except OSError as exc:
            raise CampaignLockError(
                f"cannot open campaign lock (already locked or inaccessible): {self.path}"
            ) from exc
        try:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise CampaignLockError(
                        f"campaign is already locked by another writer: {self.path}"
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise CampaignLockError(
                        f"campaign is already locked by another writer: {self.path}"
                    ) from exc

            owner = {
                "pid": os.getpid(),
                "acquired_at": utc_now(),
            }
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(owner, sort_keys=True).encode("utf-8"))
            fh.flush()
            fh.seek(0)
            self._file = fh
            return self
        except Exception:
            fh.close()
            raise

    def release(self) -> None:
        fh = self._file
        if fh is None:
            return
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
            self._file = None

    def __enter__(self) -> "CampaignLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
