from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.audit.p2s_job007_stage2_locked_replay import (
    AUTHORITY_HASHES, Job007Error, guard_data_path, validate_phase_a_marker,
)


class Job007AuditTests(unittest.TestCase):
    def test_payout_and_settlement_paths_rejected(self) -> None:
        for value in ("db/payouts.sqlite", "outputs/settlement.json"):
            with self.assertRaisesRegex(Job007Error, "FORBIDDEN"): guard_data_path(Path(value))
        guard_data_path(Path("db/market_snapshot.sqlite"))

    def test_marker_requires_current_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marker.json"
            path.write_text(json.dumps({"status": "PHASE_A_PASS", "implementation_git_commit": "bad"}))
            with patch("src.audit.p2s_job007_stage2_locked_replay.git", return_value="head"):
                with self.assertRaisesRegex(Job007Error, "HEAD_MISMATCH"): validate_phase_a_marker(path)

    def test_authority_binding_names_complete(self) -> None:
        self.assertEqual(set(AUTHORITY_HASHES), {"stage2_json_sha256", "stage2_md_sha256", "amendment_json_sha256", "amendment_md_sha256", "cleanroom_json_sha256", "cleanroom_md_sha256", "design_sha256"})

    def test_phase_b_requires_marker_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError): validate_phase_a_marker(Path(directory) / "missing.json")


if __name__ == "__main__": unittest.main()
