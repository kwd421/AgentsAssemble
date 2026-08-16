"""One authoritative engine process per user-data root.

SQLite protects its files from concurrent corruption, but two AgentsAssemble
processes would still own independent WebSocket brokers and provider runtimes.
This advisory lock therefore spans the complete GUI-engine lifetime. Rolling
restart transfers the same locked open file description to the replacement so
only that controlled overlap is allowed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - supported desktop hosts are POSIX
    fcntl = None  # type: ignore[assignment]


ENGINE_LOCK_FILENAME = ".engine.lock"


class EngineInstanceLockError(RuntimeError):
    """The engine lifetime lock could not be established safely."""


class EngineAlreadyRunningError(EngineInstanceLockError):
    """Another authoritative engine owns this user-data root."""


class EngineLockInheritanceError(EngineInstanceLockError):
    """A rolling replacement inherited the wrong or an invalid lock handle."""


class EngineInstanceLock:
    """Process-lifetime exclusive lock for one resolved output root.

    Do not call ``LOCK_UN`` during close. A rolling parent and child hold
    descriptors for the same open file description; closing each descriptor
    lets the kernel release the lock only after the final owner exits.
    """

    def __init__(self, root: Path, file_descriptor: int) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / ENGINE_LOCK_FILENAME
        self._file_descriptor = int(file_descriptor)

    @classmethod
    def acquire(
        cls,
        root: Path,
        *,
        inherited_fd: int | None = None,
    ) -> EngineInstanceLock:
        if fcntl is None:
            raise EngineInstanceLockError(
                "AgentsAssemble requires POSIX advisory file locking on this platform."
            )
        resolved_root = Path(root).expanduser().resolve()
        resolved_root.mkdir(parents=True, exist_ok=True)
        lock_path = resolved_root / ENGINE_LOCK_FILENAME
        if inherited_fd is not None:
            return cls._adopt_inherited(
                resolved_root,
                lock_path,
                int(inherited_fd),
            )
        return cls._acquire_new(resolved_root, lock_path)

    @classmethod
    def _acquire_new(cls, root: Path, lock_path: Path) -> EngineInstanceLock:
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise EngineInstanceLockError(
                f"Could not open the AgentsAssemble engine lock at {lock_path}."
            ) from error
        try:
            metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise EngineInstanceLockError(
                    "The AgentsAssemble engine lock path is not a regular file."
                )
            try:
                os.fchmod(file_descriptor, 0o600)
            except OSError:
                # The lock remains safe when an unusual filesystem refuses a
                # permission tightening operation; acquisition still decides
                # whether another engine owns the root.
                pass
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise EngineAlreadyRunningError(
                    "Another AgentsAssemble engine is already using this data directory."
                ) from error
            os.set_inheritable(file_descriptor, False)
            return cls(root, file_descriptor)
        except BaseException:
            os.close(file_descriptor)
            raise

    @classmethod
    def _adopt_inherited(
        cls,
        root: Path,
        lock_path: Path,
        file_descriptor: int,
    ) -> EngineInstanceLock:
        if file_descriptor < 0:
            raise EngineLockInheritanceError(
                "Rolling restart inherited an invalid engine lock descriptor."
            )
        try:
            try:
                inherited_metadata = os.fstat(file_descriptor)
                path_metadata = os.stat(lock_path, follow_symlinks=False)
            except OSError as error:
                raise EngineLockInheritanceError(
                    "Rolling restart inherited an invalid engine lock descriptor."
                ) from error
            if not stat.S_ISREG(inherited_metadata.st_mode) or not stat.S_ISREG(
                path_metadata.st_mode
            ):
                raise EngineLockInheritanceError(
                    "Rolling restart engine lock is not a regular file."
                )
            if (
                inherited_metadata.st_dev != path_metadata.st_dev
                or inherited_metadata.st_ino != path_metadata.st_ino
            ):
                raise EngineLockInheritanceError(
                    "Rolling restart engine lock does not belong to this data directory."
                )
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise EngineLockInheritanceError(
                    "Rolling restart did not inherit the active engine lock."
                ) from error
            os.set_inheritable(file_descriptor, False)
            return cls(root, file_descriptor)
        except BaseException:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            raise

    def fileno(self) -> int:
        if self._file_descriptor < 0:
            raise EngineInstanceLockError("The AgentsAssemble engine lock is closed.")
        return self._file_descriptor

    def close(self) -> None:
        file_descriptor = self._file_descriptor
        self._file_descriptor = -1
        if file_descriptor < 0:
            return
        try:
            os.close(file_descriptor)
        except OSError:
            pass

    def __enter__(self) -> EngineInstanceLock:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "ENGINE_LOCK_FILENAME",
    "EngineAlreadyRunningError",
    "EngineInstanceLock",
    "EngineInstanceLockError",
    "EngineLockInheritanceError",
]
