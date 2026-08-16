from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agentsassemble.application.engine_instance_lock import (
    ENGINE_LOCK_FILENAME,
    EngineInstanceLock,
    EngineLockInheritanceError,
)


_PROBE = r"""
import sys
from pathlib import Path
from agentsassemble.application.engine_instance_lock import (
    EngineAlreadyRunningError,
    EngineInstanceLock,
)
try:
    lock = EngineInstanceLock.acquire(Path(sys.argv[1]))
except EngineAlreadyRunningError:
    print("blocked")
    raise SystemExit(23)
else:
    print("acquired")
    lock.close()
"""


@unittest.skipUnless(os.name == "posix", "Engine file locking is POSIX-only.")
class EngineInstanceLockTests(unittest.TestCase):
    def _probe(self, root: Path) -> subprocess.CompletedProcess[str]:
        repository_root = Path(__file__).resolve().parents[1]
        environment = dict(os.environ)
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(repository_root), existing_pythonpath) if item
        )
        return subprocess.run(
            [sys.executable, "-c", _PROBE, str(root)],
            cwd=repository_root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )

    def test_unrelated_process_cannot_open_the_same_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with EngineInstanceLock.acquire(root):
                blocked = self._probe(root)
            acquired = self._probe(root)

        self.assertEqual(blocked.returncode, 23, blocked.stderr)
        self.assertEqual(blocked.stdout.strip(), "blocked")
        self.assertEqual(acquired.returncode, 0, acquired.stderr)
        self.assertEqual(acquired.stdout.strip(), "acquired")

    def test_rolling_handoff_keeps_the_lock_until_the_last_owner_closes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            predecessor = EngineInstanceLock.acquire(root)
            inherited_fd = os.dup(predecessor.fileno())
            replacement = EngineInstanceLock.acquire(root, inherited_fd=inherited_fd)
            predecessor.close()
            still_blocked = self._probe(root)
            replacement.close()
            released = self._probe(root)

        self.assertEqual(still_blocked.returncode, 23, still_blocked.stderr)
        self.assertEqual(released.returncode, 0, released.stderr)

    def test_inherited_descriptor_must_match_the_requested_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = EngineInstanceLock.acquire(Path(first_dir))
            inherited_fd = os.dup(first.fileno())
            try:
                with self.assertRaises(EngineLockInheritanceError):
                    EngineInstanceLock.acquire(Path(second_dir), inherited_fd=inherited_fd)
            finally:
                first.close()

    def test_stale_lock_file_is_harmless_after_the_owner_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / ENGINE_LOCK_FILENAME
            path.write_text("stale metadata\n", encoding="utf-8")
            with EngineInstanceLock.acquire(root) as lock:
                self.assertEqual(lock.path, path)
            self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
