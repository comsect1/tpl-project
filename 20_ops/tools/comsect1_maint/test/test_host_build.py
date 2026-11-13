# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""host_build pure helpers: header functions, the export table, response files and export aliases."""
import json
import os
import unittest
from unittest import mock

import maint_support as support

build = support.package_module("host_build")
registry = support.package_module("registry")

OBJDUMP = """
The Export Tables (interpreted .edata section contents)

[Ordinal/Name Pointer] Table
	[   0] +base[   1] 0000 dcb_engine_open
	[   1] +base[   2] 0001 dcb_engine_close
"""


class HostBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("host_build")
        root = cls.work.folder("product")
        support.c_cell(root, "engine", "wsb")
        support.c_cell(root, "provider/cstat", "wsb")
        support.write(root / "10_pkg" / "c" / "engine" / "membrane" / "xpt_wsb_engine.h",
                      "/* int wsb_engine_hidden(void); */\n// int wsb_engine_line(void);\n"
                      "int wsb_engine_open(void);\nconst char *wsb_engine_name(int index);\n")
        cells = {"engine": support.c_entry("engine"),
                 "cstat": support.c_entry("provider/cstat", sdk_dependencies=["wsb_shell"])}
        cls.settings = support.product(root, "sunset", cells, cores={})
        cls.cells = registry.load_registry(cls.settings)["cells"]

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def test_header_functions_skip_comments(self):
        self.assertEqual(build.header_functions(self.cells["engine"]), ["wsb_engine_open", "wsb_engine_name"])
        self.assertEqual(build._strip_comments("a /* b */ c // d\ne"), "a  c \ne")

    def test_header_functions_follow_a_declared_function_prefix(self):
        root = self.work.folder("declared_prefix")
        support.c_cell(root, "engine", "wsb")
        support.write(root / "10_pkg" / "c" / "engine" / "membrane" / "xpt_wsb_engine.h",
                      "int horizon_gate_open(void);\nconst char *horizon_sender_name(int index);\n"
                      "int wsb_engine_open(void);\n")
        entry = support.c_entry("engine", function_prefix="horizon_")
        cell = registry.load_registry(support.product(root, "wsb", {"engine": entry}, cores={}))["cells"]["engine"]
        self.assertEqual(build.header_functions(cell), ["horizon_gate_open", "horizon_sender_name"])
        self.assertEqual(build.definition_text(cell, [], build.header_functions(cell), []),
                         "LIBRARY wsb_engine.dll\nEXPORTS\n    horizon_gate_open\n    horizon_sender_name\n")

    def test_export_table(self):
        self.assertEqual(build._export_table(OBJDUMP, "x.dll"), ["dcb_engine_open", "dcb_engine_close"])
        with self.assertRaisesRegex(build.BuildError, "no export table"):
            build._export_table("nothing", "x.dll")

    def test_export_aliases_and_definition_file(self):
        provider = self.cells["cstat"]
        aliases = build.export_aliases(provider, self.settings)
        self.assertEqual(aliases, [("wsb_provider_negotiate", "wsb_provider_cstat_negotiate"),
                                   ("wsb_provider_open", "wsb_provider_cstat_open")])
        self.assertEqual(build.export_aliases(self.cells["engine"], self.settings), [])
        text = build.definition_text(provider, [provider], [], aliases)
        self.assertEqual(text, "LIBRARY wsb_provider_cstat.dll\nEXPORTS\n    wsb_provider_cstat_registration DATA\n"
                               "    wsb_provider_negotiate = wsb_provider_cstat_negotiate\n"
                               "    wsb_provider_open = wsb_provider_cstat_open\n")
        bad = json.loads(json.dumps(self.settings.raw))
        bad["host_build"]["provider_export_aliases"] = ["other_negotiate"]
        settings = support.config.Settings(bad, self.settings.repo_root, self.settings.path)
        with self.assertRaisesRegex(build.BuildError, "must start with"):
            build.export_aliases(provider, settings)

    def test_definition_file_without_aliases(self):
        engine = self.cells["engine"]
        self.assertEqual(build.definition_text(engine, [], ["wsb_engine_open"], []),
                         "LIBRARY wsb_engine.dll\nEXPORTS\n    wsb_engine_open\n")

    def test_cl_response_file_and_command(self):
        path = self.work.path / "cl.rsp"
        self.assertEqual(build._response_file(str(path), ["/Ia b", 'x"y']), "@" + str(path))
        self.assertEqual(path.read_text(encoding="utf-8"), '"/Ia b"\n"x\\"y"\n')
        command = build._msvc_unit_command({"cl": "cl.exe"}, self.cells["engine"], ["-I", "a", "-I", "b c"], ["/c"])
        self.assertEqual(command, ["cl.exe", "/nologo", "/std:c17", "/TC", "/W4", "/WX", "/Ia", "/Ib c", "/c"])
        with self.assertRaisesRegex(build.BuildError, "unexpected include"):
            build._msvc_unit_command({"cl": "cl.exe"}, self.cells["engine"], ["-isystem", "a"], [])

    @unittest.skipUnless(os.name == "nt", "Windows command line limit")
    def test_long_gcc_command_goes_through_a_response_file(self):
        gcc = os.path.join(str(self.work.path), "gcc.exe")
        run_dir = self.work.folder("response")
        (run_dir / "tmp").mkdir()
        with mock.patch.object(build, "GCC_PATH", gcc):
            short = [gcc, "--version"]
            self.assertEqual(build._gcc_response_command(short, str(run_dir)), short)
            long = [gcc] + ['-DTEXT_%04d="a \\"b\\" c"' % index for index in range(1200)]
            command = build._gcc_response_command(long, str(run_dir))
            self.assertEqual(command[0], gcc)
            record_path, = (run_dir / "tmp").glob("gcc_*.json")
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual((record["argv"], record["command"]), (long, command))
            with self.assertRaises(FileExistsError):
                build._gcc_response_command(long, str(run_dir))

    def test_sanitizer_selection(self):
        self.assertIsNone(build.parse_sanitize(None))
        self.assertEqual(build.parse_sanitize("undefined"), "undefined")
        with self.assertRaisesRegex(build.BuildError, "separate variants"):
            build.parse_sanitize("address,undefined")
        with self.assertRaisesRegex(build.BuildError, "unknown sanitizer"):
            build.parse_sanitize("thread")


if __name__ == "__main__":
    unittest.main()
