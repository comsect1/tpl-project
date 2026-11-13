# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""host_build error paths: an ABI mismatch names expected and found values; a failed run keeps a record."""
import json
import os
import types
import unittest
from unittest import mock

import maint_support as support

build = support.package_module("host_build")
registry = support.package_module("registry")

GCC_BIN = os.environ.get("COMSECT1_TEST_GCC_BIN", r"C:\msys64\ucrt64\bin")
GCC = os.path.join(GCC_BIN, "gcc.exe" if os.name == "nt" else "gcc")


class HostBuildErrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("host_build_errors")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    @unittest.skipUnless(os.path.isfile(GCC), "gcc is absent")
    def test_abi_mismatch_names_expected_and_found_values(self):
        root = self.work.folder("abi")
        support.write(root / "contract" / "pvo_x.h",
                      "#include <stdint.h>\ntypedef struct { uint32_t a; uint64_t b; } x_frame;\n#define X_LIMIT 7u\n")
        run_dir = self.work.folder("abi_run")
        (run_dir / "tmp").mkdir()
        cell = types.SimpleNamespace(name="x", root=str(root), contract_headers=["contract/pvo_x.h"],
                                     abi_path=str(root / "x_abi.py"))
        abi = types.SimpleNamespace(struct_layout=lambda: {"x_frame": {"size": 12, "offsets": {"a": 0, "b": 4}}},
                                    CONSTANTS={"X_LIMIT": 9})
        with mock.patch.object(build, "GCC_PATH", GCC), mock.patch.object(build, "GCC_BIN_DIR", GCC_BIN):
            env = build._child_env(str(run_dir / "tmp"))
            error = build._abi_mismatch(cell, abi, [GCC, "-std=c17", "-fsyntax-only", "-I", str(root)], env,
                                        str(run_dir), build.BuildError("probe failed"))
        differs = error.details["abi_mismatch"]["differs"]
        self.assertEqual(differs["sizeof(x_frame)"], {"expected": 12, "found": 16})
        self.assertEqual(differs["offsetof(x_frame, b)"], {"expected": 4, "found": 8})
        self.assertEqual(differs["X_LIMIT"], {"expected": 9, "found": 7})
        self.assertNotIn("offsetof(x_frame, a)", differs)
        for text in ("sizeof(x_frame) expected 12 found 16", str(root / "x_abi.py"), "Next step"):
            self.assertIn(text, str(error))

    def test_failure_record_keeps_the_reason_and_details(self):
        run_dir = self.work.folder("failed_run")
        cell = types.SimpleNamespace(name="engine")
        cache = build.object_cache.ObjectCache(None)
        error = build.BuildError("the fnd-sdk-standard checkout is not found", {"root_variable": "X_ROOT"})
        with mock.patch.object(registry, "_settings", return_value=None), \
                mock.patch.object(registry, "standard_record", side_effect=registry.RegistryError("unreadable pin")):
            build._write_failure_record(str(run_dir), "run-1", cell, error, cache,
                                        cache.statistics(str(support.REPO_ROOT)), {"template": None})
        record = json.loads((run_dir / "build_record.json").read_text(encoding="utf-8"))
        self.assertEqual((record["result"], record["cell"], record["details"]), ("failed", "engine", {"root_variable": "X_ROOT"}))
        self.assertIn("not found", record["error"])
        self.assertEqual(record["fnd_sdk_standard"], {"note": "unreadable pin"})


if __name__ == "__main__":
    unittest.main()
