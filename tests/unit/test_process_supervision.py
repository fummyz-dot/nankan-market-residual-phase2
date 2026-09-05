import tempfile
import unittest
from pathlib import Path

from src.ingestion.process_supervision import ProcessSupervisor
from src.ingestion.prospective_store import connect, initialize_database


class ProcessSupervisionTest(unittest.TestCase):
    def test_stale_progress_fails_supervisor_and_writes_failed_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "market.sqlite"
            initialize_database(db)
            conn = connect(db)
            supervisor = ProcessSupervisor(conn, root / "run", stale_after_seconds=30, progress_stale_seconds=60, clock=lambda: "2026-08-18T00:00:00+00:00")
            worker = supervisor.register_worker(pid=999999)
            supervisor.start_worker(worker, 999999)
            findings = supervisor.audit_freshness("2026-08-18T00:02:00+00:00")
            self.assertEqual(findings[0]["reason"], "STALE_WORKER_DETECTED")
            self.assertEqual(supervisor.finalize(), "FAILED")
            self.assertTrue((root / "run" / "FAILED").exists())
            self.assertEqual(supervisor.orphan_audit(), 0)
            conn.close()

    def test_fresh_heartbeat_with_stale_progress_is_distinct_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "market.sqlite"
            initialize_database(db)
            conn = connect(db)
            clock_value = ["2026-08-18T00:00:00+00:00"]
            supervisor = ProcessSupervisor(conn, root / "run", stale_after_seconds=30, progress_stale_seconds=60, clock=lambda: clock_value[0])
            worker = supervisor.register_worker(pid=999999)
            supervisor.start_worker(worker, 999999)
            supervisor.checkpoint(worker, "partition-001")
            clock_value[0] = "2026-08-18T00:01:30+00:00"
            supervisor.heartbeat(worker)  # fresh heartbeat, unchanged progress
            findings = supervisor.audit_freshness()
            self.assertEqual(findings[0]["reason"], "STALE_PROGRESS_DETECTED")
            self.assertEqual(conn.execute("SELECT checkpoint_value FROM process_checkpoints WHERE worker_id=? AND is_last_successful=1", (worker,)).fetchone()[0], "partition-001")
            conn.close()
