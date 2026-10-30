"""Regression guard proving test isolation for canonical-schedule configuration tests.

Verifies:
1. Outer process-level isolation redirects default STATE_PATH and USER_DEFAULTS_PATH to a temporary location.
2. The live path guard strictly intercepts and prevents any disk write, append, or atomic update targeting
   live configuration paths (current_scenario.json, user_defaults.json, lock files).
3. Path.write_text, Path.write_bytes, and atomic replacement via os.replace raise LivePathWriteError before mutation.
4. Read-only access to live evidence files is permitted without mutation.
5. Ordinary temporary file writes and atomic replacements remain permitted and functional.
6. An unpatched persistence call safely lands in the outer temporary directory without touching live files.
7. Live state and user defaults remain unmutated across the run, compared against initial read-only hashes.
8. Immutable historical fixture integrity is preserved separately, avoiding unrelated weather assertions.
"""
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import test_support
from feasibility import assumptions, engine, shared_state

# Snapshot of live user-editable configuration hashes captured read-only at module initialization.
INITIAL_LIVE_HASHES = {
    'current_scenario': hashlib.sha256(test_support.LIVE_STATE_PATH.read_bytes()).hexdigest(),
    'user_defaults': hashlib.sha256(test_support.LIVE_USER_DEFAULTS_PATH.read_bytes()).hexdigest(),
}

# Mutation canaries are generated, not numerical golden references. The separate
# public/private oracles compare model output; this guard rejects test mutations.
SYNTHETIC_FIXTURE_PATH = test_support.LIVE_DATA_DIR.parents[1] / 'compatibility/ac_heating_compatibility_fixtures.npz'
EXPECTED_FIXTURE_HASHES = {'fixtures_npz': hashlib.sha256(SYNTHETIC_FIXTURE_PATH.read_bytes()).hexdigest()}
EXPECTED_HASHES = EXPECTED_FIXTURE_HASHES


class TestIsolationRegressionGuard(unittest.TestCase):
    """Guard proving that test suites cannot write to live scenario or user defaults files."""

    def test_outer_process_defaults_and_state_paths_are_isolated(self):
        """Default paths for STATE_PATH and USER_DEFAULTS_PATH point to a temporary directory."""
        self.assertNotEqual(shared_state.STATE_PATH, test_support.LIVE_STATE_PATH)
        self.assertNotEqual(assumptions.USER_DEFAULTS_PATH, test_support.LIVE_USER_DEFAULTS_PATH)
        self.assertEqual(shared_state.STATE_PATH, test_support.PROCESS_STATE_PATH)
        self.assertEqual(assumptions.USER_DEFAULTS_PATH, test_support.PROCESS_USER_DEFAULTS_PATH)

    def test_guard_blocks_open_write_and_append_to_live_files(self):
        """Any direct file open in write or append mode targeting live files is blocked."""
        for target in (test_support.LIVE_STATE_PATH, test_support.LIVE_USER_DEFAULTS_PATH):
            for mode in ('w', 'wb', 'a', 'a+', 'r+'):
                with self.subTest(target=target.name, mode=mode, opener='open'):
                    with self.assertRaises(test_support.LivePathWriteError):
                        open(target, mode)
                with self.subTest(target=target.name, mode=mode, opener='io.open'):
                    with self.assertRaises(test_support.LivePathWriteError):
                        io.open(target, mode)

    def test_guard_blocks_path_write_text_and_write_bytes(self):
        """pathlib.Path.write_text and write_bytes targeting live files raise LivePathWriteError before mutation."""
        for target in (test_support.LIVE_STATE_PATH, test_support.LIVE_USER_DEFAULTS_PATH):
            with self.subTest(target=target.name, op='write_text'):
                sha_before = hashlib.sha256(target.read_bytes()).hexdigest()
                with self.assertRaises(test_support.LivePathWriteError):
                    target.write_text("blocked write_text attempt\n")
                sha_after = hashlib.sha256(target.read_bytes()).hexdigest()
                self.assertEqual(sha_before, sha_after, f"Live file {target.name} was mutated by write_text!")

            with self.subTest(target=target.name, op='write_bytes'):
                sha_before = hashlib.sha256(target.read_bytes()).hexdigest()
                with self.assertRaises(test_support.LivePathWriteError):
                    target.write_bytes(b"blocked write_bytes attempt\n")
                sha_after = hashlib.sha256(target.read_bytes()).hexdigest()
                self.assertEqual(sha_before, sha_after, f"Live file {target.name} was mutated by write_bytes!")

    def test_guard_blocks_atomic_replace_to_live_files(self):
        """os.replace targeting live files raises LivePathWriteError before mutation, keeping harmless source intact."""
        for target in (test_support.LIVE_STATE_PATH, test_support.LIVE_USER_DEFAULTS_PATH):
            with self.subTest(target=target.name):
                sha_before = hashlib.sha256(target.read_bytes()).hexdigest()
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    src_path = Path(tmp.name)
                    src_path.write_text("harmless temporary source payload")
                try:
                    with self.assertRaises(test_support.LivePathWriteError):
                        os.replace(src_path, target)
                    # Verify harmless tempfile source remains after rejection
                    self.assertTrue(src_path.exists(), "Source tempfile must remain after rejection")
                    self.assertEqual(src_path.read_text(), "harmless temporary source payload")
                    # Verify live destination was not mutated
                    sha_after = hashlib.sha256(target.read_bytes()).hexdigest()
                    self.assertEqual(sha_before, sha_after, f"Live file {target.name} was mutated by os.replace!")
                finally:
                    if src_path.exists():
                        src_path.unlink()

    def test_guard_permits_read_only_access_to_live_files(self):
        """Read-only access to live files succeeds without error."""
        for target in (test_support.LIVE_STATE_PATH, test_support.LIVE_USER_DEFAULTS_PATH):
            for mode in ('r', 'rb'):
                with self.subTest(target=target.name, mode=mode, opener='open'):
                    with open(target, mode) as f:
                        data = f.read()
                        self.assertTrue(len(data) > 0)
                with self.subTest(target=target.name, mode=mode, opener='io.open'):
                    with io.open(target, mode) as f:
                        data = f.read()
                        self.assertTrue(len(data) > 0)
            with self.subTest(target=target.name, op='read_text'):
                data = target.read_text()
                self.assertTrue(len(data) > 0)
            with self.subTest(target=target.name, op='read_bytes'):
                data = target.read_bytes()
                self.assertTrue(len(data) > 0)

    def test_guard_permits_ordinary_temp_writes(self):
        """Ordinary writes and atomic replacements to temporary paths remain functional."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            tmp_path.write_text("sample temporary text")
            self.assertEqual(tmp_path.read_text(), "sample temporary text")
            tmp_path.write_bytes(b"sample temporary bytes")
            self.assertEqual(tmp_path.read_bytes(), b"sample temporary bytes")

            with io.open(tmp_path, 'w') as f:
                f.write("io.open text")
            self.assertEqual(tmp_path.read_text(), "io.open text")

            with tempfile.NamedTemporaryFile(delete=False) as tmp2:
                tmp2_path = Path(tmp2.name)
            try:
                tmp2_path.write_text("replacement payload")
                os.replace(tmp2_path, tmp_path)
                self.assertEqual(tmp_path.read_text(), "replacement payload")
                self.assertFalse(tmp2_path.exists())
            finally:
                if tmp2_path.exists():
                    tmp2_path.unlink()
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_guard_blocks_atomic_write_to_live_state(self):
        """shared_state._atomic_write targeting live state is intercepted and blocked."""
        with self.assertRaises(test_support.LivePathWriteError):
            shared_state._atomic_write(test_support.LIVE_STATE_PATH, {'settings': {}})

    def test_guard_blocks_save_user_default_to_live_defaults(self):
        """assumptions.save_user_default targeting live user defaults is intercepted and blocked."""
        with self.assertRaises(test_support.LivePathWriteError):
            assumptions.save_user_default('scop', 2.0, engine.validate, path=test_support.LIVE_USER_DEFAULTS_PATH)

    def test_guard_blocks_reset_user_default_to_live_defaults(self):
        """assumptions.reset_user_default targeting live user defaults is intercepted and blocked."""
        with self.assertRaises(test_support.LivePathWriteError):
            assumptions.reset_user_default('scop', path=test_support.LIVE_USER_DEFAULTS_PATH)

    def test_unpatched_persistence_operations_target_outer_temp_not_live(self):
        """If an author calls update_state without per-test patching, writes safely land in outer temp."""
        sha_before = hashlib.sha256(test_support.LIVE_STATE_PATH.read_bytes()).hexdigest()

        # Perform update_state without explicit per-test patching
        shared_state.update_state({'settings': {'loan_years': 5}})

        # Verify the write landed in outer temporary path
        self.assertTrue(test_support.PROCESS_STATE_PATH.exists())
        written = json.loads(test_support.PROCESS_STATE_PATH.read_text())
        self.assertEqual(written['settings']['loan_years'], 5)

        # Verify live state on disk is completely untouched
        sha_after = hashlib.sha256(test_support.LIVE_STATE_PATH.read_bytes()).hexdigest()
        self.assertEqual(sha_before, sha_after)

    def test_live_file_hashes_strictly_conserved(self):
        """Live state and user defaults remain unmutated; immutable fixtures retain integrity."""
        files = {
            'current_scenario': test_support.LIVE_STATE_PATH,
            'user_defaults': test_support.LIVE_USER_DEFAULTS_PATH,
        }
        for name, path in files.items():
            self.assertTrue(path.exists(), f"File {path} must exist")
            actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                actual_sha,
                INITIAL_LIVE_HASHES[name],
                f"Live file {path.name} was mutated across test run! "
                f"Initial: {INITIAL_LIVE_HASHES[name]}, got: {actual_sha}",
            )

        fixture_path = SYNTHETIC_FIXTURE_PATH
        self.assertTrue(fixture_path.exists(), f"File {fixture_path} must exist")
        actual_sha = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
        self.assertEqual(
            actual_sha,
            EXPECTED_FIXTURE_HASHES['fixtures_npz'],
            f"Fixture file {fixture_path.name} was mutated! Expected {EXPECTED_FIXTURE_HASHES['fixtures_npz']}, got {actual_sha}",
        )

    def test_canonical_import_names_resolve_to_same_objects_and_temp_paths(self):
        import test_support as ts1
        from tests import test_support as ts2

        self.assertIs(ts1, ts2)
        self.assertIs(ts1.IsolatedDefaultsTestCase, ts2.IsolatedDefaultsTestCase)
        self.assertIs(ts1.LivePathWriteError, ts2.LivePathWriteError)
        self.assertEqual(ts1.PROCESS_STATE_PATH, ts2.PROCESS_STATE_PATH)
        self.assertEqual(ts1.PROCESS_USER_DEFAULTS_PATH, ts2.PROCESS_USER_DEFAULTS_PATH)

    def test_setup_teardown_and_cleanup_failures_cannot_restore_live_paths(self):
        from tests.test_user_defaults import UserDefaultsTests

        class FailSetUp(UserDefaultsTests):
            def setUp(self):
                super().setUp()
                raise RuntimeError("setup fail")

        class FailTearDown(UserDefaultsTests):
            def tearDown(self):
                super().tearDown()
                raise RuntimeError("teardown fail")

        class FailCleanup(UserDefaultsTests):
            def setUp(self):
                super().setUp()
                self.addCleanup(self._fail)
            def _fail(self):
                raise RuntimeError("cleanup fail")

        for cls in (FailSetUp, FailTearDown, FailCleanup):
            with self.subTest(case=cls.__name__):
                res = unittest.TestResult()
                cls('test_save_is_atomic_preserves_factory_and_other_keys').run(res)
                self.assertFalse(res.wasSuccessful())
                self.assertNotEqual(assumptions.USER_DEFAULTS_PATH, test_support.LIVE_USER_DEFAULTS_PATH)
                self.assertNotEqual(shared_state.STATE_PATH, test_support.LIVE_STATE_PATH)
                self.assertEqual(assumptions.USER_DEFAULTS_PATH, test_support.PROCESS_USER_DEFAULTS_PATH)
                self.assertEqual(shared_state.STATE_PATH, test_support.PROCESS_STATE_PATH)

        self.assertEqual(hashlib.sha256(test_support.LIVE_USER_DEFAULTS_PATH.read_bytes()).hexdigest(), INITIAL_LIVE_HASHES['user_defaults'])
        self.assertEqual(hashlib.sha256(test_support.LIVE_STATE_PATH.read_bytes()).hexdigest(), INITIAL_LIVE_HASHES['current_scenario'])


if __name__ == '__main__':
    unittest.main()
