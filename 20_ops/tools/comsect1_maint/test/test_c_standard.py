# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""c_standard: the one standard token and its verification by `__STDC_VERSION__` in the host build, the
cross build and the native Gate lane (a compiler double; one case with a real gcc, skipped without one)."""
import json
import os
import types
import unittest
from unittest import mock

import maint_support as support

c_standard = support.package_module("c_standard")
build = support.package_module("host_build")
cross = support.package_module("cross_build")
driver = support.package_module("native_gate")
registry = support.package_module("registry")

GCC_BIN = os.environ.get("COMSECT1_TEST_GCC_BIN", r"C:\msys64\ucrt64\bin")
GCC = os.path.join(GCC_BIN, "gcc.exe" if os.name == "nt" else "gcc")
GATE_CONFIG = json.loads((support.FIXTURES / "native_gate_config.json").read_text(encoding="utf-8"))


def unit_cell(name, standard=c_standard.C_STANDARD, definitions=()):
    return types.SimpleNamespace(name=name, standard=standard, definition_flags=lambda: list(definitions))


class Compiler:
    """A compiler double: answers every preprocess command with the value its standard option selects."""
    VALUES = {"-std=c17": "201710L", "/std:c17": "201710L", "-std=c11": "201112L", "-std=gnu17": "201710L",
              "-std=c23": "202311L", "-std=c90": "__STDC_VERSION__"}

    def __init__(self):
        self.commands = []

    def output(self, command):
        self.commands.append(list(command))
        selected = [part for part in command if str(part).startswith(("-std=", "/std:"))]
        return "\n" + self.VALUES[selected[-1]] + "\n"

    def host(self, command, env, cwd, what):
        return types.SimpleNamespace(stdout=self.output(command))

    def cross(self, command, env, what):
        return types.SimpleNamespace(stdout=self.output(command))

    def gate(self, name, command, timeout, cwd, log_path=None):
        return 0, self.output([str(part) for part in command]), ""


class StandardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("c_standard")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def run_dir(self, name):
        path = self.work.folder(name)
        (path / "tmp").mkdir()
        return path

    def test_one_standard_with_its_formal_name_and_value(self):
        self.assertEqual((c_standard.C_STANDARD, c_standard.FORMAL_NAME, c_standard.ALIASES, c_standard.STDC_VERSION,
                          c_standard.MSVC_FLAG),
                         ("c17", "ISO/IEC 9899:2018", ("C17", "C18"), "201710L", "/std:c17"))
        self.assertEqual(c_standard.title(), "ISO/IEC 9899:2018 (C17/C18)")

    def test_observed_value_is_the_expansion(self):
        self.assertEqual(c_standard.observed_version("# 1 \"probe.c\"\n\n201710L\r\n\n"), "201710L")
        self.assertEqual(c_standard.observed_version("__STDC_VERSION__\n"), "__STDC_VERSION__")
        self.assertEqual(c_standard.observed_version(""), "")
        record = c_standard.verification(["gcc", "-std=c17", "-E", "-P", "probe.c"], "201710L\n")
        self.assertEqual(record, {"command": ["gcc", "-std=c17", "-E", "-P", "probe.c"],
                                  "observed_stdc_version": "201710L", "expected_stdc_version": "201710L",
                                  "verified": True})
        self.assertFalse(c_standard.verification(["gcc"], "202311L\n")["verified"])

    def test_registry_refuses_every_other_token_by_the_formal_name(self):
        for standard in ("c23", "c18", "gnu17", "c11", 17, None):
            root = self.work.folder("registry_%s" % standard)
            support.c_cell(root, "engine", "dcb")
            settings = support.product(root, "dcb", {"engine": support.c_entry("engine", standard=standard)})
            with self.subTest(standard=standard), self.assertRaises(registry.RegistryError) as caught:
                registry.load_registry(settings)
            for text in ("Cell engine", repr(standard), "ISO/IEC 9899:2018 (C17/C18)", 'write "c17"'):
                self.assertIn(text, str(caught.exception))
        root = self.work.folder("registry_accepted")
        support.c_cell(root, "engine", "dcb")
        settings = support.product(root, "dcb", {"engine": support.c_entry("engine", standard="c17")})
        self.assertEqual(registry.load_registry(settings)["cells"]["engine"].standard, "c17")

    def test_host_build_verifies_each_distinct_flag_set_once(self):
        run_dir = self.run_dir("host_pass")
        compiler = Compiler()
        cells = [unit_cell("engine"), unit_cell("codec"), unit_cell("serial", definitions=["-DX=1"])]
        with mock.patch.object(build, "GCC_PATH", "gcc"), mock.patch.object(build, "_run", side_effect=compiler.host):
            record = build._verify_standard(cells, {}, str(run_dir), {"cl": "cl.exe"}, {})
        probe = str(run_dir / "tmp" / "c_standard_probe.c")
        self.assertEqual((run_dir / "tmp" / "c_standard_probe.c").read_text(encoding="utf-8"), "__STDC_VERSION__\n")
        self.assertEqual(compiler.commands, [
            ["gcc", "-std=c17", "-Wall", "-Wextra", "-Werror", "-pedantic", "-E", "-P", probe],
            ["cl.exe", "/nologo", "/std:c17", "/TC", "/W4", "/WX", "/EP", probe],
            ["gcc", "-std=c17", "-Wall", "-Wextra", "-Werror", "-pedantic", "-DX=1", "-E", "-P", probe],
            ["cl.exe", "/nologo", "/std:c17", "/TC", "/W4", "/WX", "-DX=1", "/EP", probe]])
        self.assertEqual((record["standard"], record["formal_name"], record["aliases"], record["expected_stdc_version"]),
                         ("c17", "ISO/IEC 9899:2018", ["C17", "C18"], "201710L"))
        self.assertEqual([probe["observed_stdc_version"] for probe in record["probes"]], ["201710L"] * 4)
        self.assertEqual([probe["command"] for probe in record["probes"]], compiler.commands)

    def test_host_build_refuses_another_value(self):
        for standard, observed in (("c11", "201112L"), ("c23", "202311L"), ("c90", "__STDC_VERSION__")):
            run_dir = self.run_dir("host_refused_%s" % standard)
            with self.subTest(standard=standard), mock.patch.object(build, "GCC_PATH", "gcc"), \
                    mock.patch.object(build, "_run", side_effect=Compiler().host), \
                    self.assertRaises(build.BuildError) as caught:
                build._verify_standard([unit_cell("engine", standard)], {}, str(run_dir))
            for text in ("Cell engine is not compiled as ISO/IEC 9899:2018 (C17/C18)", repr(observed),
                         "expected 201710L", "-std=%s" % standard, "Next step"):
                self.assertIn(text, str(caught.exception))
            probe, = caught.exception.details["c_standard"]["probes"]
            self.assertEqual((probe["observed_stdc_version"], probe["verified"]), (observed, False))

    @unittest.skipUnless(os.path.isfile(GCC), "gcc is absent")
    def test_host_build_probe_with_a_real_gcc(self):
        with mock.patch.object(build, "GCC_PATH", GCC), mock.patch.object(build, "GCC_BIN_DIR", GCC_BIN):
            run_dir = self.run_dir("gcc_pass")
            record = build._verify_standard([unit_cell("engine", definitions=["-DX=1"])],
                                            build._child_env(str(run_dir / "tmp")), str(run_dir))
            self.assertEqual([probe["observed_stdc_version"] for probe in record["probes"]], ["201710L"])
            run_dir = self.run_dir("gcc_refused")
            with self.assertRaisesRegex(build.BuildError, "expands to '201112L'"):
                build._verify_standard([unit_cell("engine", "c11")], build._child_env(str(run_dir / "tmp")), str(run_dir))

    def test_cross_build_verifies_the_whole_flag_order(self):
        tmp_dir = self.work.folder("cross_pass")
        compiler = Compiler()
        sets = [("Cell engine on cortex-m4", ["-mthumb", "-std=c17", "-ffreestanding"]),
                ("Cell codec on cortex-m4", ["-mthumb", "-std=c17", "-ffreestanding"]),
                ("Cell engine on cortex-m0", ["-mcpu=cortex-m0", "-std=c17", "-ffreestanding"])]
        with mock.patch.object(cross, "GCC_PATH", "arm-gcc"), mock.patch.object(cross, "run_tool", side_effect=compiler.cross):
            record = cross._verify_standard(sets, {}, str(tmp_dir))
        probe = str(tmp_dir / "c_standard_probe.c")
        self.assertEqual(compiler.commands, [["arm-gcc", "-E", "-P", "-mthumb", "-std=c17", "-ffreestanding", probe],
                                             ["arm-gcc", "-E", "-P", "-mcpu=cortex-m0", "-std=c17", "-ffreestanding", probe]])
        self.assertEqual([item["observed_stdc_version"] for item in record["probes"]], ["201710L", "201710L"])
        # A common flag that selects another standard after the Cell's own is caught by value.
        tmp_dir = self.work.folder("cross_refused")
        with mock.patch.object(cross, "GCC_PATH", "arm-gcc"), \
                mock.patch.object(cross, "run_tool", side_effect=Compiler().cross), \
                self.assertRaises(cross.CrossBuildError) as caught:
            cross._verify_standard([("Cell engine on cortex-m4", ["-mthumb", "-std=c17", "-std=c11"])], {}, str(tmp_dir))
        self.assertIn("Cell engine on cortex-m4 is not compiled as ISO/IEC 9899:2018 (C17/C18)", str(caught.exception))
        self.assertEqual(caught.exception.details["c_standard"]["probes"][0]["observed_stdc_version"], "201112L")

    def test_native_lane_verifies_the_analysis_flags(self):
        path = support.write(self.work.path / "gate.json", json.dumps(GATE_CONFIG, indent=2) + "\n")
        config = driver.load_config(path)
        run_dir = self.work.folder("gate_pass")
        compiler = Compiler()
        with mock.patch.object(driver, "run_process", side_effect=compiler.gate):
            driver.verify_standard(config, "clang", run_dir)
        command, = compiler.commands
        self.assertEqual(command[:1] + command[-4:], ["clang", "-E", "-P", "-nostdinc", str(run_dir / "c_standard_probe.c")])
        self.assertIn("-std=c17", command)
        self.assertEqual(config["_c_standard"]["probes"][0]["observed_stdc_version"], "201710L")
        config["build"]["standard"] = "c11"
        with mock.patch.object(driver, "run_process", side_effect=Compiler().gate), \
                self.assertRaises(driver.ToolError) as caught:
            driver.verify_standard(config, "clang", self.work.folder("gate_refused"))
        self.assertEqual(caught.exception.code, driver.EXIT_PLAN_FAILED)
        self.assertIn("the native lane is not compiled as ISO/IEC 9899:2018 (C17/C18)", caught.exception.message)
        self.assertEqual(config["_c_standard"]["probes"][0]["verified"], False)
        with mock.patch.object(driver, "run_process", return_value=(1, "", "clang: error: no such file")), \
                self.assertRaisesRegex(driver.ToolError, "the C standard probe failed: clang: error"):
            driver.verify_standard(config, "clang", self.work.folder("gate_failed"))


if __name__ == "__main__":
    unittest.main()
