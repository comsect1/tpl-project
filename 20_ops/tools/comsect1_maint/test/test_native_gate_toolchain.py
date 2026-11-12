# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Reference toolchain pin, its one location rule and the admission of the pinned bundle; fixtures removed through 99_trash."""
from contextlib import ExitStack
import hashlib
import json
import os
import unittest
from unittest import mock

import maint_support as support

tool = support.package_module("native_gate")
reference = support.package_module("reference_tools")
BUNDLE = "llvm_resource_test"


def pin_record(provenance_sha256, bundle=BUNDLE):
    return {"schema": reference.PIN_SCHEMA,
            "native_c": {"toolchain": "llvm", "version": "0.0.1", "bundle": bundle, "release_tag": "test",
                         "archive_name": "test.tar.xz", "archive_sha256": "a" * 64,
                         "provenance": "provenance.json", "provenance_sha256": provenance_sha256}}


class SynchronizedPinTests(unittest.TestCase):
    def test_template_pin_is_valid_and_names_one_bundle(self):
        entry = reference.load_pin(reference.PIN_FILE)["native_c"]
        self.assertEqual(entry["bundle"], "llvm_resource_" + entry["version"])
        self.assertEqual(entry["release_tag"], "llvmorg-" + entry["version"])


@unittest.skipUnless(os.name == "nt", "toolchain admission locks inputs with Windows handles")
class NativeToolchainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("native_toolchain")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def setUp(self):
        self.base = self.work.folder(self._testMethodName)
        self.tools = self.base / "layout" / "00_reference" / "tools"
        self.repo = self.base / "layout" / "10_family" / "prd" / "20_category" / "repository"
        self.repo.mkdir(parents=True)
        self.root = self.tools / BUNDLE
        values = {"bin/clang.exe": b"fixture compiler", "bin/libclang.dll": b"fixture runtime",
                  "lib/clang/19/include/stdint.h": b"fixture uint", "lib/clang/19/include/stddef.h": b"fixture size"}
        self.runtime = hashlib.sha256(values["bin/libclang.dll"]).hexdigest()
        rows = []
        for relative, data in values.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            rows.append({"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        self.record = {"kind": "native_c_toolchain_inputs",
                       "source": {"release_url": "https://example.invalid/release", "release_tag": "test",
                                  "archive_url": "https://example.invalid/archive", "archive_name": "test.tar.xz",
                                  "archive_size": 1, "archive_sha256": "a" * 64},
                       "compiler": "bin/clang.exe", "semantic_runtime": "bin/libclang.dll",
                       "resource_directory": "lib/clang/19", "files": rows}
        self.environ = {}
        self.pin()

    def pin(self, bundle=BUNDLE):
        data = json.dumps(self.record).encode()
        (self.root / "provenance.json").write_bytes(data)
        self.pin_path = self.base / "reference_toolchain.json"
        self.pin_path.write_text(json.dumps(pin_record(hashlib.sha256(data).hexdigest(), bundle)), encoding="utf-8")

    def admit(self, runtime=None):
        config = {}
        with ExitStack() as locks:
            with mock.patch.object(tool, "REPO_ROOT", self.repo):
                result = tool.find_clang(config, runtime or self.runtime, locks, self.environ, self.pin_path)
        self.native = config["_native_toolchain"]
        return result

    def test_layout_ancestor_is_found_at_any_depth_without_a_variable(self):
        compiler, resource = self.admit()
        self.assertEqual(compiler, self.root / "bin/clang.exe")
        self.assertEqual(resource, self.root / "lib/clang/19/include")
        self.assertEqual(self.native["member_count"], 4)
        self.assertEqual(self.native["members_differ"], [])
        self.assertEqual(self.native["reference_tools_root"], str(self.tools))
        self.assertTrue(self.native["root_source"].startswith("layout ancestor"))
        self.assertEqual(self.native["pin"]["bundle"], BUNDLE)

    def test_variable_selects_the_root_and_a_bad_value_never_falls_back(self):
        moved = self.base / "elsewhere"
        self.tools.rename(moved)
        self.root = moved / BUNDLE
        self.environ = {reference.ROOT_VARIABLE: str(moved)}
        self.admit()
        self.assertEqual(self.native["root_source"], "variable " + reference.ROOT_VARIABLE)
        moved.rename(self.tools)
        for value in ("relative/path", str(self.base / "absent")):
            self.environ = {reference.ROOT_VARIABLE: value}
            with self.subTest(value=value), self.assertRaises(tool.ToolError) as caught:
                self.admit()
            self.assertEqual(caught.exception.code, tool.EXIT_CLANG_UNAVAILABLE)
            self.assertIn("Next step", caught.exception.message)

    def test_missing_bundle_fails_closed_naming_the_pin_and_never_discovers(self):
        self.pin(bundle="llvm_resource_absent")
        with self.assertRaises(tool.ToolError) as caught:
            self.admit()
        self.assertEqual(caught.exception.code, tool.EXIT_CLANG_UNAVAILABLE)
        for text in ("llvm_resource_absent", "test.tar.xz", "Next step"):
            self.assertIn(text, caught.exception.message)

    def test_no_layout_ancestor_fails_closed_with_the_searched_paths(self):
        # The checkout of this test itself may lie below a real reference root; an anchor name no ancestor
        # holds stands for a checkout outside the canonical layout.
        with mock.patch.object(reference, "LAYOUT_ANCHOR", ("00_reference_absent_" + self.work.run_id, "tools")):
            with self.assertRaises(tool.ToolError) as caught:
                self.admit()
        self.assertIn("no reference tools root", caught.exception.message)
        self.assertIn(reference.ROOT_VARIABLE, caught.exception.message)

    def test_invalid_pin_is_refused(self):
        record = pin_record("A" * 64)
        self.pin_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(tool.ToolError, "64 lowercase hex"):
            self.admit()
        record = pin_record("a" * 64, bundle="../outside")
        self.pin_path.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(tool.ToolError, "one plain name"):
            self.admit()

    def test_changed_manifest_digest_is_recorded_not_refused(self):
        with (self.root / "provenance.json").open("ab") as stream: stream.write(b" ")
        self.admit()
        self.assertNotEqual(self.native["manifest_sha256"], self.native["manifest_sha256_recorded"])

    def test_other_semantic_runtime_is_recorded_not_refused(self):
        self.admit("0" * 64)
        self.assertEqual(self.native["semantic_runtime_sha256"], "0" * 64)
        self.assertEqual(self.native["semantic_runtime_sha256_listed"], self.runtime)

    def test_changed_resource_same_size_is_recorded_not_refused(self):
        (self.root / "lib/clang/19/include/stdint.h").write_bytes(b"fixture sint")
        self.admit()
        self.assertEqual(self.native["members_differ"], ["lib/clang/19/include/stdint.h"])

    def test_unlisted_header_is_refused(self):
        (self.root / "lib/clang/19/include/extra.h").write_bytes(b"unapproved")
        with self.assertRaisesRegex(tool.ToolError, "complete inventory"): self.admit()

    def test_traversal_and_case_alias_are_refused(self):
        self.record["files"][0]["path"] = "../outside"
        self.pin()
        with self.assertRaisesRegex(tool.ToolError, "unsafe"): self.admit()
        self.record["files"][0]["path"] = "BIN/libclang.dll"
        self.pin()
        with self.assertRaisesRegex(tool.ToolError, "duplicate"): self.admit()

    def test_admitted_bytes_cannot_be_rewritten_while_in_use(self):
        path = self.root / "lib/clang/19/include/stdint.h"
        with ExitStack() as locks:
            with mock.patch.object(tool, "REPO_ROOT", self.repo):
                tool.find_clang({}, self.runtime, locks, self.environ, self.pin_path)
            with self.assertRaises(PermissionError): path.write_bytes(b"changed")
        path.write_bytes(b"released")


if __name__ == "__main__": unittest.main()
