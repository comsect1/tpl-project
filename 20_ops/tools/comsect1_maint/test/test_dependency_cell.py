# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""A dependency Cell: a Cell of a declared dependency submodule, built into the consumer's libraries and
archives from where it is. Registry acceptance and every named refusal, the recorded checkout, the
deliverables, the Gate set, and one host and one cross build with the real compilers (skipped without)."""
import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import maint_support as support

registry = support.package_module("registry")
build = support.package_module("host_build")
cross = support.package_module("cross_build")
gate = support.package_module("gate")
native_gate = support.package_module("native_gate")

DEPS_PATH = "10_pkg/deps/lib-pacer"
CELL_ROOT = DEPS_PATH + "/10_pkg/c/pacer"
MEMBRANE = CELL_ROOT + "/membrane"
REPOSITORY = "https://example.invalid/lib-pacer.git"
CORES = {"cortex-m4": ["-mcpu=cortex-m4", "-mthumb"]}
MANIFEST = {"kind": "code", "languages": ["c"], "namespace": "fixture",
            "public_identity_surfaces": [{"identity": "pacer", "path": "membrane/xpt_pacer.h"}]}
SOURCES = ["core/gate/smo_pacer_gate.c", "membrane/xpt_pacer.c"]
# The value the dependent computes through the dependency Cell: PACER_STEP added to twice the argument.
ARGUMENT = 3
EXPECTED_TOTAL = 13
# How far a tracked file's time stamp is moved from the one the index recorded, in seconds.
INDEX_AGE_SECONDS = 10

GCC_BIN = os.environ.get("COMSECT1_TEST_GCC_BIN", r"C:\msys64\ucrt64\bin")
GCC = os.path.join(GCC_BIN, "gcc.exe" if os.name == "nt" else "gcc")
ARM_BIN = os.environ.get("COMSECT1_TEST_ARM_BIN", support.fixture_config("dcb")["cross_build"]["toolchain_bin_dir"])
ARM_GCC = os.path.join(ARM_BIN, "arm-none-eabi-gcc.exe" if os.name == "nt" else "arm-none-eabi-gcc")
COMMIT_COMMAND = ["-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "fixture"]


def dependency(root, manifest=None):
    """Write the fixture dependency checkout below `<root>/10_pkg/deps/lib-pacer`: its licence files, a
    registry of its own that no consumer tool may read (it is no JSON), and the Cell `pacer`."""
    checkout = pathlib.Path(root).joinpath(*DEPS_PATH.split("/"))
    cell = pathlib.Path(root).joinpath(*CELL_ROOT.split("/"))
    support.write(checkout / "LICENSE.md", "fixture licence\n")
    support.write(checkout / "NOTICE.md", "fixture notice\n")
    support.write(checkout / "20_ops" / "tools" / "cells.json", "the registry of the dependency is not read\n")
    support.write(cell / "comsect1.json", json.dumps(MANIFEST if manifest is None else manifest) + "\n")
    support.write(cell / "contract" / "pvo_pacer_link.h",
                  "#ifndef PVO_PACER_LINK_H\n#define PVO_PACER_LINK_H\n#define PACER_STEP 7\n#endif\n")
    support.write(cell / "membrane" / "xpt_pacer.h",
                  "#ifndef XPT_PACER_H\n#define XPT_PACER_H\n#include \"../contract/pvo_pacer_link.h\"\n"
                  "int pacer_gate_sum(int first, int second);\n#endif\n")
    support.write(cell / "membrane" / "xpt_pacer.c",
                  "#include \"xpt_pacer.h\"\n#include \"core/gate/smo_pacer_gate.h\"\n"
                  "int pacer_gate_sum(int first, int second)\n{\n    return smo_pacer_gate_sum(first, second);\n}\n")
    support.write(cell / "core" / "gate" / "smo_pacer_gate.h",
                  "#ifndef SMO_PACER_GATE_H\n#define SMO_PACER_GATE_H\nint smo_pacer_gate_sum(int first, int second);\n"
                  "#endif\n")
    support.write(cell / "core" / "gate" / "smo_pacer_gate.c",
                  "#include \"core/gate/smo_pacer_gate.h\"\n"
                  "int smo_pacer_gate_sum(int first, int second)\n{\n    return first + second;\n}\n")
    return checkout


def dependent(root):
    """Write the consumer's own Cell `app`, which calls the dependency Cell, and its ABI mirror."""
    cell = pathlib.Path(root) / "10_pkg" / "c" / "app"
    support.write(cell / "comsect1.json", json.dumps({"cell": "app"}) + "\n")
    support.write(cell / "contract" / "pvo_dcb_app.h",
                  "#ifndef PVO_DCB_APP_H\n#define PVO_DCB_APP_H\n#define DCB_APP_LIMIT 4\n#endif\n")
    support.write(cell / "membrane" / "xpt_dcb_app.h",
                  "#ifndef XPT_DCB_APP_H\n#define XPT_DCB_APP_H\nint dcb_app_total(int value);\n#endif\n")
    support.write(cell / "core" / "smo_dcb_app.c",
                  "#include \"membrane/xpt_dcb_app.h\"\n#include \"xpt_pacer.h\"\n"
                  "int dcb_app_total(int value)\n{\n    return pacer_gate_sum(value, value) + PACER_STEP;\n}\n")
    support.write(pathlib.Path(root) / "10_pkg" / "test" / "c" / "app" / "dcb_app_abi.py",
                  "CONSTANTS = {\"DCB_APP_LIMIT\": 4}\n\n\ndef struct_layout():\n    return {}\n")


def entries(**changes):
    """The registry entries of the fixture consumer; `changes` replace keys of the dependency entry
    (None as a value removes the key)."""
    pacer = support.c_entry("pacer", ("windows", "arm"), dependency=DEPS_PATH, include_roots=["membrane", "."])
    for key, value in changes.items():
        if value is None:
            pacer.pop(key, None)
        else:
            pacer[key] = value
    return {"pacer": pacer, "app": support.c_entry("app", ("windows", "arm"), depends_on=["pacer"])}


def consumer(root, cells=None, gitmodules=DEPS_PATH, manifest=None, checkout=True):
    """A fixture consumer at `root` with its dependency checkout (`checkout`), declared in .gitmodules at
    `gitmodules` (None: no .gitmodules); returns its Settings."""
    if checkout:
        dependency(root, manifest)
    dependent(root)
    if gitmodules is not None:
        support.write(pathlib.Path(root) / ".gitmodules",
                      '[submodule "%s"]\n\tpath = %s\n\turl = %s\n' % (gitmodules, gitmodules, REPOSITORY))
    return support.product(root, "dcb", entries() if cells is None else cells, cores=CORES)


def tree(folder):
    """{relative path: SHA-256 or None for a folder} of everything below `folder`."""
    found = {}
    for directory, names, files in os.walk(str(folder)):
        for name in names:
            found[os.path.relpath(os.path.join(directory, name), str(folder)).replace("\\", "/")] = None
        for name in files:
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, str(folder)).replace("\\", "/")] = hashlib.sha256(handle.read()).hexdigest()
    return found


class DependencyCellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("dependency_cell")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def cells(self, name, **arguments):
        root = self.work.folder(name)
        return root, registry.load_registry(consumer(root, **arguments))["cells"]

    def refusal(self, name, **arguments):
        root = self.work.folder(name)
        settings = consumer(root, **arguments)
        with self.assertRaises(registry.RegistryError) as caught:
            registry.load_registry(settings)
        return str(caught.exception)

    def test_registry_reads_the_dependency_cell_where_it_is(self):
        root, cells = self.cells("accepted")
        pacer, app = cells["pacer"], cells["app"]
        self.assertEqual(pacer.dependency, DEPS_PATH)
        self.assertEqual(pacer.root, os.path.join(str(root), *CELL_ROOT.split("/")))
        self.assertEqual((pacer.stem, pacer.archive_name, pacer.function_prefix, pacer.public_header),
                         ("pacer", "libpacer.a", "pacer_", "membrane/xpt_pacer.h"))
        self.assertEqual((pacer.sources, pacer.contract_headers), (SOURCES, ["contract/pvo_pacer_link.h"]))
        self.assertEqual((pacer.tests_root, pacer.abi_path, pacer.test_pattern, pacer.library_name,
                          pacer.definition_file, pacer.library_environment), (None,) * 6)
        self.assertEqual(pacer.standard, support.c_standard.C_STANDARD)
        self.assertIsNone(app.dependency)
        self.assertEqual((app.linked_cells(), app.library_plan()), ([pacer], (app, [pacer])))
        self.assertEqual(app.dependency_membranes(), [os.path.join(str(root), *MEMBRANE.split("/"))])
        self.assertEqual(app.external_include_roots(), [MEMBRANE])
        self.assertEqual(registry.authored_cells(cells), [app])
        self.assertEqual(build.header_functions(pacer), ["pacer_gate_sum"])

    def test_dependency_cell_may_declare_its_function_prefix_and_be_exported(self):
        cells = entries(function_prefix="pacer_gate_")
        cells["app"]["exports"] = ["app", "pacer"]
        _root, loaded = self.cells("exported", cells=cells)
        self.assertEqual(loaded["pacer"].function_prefix, "pacer_gate_")
        self.assertEqual(loaded["app"].exported_cells(), [loaded["app"], loaded["pacer"]])

    def test_malformed_dependency_values_are_refused_by_name(self):
        for index, (value, reason) in enumerate(((7, "it must be a non-empty string"),
                                                 ("", "it must be a non-empty string"),
                                                 ("10_pkg/c/pacer", "it must name a folder below 10_pkg/deps/"),
                                                 ("../lib-pacer", "it must name a folder below 10_pkg/deps/"),
                                                 ("C:/lib-pacer", "it must be repository-relative"),
                                                 ("10_pkg/deps/lib-pacer/", "is not a plain folder name"))):
            with self.subTest(value=value):
                text = self.refusal("value_%d" % index, cells=entries(dependency=value))
                for part in ("Cell pacer: dependency", "is invalid", reason, "Next step"):
                    self.assertIn(part, text)

    def test_undeclared_submodule_is_refused(self):
        for name, gitmodules in (("undeclared_none", None), ("undeclared_other", "10_pkg/deps/other")):
            with self.subTest(gitmodules=gitmodules):
                text = self.refusal(name, gitmodules=gitmodules)
                for part in ("Cell pacer: dependency %s" % DEPS_PATH, "lists no submodule at that path",
                             "the submodule is the declaration", "Next step", "git submodule add"):
                    self.assertIn(part, text)

    def test_uninitialized_submodule_is_refused(self):
        for state in ("missing", "empty", "a file, not a folder"):
            root = self.work.folder("uninitialized_%s" % state.split(",")[0].replace(" ", "_"))
            settings = consumer(root, checkout=False)
            checkout = root.joinpath(*DEPS_PATH.split("/"))
            if state == "empty":
                checkout.mkdir(parents=True)
            if state == "a file, not a folder":
                support.write(checkout, "not a folder\n")
            with self.subTest(state=state), self.assertRaises(registry.RegistryError) as caught:
                registry.load_registry(settings)
            for part in ("Cell pacer: the dependency checkout", "is %s" % state, DEPS_PATH,
                         "Next step: run git submodule update --init %s" % DEPS_PATH):
                self.assertIn(part, str(caught.exception))
            self.assertEqual(caught.exception.details["deps_path"], DEPS_PATH)

    def test_dependency_cell_without_its_manifest_or_publication_is_refused(self):
        root = self.work.folder("no_manifest")
        settings = consumer(root)
        os.remove(str(root.joinpath(*CELL_ROOT.split("/")) / "comsect1.json"))
        with self.assertRaisesRegex(registry.RegistryError, "has no comsect1.json"):
            registry.load_registry(settings)
        cases = (({"kind": "code", "languages": ["rust"], "public_identity_surfaces": MANIFEST["public_identity_surfaces"]},
                  "does not declare the language c"),
                 ({"kind": "code", "public_identity_surfaces": MANIFEST["public_identity_surfaces"]},
                  "does not declare the language c"),
                 ({"languages": ["c"]}, "exactly one public identity surface"),
                 ({"languages": ["c"], "public_identity_surfaces": []}, "exactly one public identity surface"),
                 ({"languages": ["c"], "public_identity_surfaces": MANIFEST["public_identity_surfaces"] * 2},
                  "exactly one public identity surface"),
                 ({"languages": ["c"], "public_identity_surfaces": [{"identity": "Pacer", "path": "membrane/xpt_pacer.h"}]},
                  "exactly one public identity surface"),
                 ({"languages": ["c"], "public_identity_surfaces": [{"identity": "pacer", "path": "core/smo_pacer.h"}]},
                  "exactly one public identity surface"),
                 ({"languages": ["c"], "public_identity_surfaces": [{"identity": "pacer", "path": "membrane/xpt_absent.h"}]},
                  "public header membrane/xpt_absent.h is missing"))
        for index, (manifest, reason) in enumerate(cases):
            with self.subTest(manifest=manifest):
                text = self.refusal("manifest_%d" % index, manifest=manifest)
                for part in ("Cell pacer", reason, "comsect1.json"):
                    self.assertIn(part, text)

    def test_keys_of_the_owner_are_refused_in_a_dependency_entry(self):
        section = {"environment": "DCB_KERNEL", "commit": "0" * 40, "include_roots": ["include"],
                   "core_include_roots": {"cortex-m4": []}, "gate_core": "cortex-m4", "config_roots": []}
        changes = ({"test_fixture": "fixture"}, {"link_fixture": False}, {"fixture_units": []}, {"library_from": "app"},
                   {"link_with": ["app"]}, {"link_libraries": ["ws2_32"]}, {"exports": ["pacer"]},
                   {"hosted_include_roots": ["C:/sdk/include"]}, {"passive_include_roots": ["10_pkg/c"]},
                   {"external_headers": section})
        for index, change in enumerate(changes):
            key, = change
            with self.subTest(key=key):
                text = self.refusal("key_%d" % index, cells=entries(**change))
                for part in ("Cell pacer: a dependency Cell declares no %s" % key, "its own repository"):
                    self.assertIn(part, text)

    def test_dependency_cell_roles_are_refused_by_name(self):
        text = self.refusal("standard", cells=entries(standard="c23"))
        self.assertIn("Cell pacer: standard 'c23' is refused", text)
        cells = entries()
        cells["pacer"]["depends_on"] = ["app"]
        cells["app"]["depends_on"] = []
        self.assertIn("Cell pacer: a dependency Cell depends only on dependency Cells, not on app",
                      self.refusal("depends", cells=cells))
        cells = entries()
        cells["app"]["library_from"] = "pacer"
        self.assertIn("Cell app: library_from names the dependency Cell pacer, which has no library",
                      self.refusal("library_from", cells=cells))
        root = self.work.folder("provider")
        settings = consumer(root, cells={"pacer": dict(entries()["pacer"], path="provider/pacer",
                                                       budget={"cortex-m4": {"text": 1, "data": 0, "bss": 0}})})
        with self.assertRaisesRegex(registry.RegistryError, "Cell pacer: a dependency Cell is no Provider"):
            registry.load_registry(settings)

    def checkout_product(self, name):
        """A fixture consumer below the system temporary directory (a Git object path below a deep
        checkout's 90_temps can exceed the Windows path limit) whose dependency is its own Git work tree."""
        base = tempfile.TemporaryDirectory(prefix="dependency_cell_")
        self.addCleanup(base.cleanup)
        root = pathlib.Path(base.name) / name
        root.mkdir()
        settings = consumer(root)
        checkout = root.joinpath(*DEPS_PATH.split("/"))
        for command in (["init", "-q"], ["add", "-A"], COMMIT_COMMAND):
            registry._git(str(checkout), command)
        return root, settings, checkout

    def write_pin(self, root, commit):
        support.write(root / "10_pkg" / "components" / "lib_pacer.json", json.dumps(
            {"repository": REPOSITORY, "commit": commit, "root_variable": "DCB_LIB_PACER_ROOT",
             "deps_path": DEPS_PATH, "public_header": "10_pkg/c/pacer/membrane/xpt_pacer.h"}) + "\n")

    def test_checkout_state_and_pin_are_recorded_never_refused(self):
        root, settings, checkout = self.checkout_product("recorded")
        head = registry._git(str(checkout), ["rev-parse", "HEAD"])
        with mock.patch.dict(registry._OBSERVED_CHECKOUTS, clear=True):
            record = registry.load_registry(settings)["cells"]["pacer"].dependency_record()
        self.assertEqual(record, {"cell": "pacer", "identity": "pacer", "deps_path": DEPS_PATH, "source": "deps_path",
                                  "root": os.path.realpath(str(checkout)),
                                  "cell_root": os.path.join(str(root), *CELL_ROOT.split("/")),
                                  "pin": None, "pinned_commit": None, "pin_note": None, "head": head, "dirty": False,
                                  "note": None, "matches_pin": False})
        self.write_pin(root, head)
        with mock.patch.dict(registry._OBSERVED_CHECKOUTS, clear=True):
            record = registry.load_registry(settings)["cells"]["pacer"].dependency_record()
        self.assertEqual((record["pin"], record["pinned_commit"], record["matches_pin"]),
                         (str(root / "10_pkg" / "components" / "lib_pacer.json"), head, True))
        self.write_pin(root, "f" * 40)
        support.write(checkout / "untracked.txt", "a change of the workstation\n")
        with mock.patch.dict(registry._OBSERVED_CHECKOUTS, clear=True):
            record = registry.load_registry(settings)["cells"]["pacer"].dependency_record()
        self.assertEqual((record["pinned_commit"], record["head"], record["dirty"], record["matches_pin"]),
                         ("f" * 40, head, True, False))
        self.assertIsNone(registry.load_registry(settings)["cells"]["app"].dependency_record())

    def test_deliverables_carry_the_names_of_the_dependency(self):
        root, cells = self.cells("deliverables")
        checkout = root.joinpath(*DEPS_PATH.split("/"))
        self.assertIsNone(cells["app"].dependency_deliverables())
        self.assertEqual(cells["pacer"].dependency_deliverables(), {
            "identity": "pacer", "deps_path": DEPS_PATH, "checkout": str(checkout),
            "cell_root": os.path.join(str(root), *CELL_ROOT.split("/")),
            "public_headers": ["membrane/xpt_pacer.h", "contract/pvo_pacer_link.h"],
            "archive_name": "libpacer.a", "archives": {"cortex-m4": "pacer/cortex-m4/libpacer.a"},
            "license_files": [str(checkout / "LICENSE.md"), str(checkout / "NOTICE.md")]})
        os.remove(str(checkout / "NOTICE.md"))
        self.assertEqual(cells["pacer"].dependency_deliverables()["license_files"], [str(checkout / "LICENSE.md")])
        os.remove(str(checkout / "LICENSE.md"))
        with self.assertRaises(registry.RegistryError) as caught:
            cells["pacer"].dependency_deliverables()
        for part in ("Cell pacer", "holds no LICENSE.md", str(checkout), "Next step"):
            self.assertIn(part, str(caught.exception))

    def host_patches(self, settings):
        return (mock.patch.object(build, "_SETTINGS", settings), mock.patch.object(build, "_HOST", settings.host_build),
                mock.patch.object(build, "GCC_BIN_DIR", GCC_BIN), mock.patch.object(build, "GCC_PATH", GCC),
                mock.patch.object(build, "NM_PATH", os.path.join(GCC_BIN, os.path.basename(GCC).replace("gcc", "nm"))),
                mock.patch.object(build, "OBJDUMP_PATH",
                                  os.path.join(GCC_BIN, os.path.basename(GCC).replace("gcc", "objdump"))),
                mock.patch.object(build, "IMAGE_TARGET", settings.host_build["image_target"]),
                mock.patch.object(build, "get_cell", lambda name: registry.get_cell(name, settings)),
                mock.patch.object(build.maint_config, "maintainer_tools", return_value={"template": None}),
                mock.patch.dict(registry._STANDARD_CHECKOUTS, clear=True))

    def test_consumer_tools_neither_build_nor_judge_the_dependency_cell_alone(self):
        root = self.work.folder("refused_alone")
        settings = consumer(root)
        loaded = registry.load_registry(settings)
        patches = self.host_patches(settings)
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with self.assertRaises(build.BuildError) as caught:
            build.build_library("pacer", "run-1", object_cache_mode="run", jobs=1)
        for part in ("Cell pacer is a Cell of the dependency %s" % DEPS_PATH, "no library of its own",
                     "its own repository"):
            self.assertIn(part, str(caught.exception))
        self.assertFalse((root / "90_temps").exists())
        with mock.patch.object(registry, "load_registry", return_value=loaded):
            selected = build._requested_cells(argparse.Namespace(all=True, cell=[]))
        self.assertEqual([cell.name for cell in selected], ["app"])
        errors = []
        with mock.patch.object(gate, "BASE_CONFIG", str(support.FIXTURES / "native_gate_config.json")), \
                mock.patch.object(registry, "get_cell", lambda name: loaded["cells"][name]), \
                mock.patch.object(gate.sys.stderr, "write", errors.append), \
                mock.patch.object(gate.subprocess, "run") as run:
            self.assertEqual(gate.main(["--cell", "pacer", "--target", "windows"]), 2)
        run.assert_not_called()
        for part in ("Cell pacer is a Cell of the dependency %s" % DEPS_PATH, "its Gate verdict is its owner's"):
            self.assertIn(part, "".join(errors))

    def test_gate_of_a_dependent_reads_the_dependency_membrane_as_an_external_root(self):
        root, cells = self.cells("gate")
        app_root = pathlib.Path(cells["app"].root).resolve()
        with mock.patch.object(gate, "BASE_CONFIG", str(support.FIXTURES / "native_gate_config.json")), \
                mock.patch.object(gate, "REPO_ROOT", str(root)):
            config = gate.derived_config(cells["app"], "windows")
        self.assertEqual(config["build"]["external_include_roots"], [MEMBRANE])
        self.assertEqual(config["build"]["system_include_roots"], [])
        membrane = root.joinpath(*MEMBRANE.split("/")).resolve()
        with mock.patch.object(native_gate, "REPO_ROOT", pathlib.Path(str(root))):
            self.assertEqual(native_gate.include_directories(config, app_root), [app_root, membrane])
        # With publications, the header of the dependency is an input whose owner is the dependency's
        # own Cell control, read where it is: the Cell is submitted as a source Cell, never governed.
        sources, publications = native_gate.input_owner_selection(
            dict(config, publications=[]), {"header": str(membrane / "xpt_pacer.h")}, app_root)
        self.assertEqual((sources, publications), ([root.joinpath(*CELL_ROOT.split("/")).resolve()], []))

    @unittest.skipUnless(os.path.isfile(GCC), "gcc is absent")
    def test_host_build_links_the_dependency_cell_into_its_dependent(self):
        for name, exports, exported in (("host_build", None, ["dcb_app_total"]),
                                        ("host_exports", ["app", "pacer"], ["dcb_app_total", "pacer_gate_sum"])):
            with self.subTest(exports=exports):
                root = self.work.folder(name)
                cells = entries()
                if exports is not None:
                    cells["app"]["exports"] = exports
                settings = consumer(root, cells=cells)
                checkout = root.joinpath(*DEPS_PATH.split("/"))
                before = tree(checkout)
                patches = self.host_patches(settings)
                for patch in patches:
                    patch.start()
                try:
                    library = build.build_library("app", "run-1", object_cache_mode="run", jobs=1)
                finally:
                    for patch in reversed(patches):
                        patch.stop()
                run_dir = root / "90_temps" / "build" / "app" / "run-1"
                self.assertEqual(pathlib.Path(library), run_dir / "dcb_app.dll")
                record = json.loads((run_dir / "build_record.json").read_text(encoding="utf-8"))
                self.assertEqual((record["result"], record["linked"], record["exported"], sorted(record["probed"])),
                                 ("passed", ["pacer"], exported, ["app"]))
                dependency_record, = record["dependency_cells"]
                self.assertEqual((dependency_record["cell"], dependency_record["identity"], dependency_record["deps_path"],
                                  dependency_record["cell_root"]),
                                 ("pacer", "pacer", DEPS_PATH, os.path.join(str(root), *CELL_ROOT.split("/"))))
                self.assertEqual([probe["observed_stdc_version"] for probe in record["c_standard"]["probes"]],
                                 [support.c_standard.STDC_VERSION])
                # The library is called in a child process, so no handle of this process keeps the run.
                done = subprocess.run([sys.executable, "-I", "-B", "-c",
                                       "import ctypes, sys; print(ctypes.CDLL(sys.argv[1]).dcb_app_total(int(sys.argv[2])))",
                                       library, str(ARGUMENT)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                      creationflags=support.process.window_flags())
                self.assertEqual((done.returncode, done.stdout.strip()), (0, str(EXPECTED_TOTAL)), done.stderr)
                self.assertEqual(tree(checkout), before)
                self.assertFalse(any(name.endswith(".def") for name in before))

    def cross_patches(self, settings):
        """One context that points cross_build at the Arm toolchain and the fixture's common flags."""
        def tool(name):
            return os.path.join(ARM_BIN, os.path.basename(ARM_GCC).replace("gcc", name))

        stack = contextlib.ExitStack()
        for name, value in (("TOOLCHAIN_BIN_DIR", ARM_BIN), ("GCC_PATH", ARM_GCC), ("AR_PATH", tool("ar")),
                            ("SIZE_PATH", tool("size")), ("NM_PATH", tool("nm")),
                            ("COMMON_FLAGS", list(settings.cross_build["common_flags"]))):
            stack.enter_context(mock.patch.object(cross, name, value))
        stack.enter_context(mock.patch.object(registry, "standard_record", return_value=None))
        return stack

    def test_cross_build_selects_the_dependency_cell_under_its_own_archive_name(self):
        _root, cells = self.cells("cross_names")
        settings = cells["pacer"].settings
        _cores, selected = cross.selected_cells(None, settings)
        self.assertEqual([(cell.name, cell.archive_name) for cell in selected],
                         [("app", "libdcb_app.a"), ("pacer", "libpacer.a")])

    @unittest.skipUnless(os.path.isfile(ARM_GCC), "the Arm toolchain is absent")
    def test_cross_build_archives_the_dependency_cell_per_core(self):
        root = self.work.folder("cross_build")
        settings = consumer(root)
        checkout = root.joinpath(*DEPS_PATH.split("/"))
        before = tree(checkout)
        cores, selected = cross.selected_cells(None, settings)
        run_dir = root / "90_temps" / "build" / "cross" / "run-1"
        (run_dir / "tmp").mkdir(parents=True)

        with self.cross_patches(settings):
            _run_dir, record = cross._build_run("run-1", True, {"template": None}, cores, selected, str(run_dir),
                                                str(run_dir / "tmp"), "run", 1)
        results = {result["cell"]: result for result in record["results"]}
        self.assertEqual((results["pacer"]["archive"], results["app"]["archive"]),
                         ("pacer/cortex-m4/libpacer.a", "app/cortex-m4/libdcb_app.a"))
        deliverables = selected[1].dependency_deliverables()
        self.assertTrue((run_dir / deliverables["archives"]["cortex-m4"]).is_file())
        self.assertEqual((results["pacer"]["sources"], results["pacer"]["external"]), (SOURCES, []))
        self.assertEqual(results["app"]["depends_on"], ["pacer"])
        # Every result whose compiled units hold a dependency Cell keeps its checkout, and so does the run.
        for name in ("pacer", "app"):
            state, = results[name]["dependency_cells"]
            self.assertEqual((state["cell"], state["deps_path"]), ("pacer", DEPS_PATH))
        self.assertEqual(record["dependency_cells"], results["pacer"]["dependency_cells"])
        self.assertEqual(tree(checkout), before)

    @unittest.skipUnless(os.path.isfile(ARM_GCC), "the Arm toolchain is absent")
    def test_cross_build_of_a_dependent_alone_records_the_dependency_checkout(self):
        root = self.work.folder("cross_dependent")
        settings = consumer(root)
        cores, selected = cross.selected_cells("app", settings)
        self.assertEqual([cell.name for cell in selected], ["app"])
        run_dir = root / "90_temps" / "build" / "cross" / "run-1"
        (run_dir / "tmp").mkdir(parents=True)
        with self.cross_patches(settings):
            _run_dir, record = cross._build_run("run-1", True, {"template": None}, cores, selected, str(run_dir),
                                                str(run_dir / "tmp"), "run", 1)
        result, = record["results"]
        self.assertEqual((result["cell"], result["archive"]), ("app", "app/cortex-m4/libdcb_app.a"))
        state, = result["dependency_cells"]
        self.assertEqual((state["cell"], state["identity"], state["deps_path"], state["pinned_commit"],
                          state["matches_pin"]), ("pacer", "pacer", DEPS_PATH, None, False))
        for key in ("root", "cell_root", "head", "dirty", "note", "pin", "pin_note"):
            self.assertIn(key, state)
        self.assertEqual(record["dependency_cells"], [state])

    def junction(self, link, target):
        """A directory junction (or symbolic link) `link` to `target`, removed again after the test."""
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            try:
                import _winapi
                _winapi.CreateJunction(str(target), str(link))
            except (ImportError, AttributeError, OSError):
                self.skipTest("this platform cannot create a symbolic link or a junction")
        self.addCleanup(lambda: os.path.lexists(str(link)) and (os.rmdir if os.name == "nt" else os.unlink)(str(link)))

    def link_refusal(self, settings, link):
        with self.assertRaises(registry.RegistryError) as caught:
            registry.load_registry(settings)
        for part in ("Cell pacer", "is a link or reparse point", str(link), DEPS_PATH, "Next step"):
            self.assertIn(part, str(caught.exception))

    def test_linked_cell_root_inside_the_checkout_is_refused(self):
        outside = self.work.folder("link_root_outside")
        dependency(outside)
        root = self.work.folder("link_root")
        settings = consumer(root, checkout=False)
        checkout = root.joinpath(*DEPS_PATH.split("/"))
        support.write(checkout / "LICENSE.md", "fixture licence\n")
        (checkout / "10_pkg" / "c").mkdir(parents=True)
        link = root.joinpath(*CELL_ROOT.split("/"))
        self.junction(link, outside.joinpath(*CELL_ROOT.split("/")))
        self.link_refusal(settings, link)

    def test_linked_folder_below_the_cell_root_is_refused(self):
        outside = self.work.folder("link_folder_outside")
        dependency(outside)
        root = self.work.folder("link_folder")
        settings = consumer(root)
        cell = root.joinpath(*CELL_ROOT.split("/"))
        for name in ("smo_pacer_gate.c", "smo_pacer_gate.h"):
            os.remove(str(cell / "core" / "gate" / name))
        os.rmdir(str(cell / "core" / "gate"))
        link = cell / "core" / "gate"
        self.junction(link, outside.joinpath(*CELL_ROOT.split("/")) / "core" / "gate")
        self.link_refusal(settings, link)

    def test_linked_input_file_of_the_cell_is_refused(self):
        outside = self.work.folder("link_file_outside")
        dependency(outside)
        for relative in ("membrane/xpt_pacer.h", "core/gate/smo_pacer_gate.c", "comsect1.json"):
            with self.subTest(file=relative):
                root = self.work.folder("link_file_%s" % relative.split("/")[-1].split(".")[0])
                settings = consumer(root)
                link = root.joinpath(*CELL_ROOT.split("/")).joinpath(*relative.split("/"))
                os.remove(str(link))
                try:
                    os.symlink(str(outside.joinpath(*CELL_ROOT.split("/")).joinpath(*relative.split("/"))), str(link))
                except (OSError, NotImplementedError) as error:
                    self.skipTest("this account cannot create a file symbolic link without privilege: %s" % error)
                self.addCleanup(lambda path=link: os.path.lexists(str(path)) and os.unlink(str(path)))
                self.link_refusal(settings, link)

    def test_null_dependency_is_refused_and_never_read_as_a_local_cell(self):
        root = self.work.folder("null")
        cells = entries()
        cells["pacer"]["dependency"] = None
        settings = consumer(root, cells=cells)
        support.c_cell(root, "pacer", "dcb")
        with self.assertRaises(registry.RegistryError) as caught:
            registry.load_registry(settings)
        for part in ("Cell pacer: dependency None is invalid", "it must be a non-empty string", "Next step"):
            self.assertIn(part, str(caught.exception))

    def test_submodule_declarations_follow_the_git_configuration_rules(self):
        root = self.work.folder("gitmodules")
        path = "10_pkg/deps/lib-pacer"
        accepted = (('[submodule "a"]\n\tpath = %s\n' % path, {path}),
                    ('[submodule "a"]\n\tpath = "%s"\n' % path, {path}),
                    ('[submodule "a"]\n\tPATH = %s # a comment\n' % path, {path}),
                    ('[submodule "a"]\n\tpath = %s ; a comment\n' % path, {path}),
                    ('[SubModule "a"]\n  path="%s"   \n' % path, {path}),
                    ('[submodule "a"] path = %s\n\turl = x\n' % path, {path}),
                    ('# head\n[submodule "a"]\n\turl = "x # y"\n\tpath = "10_pkg/deps/a # b"\n', {"10_pkg/deps/a # b"}),
                    ('[submodule "a"]\n\tpath = "10_pkg/deps/a\\\\b\\"c"\n', {'10_pkg/deps/a\\b"c'}),
                    ('[submodule "a"]\n\tpath = 10_pkg/deps/a  b\n', {"10_pkg/deps/a  b"}),
                    ('[submodule "a"]\n\tpath = 10_pkg/deps/x\n[submodule "a"]\n\tpath = %s\n' % path, {path}),
                    ('[submodule "a"]\n\tpath = 10_pkg/deps/x\n[submodule "b"]\n\tpath = %s\n' % path,
                     {"10_pkg/deps/x", path}),
                    ('[other]\n\tpath = %s\n' % path, set()),
                    ('[submodule]\n\tpath = %s\n' % path, set()),
                    ('[submodule "a"]\n\turl = x\n[core]\n\tpath = %s\n' % path, set()),
                    ('[submodule "a"]\n\tpathology = %s\n' % path, set()))
        for index, (text, expected) in enumerate(accepted):
            with self.subTest(index=index, text=text):
                support.write(root / ".gitmodules", text)
                self.assertEqual(registry._submodule_paths(str(root)), expected)
        refused = (('[submodule "a"]\n\tpath = %s\n[submodule "b"]\n\tpath = %s\n' % (path, path),
                    ("declared by the submodules", '"a"', '"b"', path)),
                   ('[submodule "a"]\n\tpath = "%s\n' % path, ("unterminated quoted value", "line 2")),
                   ('[submodule "a"]\n\tpath = %s\\\n' % path, ("continued over the line end", "line 2")),
                   ('[submodule "a"\n\tpath = %s\n' % path, ("malformed section header", "line 1")))
        for index, (text, parts) in enumerate(refused):
            with self.subTest(index=index, text=text):
                support.write(root / ".gitmodules", text)
                with self.assertRaises(registry.RegistryError) as caught:
                    registry._submodule_paths(str(root))
                for part in parts + (str(root / ".gitmodules"), "Next step"):
                    self.assertIn(part, str(caught.exception))

    def test_dependency_declared_outside_a_submodule_section_or_quoted(self):
        root = self.work.folder("gitmodules_section")
        settings = consumer(root)
        support.write(root / ".gitmodules", '[other]\n\tpath = %s\n' % DEPS_PATH)
        with self.assertRaisesRegex(registry.RegistryError, "lists no submodule at that path"):
            registry.load_registry(settings)
        support.write(root / ".gitmodules", '[submodule "lib-pacer"]\n\tpath = "%s" # the library\n' % DEPS_PATH)
        self.assertEqual(registry.load_registry(settings)["cells"]["pacer"].dependency, DEPS_PATH)

    def test_state_query_writes_nothing_into_the_checkout(self):
        _root, settings, checkout = self.checkout_product("index")
        index = checkout / ".git" / "index"
        tracked = checkout / "LICENSE.md"
        later = os.stat(str(tracked)).st_mtime + INDEX_AGE_SECONDS
        os.utime(str(tracked), (later, later))
        before = (index.read_bytes(), os.stat(str(index)).st_mtime_ns, tree(checkout))
        with mock.patch.dict(registry._OBSERVED_CHECKOUTS, clear=True):
            record = registry.load_registry(settings)["cells"]["pacer"].dependency_record()
        self.assertEqual((record["dirty"], record["note"]), (False, None))
        self.assertEqual(record["head"], registry._git(str(checkout), ["rev-parse", "HEAD"]))
        self.assertFalse((checkout / ".git" / "index.lock").exists())
        self.assertEqual((index.read_bytes(), os.stat(str(index)).st_mtime_ns, tree(checkout)), before)

    def test_absent_git_is_recorded_as_unknown(self):
        _root, cells = self.cells("no_git")
        with mock.patch.dict(registry._OBSERVED_CHECKOUTS, clear=True), \
                mock.patch.object(registry.subprocess, "run", side_effect=FileNotFoundError("git")):
            record = cells["pacer"].dependency_record()
        self.assertEqual((record["head"], record["dirty"], record["matches_pin"]), (None, None, False))
        self.assertIn("no Git work tree", record["note"])

    def test_unreadable_or_ambiguous_pins_are_a_note(self):
        root, cells = self.cells("pin_note")
        self.write_pin(root, "f" * 40)
        components = os.path.join(str(root), "10_pkg", "components")
        listdir = os.listdir

        def denied(path):
            if os.path.normcase(str(path)) == os.path.normcase(components):
                raise PermissionError("denied")
            return listdir(path)

        with mock.patch.object(registry.os, "listdir", side_effect=denied):
            record = cells["pacer"].dependency_record()
        self.assertEqual((record["pin"], record["pinned_commit"], record["matches_pin"]), (None, None, False))
        for part in ("denied", components):
            self.assertIn(part, record["pin_note"])
        record = cells["pacer"].dependency_record()
        self.assertEqual((record["pinned_commit"], record["pin_note"]), ("f" * 40, None))
        support.write(root / "10_pkg" / "components" / "second.json",
                      json.dumps({"deps_path": DEPS_PATH, "commit": "e" * 40}) + "\n")
        record = cells["pacer"].dependency_record()
        self.assertEqual((record["pin"], record["pinned_commit"]), (None, None))
        for part in ("lib_pacer.json", "second.json", DEPS_PATH):
            self.assertIn(part, record["pin_note"])

    SCANNED = ("int pacer_keep(void);\n"
               "int\npacer_split(void);\n"
               "extern const char *\n    pacer_name(int index,\n               int other);\n"
               "static inline int pacer_id(int x) { return x; }\n"
               "static int pacer_private(void);\n"
               "inline int pacer_inline(int x) { return x; }\n"
               "int pacer_defined(void)\n{\n    return 0;\n}\n"
               "typedef int (*pacer_callback)(int);\n"
               "typedef int pacer_function(int);\n"
               "struct pacer_ops { int (*pacer_member)(void); int pacer_field; };\n"
               "extern pacer_handle (*pacer_hook)(void);\n"
               "extern int pacer_table[PACER_SIZE(3)];\n"
               "int other_open(void);\n"
               "void pacer_last(struct pacer_ops *ops, int (*callback)(int));\n")

    def test_exports_are_the_external_function_declarations_only(self):
        self.assertEqual(build.declared_functions(self.SCANNED, "pacer_"),
                         ["pacer_keep", "pacer_split", "pacer_name", "pacer_last"])
        self.assertEqual(build.declared_functions("#ifdef __cplusplus\nextern \"C\" {\n#endif\nint pacer_open(void);\n"
                                                  "#ifdef __cplusplus\n}\n#endif\n", "pacer_"), ["pacer_open"])
        root, cells = self.cells("scanned")
        support.write(root.joinpath(*MEMBRANE.split("/")) / "xpt_pacer.h", "/* int pacer_hidden(void); */\n" + self.SCANNED)
        self.assertEqual(build.header_functions(cells["pacer"]), ["pacer_keep", "pacer_split", "pacer_name", "pacer_last"])
        marked = ('# 1 "wrapper.c"\n# 1 "%s" 1\nint pacer_keep(void);\n# 1 "other.h" 1\nint pacer_other(void);\n'
                  '# 3 "%s" 2\nint\npacer_split(void);\n# 2 "wrapper.c" 2\nint pacer_wrapper(void);\n')
        header = os.path.join(cells["pacer"].root, "membrane", "xpt_pacer.h")
        for spelling in (header, header.replace("\\", "/"), header.replace("\\", "\\\\")):
            with self.subTest(spelling=spelling):
                self.assertEqual(build.header_text(marked % (spelling, spelling), header),
                                 "int pacer_keep(void);\nint\npacer_split(void);\n")

    @unittest.skipUnless(os.path.isfile(GCC), "gcc is absent")
    def test_host_build_exports_what_the_preprocessed_dependency_header_declares(self):
        root = self.work.folder("host_preprocessed")
        cells = entries()
        cells["app"]["exports"] = ["app", "pacer"]
        settings = consumer(root, cells=cells)
        cell = root.joinpath(*CELL_ROOT.split("/"))
        support.write(cell / "membrane" / "xpt_pacer.h",
                      "#ifndef XPT_PACER_H\n#define XPT_PACER_H\n#include \"../contract/pvo_pacer_link.h\"\n"
                      "#define PACER_OPERATION(name) int pacer_gate_##name(int value)\n"
                      "int pacer_gate_sum(int first, int second);\n"
                      "int\npacer_gate_twice(int value);\n"
                      "PACER_OPERATION(half);\n"
                      "#ifdef PACER_ABSENT_OPTION\nint pacer_gate_absent(int value);\n#endif\n"
                      "static inline int pacer_gate_same(int value) { return value; }\n#endif\n")
        support.write(cell / "membrane" / "xpt_pacer.c",
                      "#include \"xpt_pacer.h\"\n#include \"core/gate/smo_pacer_gate.h\"\n"
                      "int pacer_gate_sum(int first, int second)\n{\n    return smo_pacer_gate_sum(first, second);\n}\n"
                      "int pacer_gate_twice(int value)\n{\n    return smo_pacer_gate_sum(value, pacer_gate_same(value));\n}\n"
                      "int pacer_gate_half(int value)\n{\n    return value / 2;\n}\n")
        before = tree(root.joinpath(*DEPS_PATH.split("/")))
        patches = self.host_patches(settings)
        for patch in patches:
            patch.start()
        try:
            build.build_library("app", "run-1", object_cache_mode="run", jobs=1)
        finally:
            for patch in reversed(patches):
                patch.stop()
        run_dir = root / "90_temps" / "build" / "app" / "run-1"
        record = json.loads((run_dir / "build_record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["exported"], ["dcb_app_total", "pacer_gate_half", "pacer_gate_sum", "pacer_gate_twice"])
        self.assertEqual((run_dir / "dcb_app.def").read_text(encoding="utf-8"),
                         "LIBRARY dcb_app.dll\nEXPORTS\n    dcb_app_total\n    pacer_gate_sum\n    pacer_gate_twice\n"
                         "    pacer_gate_half\n")
        self.assertEqual(tree(root.joinpath(*DEPS_PATH.split("/"))), before)


if __name__ == "__main__":
    unittest.main()
