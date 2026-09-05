import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[2] / "src/audit/p2_m01_build_history_context.py"
SPEC = importlib.util.spec_from_file_location("p2_m01", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class P2M01ContextIdentityTests(unittest.TestCase):
    def test_identity_key_is_deterministic_and_exact(self) -> None:
        self.assertEqual(MODULE.horse_identity("馬Ａ", "2020-01-02"), MODULE.horse_identity("馬Ａ", "2020-01-02"))
        self.assertNotEqual(MODULE.horse_identity("馬Ａ", "2020-01-02"), MODULE.horse_identity("馬A", "2020-01-02"))
        self.assertNotEqual(MODULE.horse_identity("馬Ａ", "2020-01-02"), MODULE.horse_identity("馬Ａ", "2020-01-03"))

    def test_no_name_only_or_fuzzy_join_path_exists(self) -> None:
        source = inspect.getsource(MODULE.horse_identity)
        self.assertIn("name", source)
        self.assertIn("birth_date", source)
        self.assertNotIn("normalize", source.lower())
        self.assertEqual(MODULE.venue_class("帯広ば"), "BANEI")
        self.assertEqual(MODULE.venue_class("未確認会場"), "UNKNOWN")

    def test_existing_formal_db_is_never_silently_replaced(self) -> None:
        original_formal, original_temp = MODULE.FORMAL_DB, MODULE.TEMP_DB
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                formal = root / "formal.sqlite"
                formal.write_text("existing", encoding="utf-8")
                MODULE.FORMAL_DB, MODULE.TEMP_DB = formal, root / "temporary.sqlite"
                with self.assertRaisesRegex(MODULE.BuildError, "FORMAL_DB_ALREADY_EXISTS"):
                    MODULE.build()
                self.assertEqual(formal.read_text(encoding="utf-8"), "existing")
        finally:
            MODULE.FORMAL_DB, MODULE.TEMP_DB = original_formal, original_temp

    def test_atomic_final_promotion_is_explicitly_implemented(self) -> None:
        source = inspect.getsource(MODULE.finalize_and_promote)
        self.assertIn("os.replace(TEMP_DB, FORMAL_DB)", source)
