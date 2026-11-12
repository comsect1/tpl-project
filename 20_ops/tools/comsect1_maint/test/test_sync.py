# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""sync: manifest write and check round trip; modified, missing and extra common files are reported; --from
merges the common instruction files three ways and never writes kind/, local/ or the template origin."""
import json
import pathlib
import shutil
import tempfile
import unittest

import maint_support as support

sync = support.package_module("sync")

# Repository-owned files below .instructions/ that synchronization never writes and --check never reports.
OWNED = ("kind/README.md", "local/README.md", "templates/RECORD.md", "template-origin.json")


def common_tree(root):
    """A fixture template at `root`: a copy of the package, placeholder shims and three common instructions;
    returns its tools folder."""
    tools = root / "20_ops" / "tools"
    shutil.copytree(support.PACKAGE_ROOT, tools / "comsect1_maint", ignore=shutil.ignore_patterns("__pycache__"))
    # Placeholders: the checkout may be a Rust product, whose build.py and gate.py are local and
    # which has no C shims; these tests rewrite the manifest of the copy and need only the paths.
    for file_name in sync.SHIMS + sync.ROOT_FILES:
        support.write(tools / file_name, "# common file %s\n" % file_name)
    for file_name in ("README.md", "policy.md", "rules.md"):
        support.write(root / ".instructions" / file_name, "# %s\n" % file_name)
    return tools


class SyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("sync")

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def copy_tools(self, name):
        return common_tree(self.work.folder(name))

    def test_this_checkout_matches_its_manifest(self):
        language = sync.product_language(str(support.TOOLS_ROOT))
        self.assertEqual(sync.check(str(support.TOOLS_ROOT), language), [])
        self.assertEqual(sync.check_instructions(str(support.TOOLS_ROOT)), [])

    def test_round_trip_and_findings(self):
        tools = self.copy_tools("round_trip")
        sync.write_manifest(str(tools))
        self.assertEqual(sync.check(str(tools)), [])
        support.write(tools / "comsect1_maint" / "registry.py", "# local edit\n")
        (tools / "gate.py").unlink()
        support.write(tools / "comsect1_maint" / "extra.py", "# stray\n")
        self.assertEqual(sync.check(str(tools)), [("EXTRA", "comsect1_maint/extra.py"),
                                                  ("MODIFIED", "comsect1_maint/registry.py"), ("MISSING", "gate.py")])

    def test_instruction_findings_cover_only_the_common_files(self):
        tools = self.copy_tools("instructions")
        instructions = tools.parent.parent / ".instructions"
        sync.write_manifest(str(tools))
        self.assertEqual(sync.check_instructions(str(tools)), [])
        for name in OWNED:
            support.write(instructions / name, "owned by the repository\n")
        self.assertEqual(sync.check_instructions(str(tools)), [])
        support.write(instructions / "policy.md", "# adapted here\n")
        (instructions / "rules.md").unlink()
        support.write(instructions / "stray.md", "# not common\n")
        self.assertEqual(sync.check_instructions(str(tools)), [
            ("MODIFIED", ".instructions/policy.md"), ("MISSING", ".instructions/rules.md"),
            ("EXTRA", ".instructions/stray.md")])
        self.assertEqual(sync.check(str(tools)), [])

    def test_a_rust_product_needs_only_the_registry_shim(self):
        tools = self.copy_tools("rust")
        sync.write_manifest(str(tools))
        for name in ("build.py", "gate.py", "native_gate.py", "cross_build.py"):
            (tools / name).unlink()
        support.write(tools / "maint_config.json", json.dumps({"language": "rust"}))
        self.assertEqual(sync.check(str(tools), sync.product_language(str(tools))), [])
        self.assertEqual(len(sync.check(str(tools), "c")), 4)

    def test_manifest_shape_is_checked(self):
        tools = self.copy_tools("shape")
        support.write(tools / "comsect1_maint" / "sync_manifest.json", json.dumps({"schema": "other", "files": {}}))
        with self.assertRaisesRegex(sync.SyncError, "not a"):
            sync.check(str(tools))
        sync.write_manifest(str(tools))
        manifest = json.loads((tools / "comsect1_maint" / "sync_manifest.json").read_text(encoding="utf-8"))
        manifest["instructions"]["../outside.md"] = "0" * 64
        support.write(tools / "comsect1_maint" / "sync_manifest.json", json.dumps(manifest))
        with self.assertRaisesRegex(sync.SyncError, "not a"):
            sync.check_instructions(str(tools))


class FromTest(unittest.TestCase):
    """--from between Git checkouts below the system temporary directory (a Git object path below a deep
    checkout's 90_temps can exceed the Windows path limit)."""

    COMMIT_COMMAND = ["-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "fixture"]

    def setUp(self):
        base = tempfile.TemporaryDirectory(prefix="sync_")
        self.addCleanup(base.cleanup)
        self.base = pathlib.Path(base.name)

    def commit(self, root):
        """Record the manifest of the template at `root`, commit it and return the commit."""
        sync.write_manifest(str(root / "20_ops" / "tools"))
        if not (root / ".git").exists():
            sync._git(str(root), ["init", "-q"])
        sync._git(str(root), ["-c", "core.autocrlf=false", "add", "-A"])
        sync._git(str(root), self.COMMIT_COMMAND)
        return sync._git(str(root), ["rev-parse", "HEAD"])

    def template(self, name):
        root = self.base / name
        common_tree(root)
        return root

    def product(self, name, baseline):
        """A product with only owned instruction files; `baseline` is its recorded template commit."""
        root = self.base / name
        (root / "20_ops" / "tools").mkdir(parents=True)
        for owned in OWNED[:-1]:
            support.write(root / ".instructions" / owned, "owned %s\n" % owned)
        origin = {"schema": "comsect1.template-origin.v1", "template": "fixture", "baseline_commit": baseline}
        support.write(root / ".instructions" / "template-origin.json", json.dumps(origin) + "\n")
        return root

    def owned_bytes(self, root):
        return {name: (root / ".instructions" / name).read_bytes() for name in OWNED}

    def test_copy_then_three_way_merge_leaves_owned_files_untouched(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", first)
        owned = self.owned_bytes(product)
        commit, outcomes = sync.copy_from(str(template), str(product))
        self.assertEqual(commit, first)
        self.assertEqual(outcomes, [(sync.ADDED, ".instructions/README.md"), (sync.ADDED, ".instructions/policy.md"),
                                    (sync.ADDED, ".instructions/rules.md")])
        self.assertEqual(sync.check_instructions(str(product / "20_ops" / "tools")), [])
        # The template changes README.md and policy.md and adds new.md; the product adapts policy.md and
        # rules.md while its baseline stays at the first commit.
        support.write(template / ".instructions" / "README.md", "# README.md, second\n")
        support.write(template / ".instructions" / "policy.md", "# policy.md, second\n")
        support.write(template / ".instructions" / "new.md", "# new.md\n")
        second = self.commit(template)
        support.write(product / ".instructions" / "policy.md", "# policy.md, adapted\n")
        support.write(product / ".instructions" / "rules.md", "# rules.md, adapted\n")
        commit, outcomes = sync.copy_from(str(template), str(product))
        self.assertEqual(commit, second)
        self.assertEqual(outcomes, [(sync.UPDATED, ".instructions/README.md"), (sync.ADDED, ".instructions/new.md"),
                                    (sync.CONFLICT, ".instructions/policy.md"), (sync.KEPT, ".instructions/rules.md")])
        self.assertEqual((product / ".instructions" / "README.md").read_text(encoding="utf-8"), "# README.md, second\n")
        self.assertEqual((product / ".instructions" / "policy.md").read_text(encoding="utf-8"), "# policy.md, adapted\n")
        self.assertEqual((product / ".instructions" / "rules.md").read_text(encoding="utf-8"), "# rules.md, adapted\n")
        self.assertEqual(self.owned_bytes(product), owned)
        self.assertEqual(sync.check_instructions(str(product / "20_ops" / "tools")),
                         [("MODIFIED", ".instructions/policy.md"), ("MODIFIED", ".instructions/rules.md")])

    def test_an_unknown_baseline_keeps_every_differing_file_as_a_conflict(self):
        template = self.template("template")
        self.commit(template)
        product = self.product("product", "f" * 40)
        support.write(product / ".instructions" / "policy.md", "# policy.md, older\n")
        outcomes = sync.copy_from(str(template), str(product))[1]
        self.assertIn((sync.CONFLICT, ".instructions/policy.md"), outcomes)
        self.assertEqual((product / ".instructions" / "policy.md").read_text(encoding="utf-8"), "# policy.md, older\n")

    def test_a_derived_template_passes_its_adaptations_and_extra_files_to_its_members(self):
        upstream = self.template("upstream")
        first = self.commit(upstream)
        derived = self.product("derived", first)
        sync.copy_from(str(upstream), str(derived))
        support.write(derived / ".instructions" / "policy.md", "# policy.md, derived\n")
        support.write(derived / ".instructions" / "derived-only.md", "# derived-only.md\n")
        derived_commit = self.commit(derived)
        self.assertEqual(sync.check_instructions(str(derived / "20_ops" / "tools")), [])
        member = self.product("member", derived_commit)
        outcomes = sync.copy_from(str(derived), str(member))[1]
        self.assertIn((sync.ADDED, ".instructions/derived-only.md"), outcomes)
        self.assertEqual((member / ".instructions" / "policy.md").read_text(encoding="utf-8"), "# policy.md, derived\n")
        self.assertEqual(sync.check_instructions(str(member / "20_ops" / "tools")), [])
        # The upstream changes only README.md: the derived template's adaptation is kept, not overwritten.
        support.write(upstream / ".instructions" / "README.md", "# README.md, upstream second\n")
        self.commit(upstream)
        outcomes = sync.copy_from(str(upstream), str(derived))[1]
        self.assertEqual(outcomes, [(sync.UPDATED, ".instructions/README.md"), (sync.KEPT, ".instructions/policy.md")])
        self.assertEqual((derived / ".instructions" / "policy.md").read_text(encoding="utf-8"), "# policy.md, derived\n")
        self.assertTrue((derived / ".instructions" / "derived-only.md").is_file())
        self.assertEqual(sync.check_instructions(str(derived / "20_ops" / "tools")),
                         [("EXTRA", ".instructions/derived-only.md"), ("MODIFIED", ".instructions/policy.md")])

    def test_an_instruction_target_that_is_not_a_file_is_refused(self):
        template = self.template("template")
        self.commit(template)
        product = self.product("product", "f" * 40)
        (product / ".instructions" / "README.md").mkdir()
        with self.assertRaisesRegex(sync.SyncError, "not a file"):
            sync.copy_from(str(template), str(product))


if __name__ == "__main__":
    unittest.main()
