# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""native_gate.load_config: no toolchain member, one standard only, replaced members refused by name, publications optional."""
import json
import unittest

import maint_support as support

driver = support.package_module("native_gate")
BASE = json.loads((support.FIXTURES / "native_gate_config.json").read_text(encoding="utf-8"))
MANIFEST = {"manifest": "%LOCALAPPDATA%/toolchain/provenance.json", "manifest_sha256": "a" * 64}
DISCOVERY = {"resource_dir": None, "required_major": "19", "clang_candidates": ["clang"]}


class LaneConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("native_gate_config")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def load(self, value, name):
        path = support.write(self.work.path / (name + ".json"), json.dumps(value, indent=2) + "\n")
        return driver.load_config(path)

    def test_configuration_names_no_toolchain(self):
        config = self.load(BASE, "base")
        self.assertNotIn("clang_resource", config)
        self.assertNotIn("publications", config)
        value = dict(BASE, publications=[
            {"canonical_root": "%WSB_SDK_ROOT%", "inventory_member": "wsb-sdk.swidtag",
             "declaration_member": "binding/native_publication.json"}])
        self.assertEqual(len(self.load(value, "publications")["publications"]), 1)

    def test_replaced_toolchain_selection_is_refused_by_name(self):
        for name, clang in (("manifest", MANIFEST), ("discovery", DISCOVERY), ("empty", {})):
            with self.subTest(shape=name), self.assertRaises(driver.ToolError) as caught:
                self.load(dict(BASE, clang_resource=clang), name)
            self.assertEqual(caught.exception.code, driver.EXIT_CONFIG)
            self.assertIn("reference_toolchain.json", caught.exception.message)
            self.assertIn("delete clang_resource", caught.exception.message)

    def test_publication_member_is_refused(self):
        with self.assertRaisesRegex(driver.ToolError, "unaliased"):
            self.load(dict(BASE, publications=[{"canonical_root": "C:/sdk", "inventory_member": "../x",
                                                "declaration_member": "d.json"}]), "publication")

    def test_tool_hash_difference_is_recorded_not_refused(self):
        binary = support.write(self.work.path / "tool_binary.exe", "fixture tool\n")
        config = self.load(BASE, "tool_hash")
        paths = {key: binary for key in driver.TOOL_KEYS}
        actual, differs = driver.tool_hashes(config, paths)
        self.assertEqual(set(actual), set(driver.TOOL_KEYS))
        self.assertEqual(differs, [key for key in driver.TOOL_KEYS if config["expected_sha256"][key] != actual[key]])
        self.assertTrue(differs)

    def test_missing_gate_set_names_the_path_and_the_next_step(self):
        root = self.work.path / "gate_runtime"
        config = self.load(dict(BASE, gate_set=dict(BASE["gate_set"], runtime_root=str(root))), "gate_set")
        with self.assertRaises(driver.ToolError) as caught:
            driver.resolve_gate_set(config)
        self.assertIn(str(root / "current.json"), caught.exception.message)
        self.assertIn("Next step", caught.exception.message)
        support.write(root / "current.json", json.dumps({"set_path": "sets/a", "release_set_id": "set-a"}))
        (root / "sets" / "a" / "payload" / "bin").mkdir(parents=True)
        with self.assertRaises(driver.ToolError) as caught:
            driver.resolve_gate_set(config)
        self.assertEqual(caught.exception.code, driver.EXIT_TOOL_SET_UNAVAILABLE)
        for text in ("comsect1-gate.exe", "release set set-a", "Next step"):
            self.assertIn(text, caught.exception.message)

    def test_analysis_standard_is_the_one_build_standard_without_downgrade(self):
        config = self.load(BASE, "c17")
        self.assertIn("-std=c17", driver.compile_flags(config, []))
        for standard in ("c99", "c11", "c18", "gnu17", "c23", "c2y"):
            with self.subTest(standard=standard), self.assertRaises(driver.ToolError) as caught:
                self.load(dict(BASE, build=dict(BASE["build"], standard=standard)), standard)
            for text in ("no downgrade", "ISO/IEC 9899:2018 (C17/C18)", 'write "c17"'):
                self.assertIn(text, caught.exception.message)
        for name, value in (("c17_only", ["c99", "c11", "c17"]), ("with_c23", ["c99", "c11", "c17", "c23"])):
            with self.subTest(frontend=name), self.assertRaises(driver.ToolError) as caught:
                self.load(dict(BASE, gate_set=dict(BASE["gate_set"], frontend_standards=value)), name)
            self.assertIn("delete gate_set.frontend_standards", caught.exception.message)
            self.assertIn("ISO/IEC 9899:2018", caught.exception.message)

    def test_unknown_member_is_refused(self):
        with self.assertRaisesRegex(driver.ToolError, "exactly the members"):
            self.load(dict(BASE, extra=True), "extra")


if __name__ == "__main__":
    unittest.main()
