# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""gate: the derived lane configuration and the exact scratch cleanup (Sunset guards, moved)."""
import json
import os
import pathlib
import unittest
from unittest import mock

import maint_support as support

gate = support.package_module("gate")
native_gate = support.package_module("native_gate")
registry = support.package_module("registry")
process = support.process


class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("gate")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def test_derived_config_carries_the_registry_and_passive_roots(self):
        root = self.work.folder("derived")
        support.c_cell(root, "horizon", "wsb")
        support.c_cell(root, "engine", "wsb")
        support.write(root / "10_pkg" / "binding" / "include" / "x.h", "/* passive */\n")
        cells = {"horizon": support.c_entry("horizon"),
                 "engine": support.c_entry("engine", depends_on=["horizon"], definitions={"X": "1"},
                                           passive_include_roots=["10_pkg/binding/include"],
                                           hosted_include_roots=["C:/sdk/include"])}
        settings = support.product(root, "wsb", cells, cores={})
        support.write(root / "20_ops" / "tools" / "product_hooks.py",
                      "def gate_publications(cell, config):\n"
                      "    return [{'canonical_root': 'C:/sdk', 'inventory_member': 'sdk.swidtag',\n"
                      "             'declaration_member': 'binding/native_publication.json'}]\n")
        cell = registry.load_registry(settings)["cells"]["engine"]
        with mock.patch.object(gate, "BASE_CONFIG", str(support.FIXTURES / "native_gate_config.json")), \
                mock.patch.object(gate, "REPO_ROOT", str(root)):
            config = gate.derived_config(cell, "windows")
        build = config["build"]
        self.assertEqual(build["external_include_roots"], ["10_pkg/c/horizon/membrane", "10_pkg/binding/include"])
        self.assertEqual(build["system_include_roots"], ["C:/sdk/include"])
        self.assertEqual(build["definitions"], {"X": "1"})
        self.assertEqual(config["publications"][0]["inventory_member"], "sdk.swidtag")

    def test_declared_in_repository_foundation_is_a_system_root_the_lane_admits(self):
        root = self.work.folder("declared")
        support.c_cell(root, "engine", "dcb", manifest={
            "cell": "engine", "composition_context": {"hosted_vocabularies": [{"id": "fnd-sdk-standard"}]}})
        settings = support.product(root, "dcb", {"engine": support.c_entry("engine")})
        declared = "10_pkg/deps/fnd-platform-architecture"
        support.write(root / declared / "10_pkg" / "include" / "comsect1_provider.h", "/* standard */\n")
        support.write(root / ".gitmodules", '[submodule "fnd"]\n\tpath = %s\n' % declared)
        support.write(root / "10_pkg" / "components" / "fnd_sdk_standard.json", json.dumps(
            {"commit": "f" * 40, "root_variable": "DCB_FND_SDK_STANDARD_ROOT", "deps_path": declared,
             "public_header": "10_pkg/include/comsect1_provider.h"}) + "\n")
        cell = registry.load_registry(settings)["cells"]["engine"]
        include = os.path.join(os.path.realpath(str(root)), *declared.split("/"), "10_pkg", "include")
        with mock.patch.dict(os.environ, {"DCB_FND_SDK_STANDARD_ROOT": ""}), \
                mock.patch.object(gate, "BASE_CONFIG", str(support.FIXTURES / "native_gate_config.json")), \
                mock.patch.object(gate, "REPO_ROOT", str(root)):
            config = gate.derived_config(cell, "windows")
        self.assertEqual(config["build"]["system_include_roots"], [include.replace("\\", "/")])
        with mock.patch.object(native_gate, "REPO_ROOT", pathlib.Path(str(root))):
            self.assertEqual(native_gate.system_directories(config),
                             [pathlib.Path(include).resolve()])
            config["build"]["system_include_roots"] = [str(root / "10_pkg" / "c" / "engine").replace("\\", "/")]
            with self.assertRaisesRegex(native_gate.ToolError, "inside this repository outside 10_pkg/deps/"):
                native_gate.system_directories(config)
            config["build"]["system_include_roots"] = [str(root / "10_pkg" / "deps").replace("\\", "/")]
            with self.assertRaisesRegex(native_gate.ToolError, "inside this repository outside 10_pkg/deps/"):
                native_gate.system_directories(config)

    def test_derived_config_without_hooks_keeps_the_base_members(self):
        root = self.work.folder("plain")
        support.c_cell(root, "engine", "dcb")
        settings = support.product(root, "dcb", {"engine": support.c_entry("engine")})
        cell = registry.load_registry(settings)["cells"]["engine"]
        with mock.patch.object(gate, "BASE_CONFIG", str(support.FIXTURES / "native_gate_config.json")):
            config = gate.derived_config(cell, "windows")
        base = json.loads((support.FIXTURES / "native_gate_config.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(config), sorted(base))

    def test_only_the_named_gate_run_can_be_quarantined(self):
        other = os.path.join(str(support.REPO_ROOT), "90_temps", "scratch", "gate", "other")
        with mock.patch.object(process, "is_link", return_value=False), mock.patch.object(process.os, "rename") as rename:
            with self.assertRaises(RuntimeError):
                gate.quarantine(other, "named")
        rename.assert_not_called()

    def test_linked_scratch_ancestor_is_refused(self):
        linked = os.path.join(str(support.REPO_ROOT), "90_temps", "scratch")
        candidate = os.path.join(linked, "gate", "run")
        with mock.patch.object(process, "is_link",
                               side_effect=lambda path: os.path.normcase(path) == os.path.normcase(linked)):
            with self.assertRaises(RuntimeError):
                gate.quarantine(candidate, "run")


if __name__ == "__main__":
    unittest.main()
