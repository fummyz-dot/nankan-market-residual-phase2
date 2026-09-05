from __future__ import annotations

import builtins
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.validation.stage2_causal_access_guard import (
    CausalAccessBoundaryError, FORBIDDEN_EXACT_PATHS, PhaseAAccessGuard,
)


class PhaseAAccessGuardTests(unittest.TestCase):
    def test_all_forbidden_direct_paths_rejected_for_open_and_sqlite(self) -> None:
        with PhaseAAccessGuard() as guard:
            for path in FORBIDDEN_EXACT_PATHS:
                with self.assertRaises(CausalAccessBoundaryError): builtins.open(path, "rb")
                with self.assertRaises(CausalAccessBoundaryError): sqlite3.connect(path)
        self.assertEqual(len(guard.denied_attempts), 8)

    def test_all_forbidden_sqlite_uris_rejected(self) -> None:
        with PhaseAAccessGuard() as guard:
            for path in FORBIDDEN_EXACT_PATHS:
                with self.assertRaises(CausalAccessBoundaryError): sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.assertEqual(len(guard.denied_attempts), 4)

    def test_pathlib_and_io_reads_rejected(self) -> None:
        path = FORBIDDEN_EXACT_PATHS[0]
        with PhaseAAccessGuard():
            with self.assertRaises(CausalAccessBoundaryError): path.read_bytes()
            with self.assertRaises(CausalAccessBoundaryError): io.open(path, "rb")

    def test_synthetic_sqlite_allowed_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory, PhaseAAccessGuard() as guard:
            path = Path(directory) / "fixture.sqlite"
            connection = sqlite3.connect(path); connection.close()
        self.assertEqual(guard.sqlite_paths_opened, [str(path.resolve())])
        self.assertEqual(guard.audit()["postcutoff_live_db_open_count"], 0)

    def test_patch_is_scoped_and_network_flag_rejected(self) -> None:
        original = sqlite3.connect
        with PhaseAAccessGuard(): self.assertIsNot(sqlite3.connect, original)
        self.assertIs(sqlite3.connect, original)
        with self.assertRaises(CausalAccessBoundaryError):
            with PhaseAAccessGuard(network_access=True): pass


if __name__ == "__main__": unittest.main()
