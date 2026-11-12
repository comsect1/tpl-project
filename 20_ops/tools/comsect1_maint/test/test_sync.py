# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""sync: manifest write and check round trip; modified, missing and extra common files are reported; --from
merges the common instruction files three ways, declines what a repository removed, and never writes
kind/, local/ or the template origin; main() runs from the template's own sync.py."""
import contextlib
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

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


class GitFixtures:
    """Fixtures for --from between Git checkouts below the system temporary directory (a Git object path below a deep
    checkout's 90_temps can exceed the Windows path limit)."""

    COMMIT_COMMAND = ["-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-q", "-m", "fixture"]

    def setUp(self):
        base = tempfile.TemporaryDirectory(prefix="sync_")
        self.addCleanup(base.cleanup)
        self.base = pathlib.Path(base.name)

    def commit(self, root, git_root=None):
        """Record the manifest of the template at `root`, commit its Git work tree (`git_root`, default
        `root`) and return the commit."""
        git_root = git_root or root
        sync.write_manifest(str(root / "20_ops" / "tools"))
        if not (git_root / ".git").exists():
            sync._git(str(git_root), ["init", "-q"])
        sync._git(str(git_root), ["-c", "core.autocrlf=false", "add", "-A"])
        sync._git(str(git_root), self.COMMIT_COMMAND)
        return sync._git(str(git_root), ["rev-parse", "HEAD"])

    def template(self, name):
        root = self.base / name
        common_tree(root)
        return root

    def record_origin(self, root, baseline):
        origin = {"schema": "comsect1.template-origin.v1", "template": "fixture", "baseline_commit": baseline}
        support.write(root / ".instructions" / "template-origin.json", json.dumps(origin) + "\n")

    def product(self, name, baseline):
        """A product with only owned instruction files; `baseline` is its recorded template commit (None:
        no origin record yet, the first adoption)."""
        root = self.base / name
        (root / "20_ops" / "tools").mkdir(parents=True)
        for owned in OWNED[:-1]:
            support.write(root / ".instructions" / owned, "owned %s\n" % owned)
        if baseline is not None:
            self.record_origin(root, baseline)
        return root

    def owned_bytes(self, root):
        return {name: (root / ".instructions" / name).read_bytes() for name in OWNED
                if (root / ".instructions" / name).exists()}

    def text(self, root, name):
        return (root / ".instructions" / name).read_text(encoding="utf-8")


class FromTest(GitFixtures, unittest.TestCase):
    def test_copy_then_three_way_merge_leaves_owned_files_untouched(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", None)
        owned = self.owned_bytes(product)
        commit, outcomes = sync.copy_from(str(template), str(product))
        self.assertEqual(commit, first)
        self.assertEqual(outcomes, [(sync.ADDED, ".instructions/README.md"), (sync.ADDED, ".instructions/policy.md"),
                                    (sync.ADDED, ".instructions/rules.md")])
        self.assertEqual(sync.check_instructions(str(product / "20_ops" / "tools")), [])
        self.assertEqual(self.owned_bytes(product), owned)
        self.record_origin(product, first)
        owned = self.owned_bytes(product)
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
        self.assertEqual(self.text(product, "README.md"), "# README.md, second\n")
        self.assertEqual(self.text(product, "policy.md"), "# policy.md, adapted\n")
        self.assertEqual(self.text(product, "rules.md"), "# rules.md, adapted\n")
        self.assertEqual(self.owned_bytes(product), owned)
        self.assertEqual(sync.check_instructions(str(product / "20_ops" / "tools")),
                         [("MODIFIED", ".instructions/policy.md"), ("MODIFIED", ".instructions/rules.md")])

    def test_an_absent_file_is_declined_only_when_the_baseline_held_it(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", first)
        support.write(product / ".instructions" / "README.md", "# README.md\n")
        support.write(product / ".instructions" / "policy.md", "# policy.md\n")
        support.write(template / ".instructions" / "new.md", "# new.md\n")
        self.commit(template)
        outcomes = sync.copy_from(str(template), str(product))[1]
        self.assertEqual(outcomes, [(sync.ADDED, ".instructions/new.md"), (sync.DECLINED, ".instructions/rules.md")])
        self.assertFalse((product / ".instructions" / "rules.md").exists())
        tools = product / "20_ops" / "tools"
        self.assertNotIn(".instructions/rules.md", sync.read_manifest(str(tools))[1])
        self.assertEqual(sync.check_instructions(str(tools)), [])
        self.assertEqual(sync.check(str(tools)), [])

    def test_an_unknown_baseline_adds_absent_files_and_keeps_differing_ones_as_conflicts(self):
        template = self.template("template")
        self.commit(template)
        product = self.product("product", "f" * 40)
        support.write(product / ".instructions" / "policy.md", "# policy.md, older\n")
        outcomes = sync.copy_from(str(template), str(product))[1]
        self.assertEqual(outcomes, [(sync.ADDED, ".instructions/README.md"), (sync.CONFLICT, ".instructions/policy.md"),
                                    (sync.ADDED, ".instructions/rules.md")])
        self.assertEqual(self.text(product, "policy.md"), "# policy.md, older\n")

    def test_an_abbreviated_baseline_is_resolved_in_the_template(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", first[:7])
        support.write(product / ".instructions" / "README.md", "# README.md\n")
        outcomes = sync.copy_from(str(template), str(product))[1]
        self.assertEqual(outcomes, [(sync.DECLINED, ".instructions/policy.md"),
                                    (sync.DECLINED, ".instructions/rules.md")])

    def test_a_derived_template_passes_its_adaptations_and_extra_files_to_its_members(self):
        upstream = self.template("upstream")
        first = self.commit(upstream)
        derived = self.product("derived", None)
        sync.copy_from(str(upstream), str(derived))
        self.record_origin(derived, first)
        support.write(derived / ".instructions" / "policy.md", "# policy.md, derived\n")
        support.write(derived / ".instructions" / "derived-only.md", "# derived-only.md\n")
        self.commit(derived)
        self.assertEqual(sync.check_instructions(str(derived / "20_ops" / "tools")), [])
        member = self.product("member", None)
        outcomes = sync.copy_from(str(derived), str(member))[1]
        self.assertIn((sync.ADDED, ".instructions/derived-only.md"), outcomes)
        self.assertEqual(self.text(member, "policy.md"), "# policy.md, derived\n")
        self.assertEqual(sync.check_instructions(str(member / "20_ops" / "tools")), [])
        # The upstream changes only README.md: the derived template's adaptation is kept, not overwritten.
        support.write(upstream / ".instructions" / "README.md", "# README.md, upstream second\n")
        self.commit(upstream)
        outcomes = sync.copy_from(str(upstream), str(derived))[1]
        self.assertEqual(outcomes, [(sync.UPDATED, ".instructions/README.md"), (sync.KEPT, ".instructions/policy.md")])
        self.assertEqual(self.text(derived, "policy.md"), "# policy.md, derived\n")
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


class MainTest(GitFixtures, unittest.TestCase):
    """sync.py through main(), run from the fixture template's own copy of sync.py as the procedure states."""

    def fixture_module(self, template, name):
        return support.load_file(template / "20_ops" / "tools" / "comsect1_maint" / "sync.py", name)

    def run_main(self, template, argv):
        """(exit code, stdout, stderr) of main() of the sync.py inside `template`."""
        module = self.fixture_module(template, "sync_fixture_main")
        streams = [io.TextIOWrapper(io.BytesIO()) for _ in range(2)]
        with contextlib.redirect_stdout(streams[0]), contextlib.redirect_stderr(streams[1]):
            code = module.main(argv)
        texts = []
        for stream in streams:
            stream.flush()
            texts.append(stream.buffer.getvalue().decode("utf-8"))
        return code, texts[0], texts[1]

    def synchronize(self, template, product):
        return self.run_main(template, ["--from", str(template), "--to", str(product)])

    def test_a_conflict_exits_1_without_a_baseline_line(self):
        template = self.template("template")
        self.commit(template)
        product = self.product("product", "f" * 40)
        support.write(product / ".instructions" / "policy.md", "# policy.md, older\n")
        code, out, err = self.synchronize(template, product)
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT .instructions/policy.md", out)
        self.assertNotIn("baseline_commit:", out + err)
        self.assertIn("sync: CONFLICT: 1 common instruction file(s)", err)

    def test_kept_files_are_explained_and_a_clean_run_prints_the_baseline(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", first)
        for name in ("README.md", "rules.md"):
            support.write(product / ".instructions" / name, "# %s\n" % name)
        support.write(product / ".instructions" / "policy.md", "# policy.md, adapted\n")
        code, out, err = self.synchronize(template, product)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("KEPT .instructions/policy.md", out)
        self.assertIn("reports each as MODIFIED until this repository, as a derived template", out)
        self.assertIn("baseline_commit: %s" % first, out)

    def test_a_missing_origin_record_is_a_first_adoption(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", None)
        code, out, err = self.synchronize(template, product)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ADDED .instructions/rules.md", out)
        self.assertIn("baseline_commit: %s" % first, out)

    def test_a_malformed_origin_record_is_refused_before_any_write(self):
        template = self.template("template")
        self.commit(template)
        for index, text in enumerate(("{not json\n", json.dumps({"schema": "other", "baseline_commit": "f" * 40}))):
            product = self.product("product_%d" % index, None)
            support.write(product / ".instructions" / "template-origin.json", text)
            code, out, err = self.synchronize(template, product)
            self.assertEqual(code, 1)
            self.assertIn("is unreadable or lacks schema comsect1.template-origin.v1", err)
            self.assertFalse((product / "20_ops" / "tools" / "comsect1_maint").exists())
            self.assertFalse((product / ".instructions" / "README.md").exists())

    def test_a_linked_instruction_folder_is_refused(self):
        template = self.template("template")
        self.commit(template)
        product = self.product("product", None)
        module = self.fixture_module(template, "sync_fixture_link")
        original = module._is_link
        target = os.path.normcase(str(product / ".instructions"))
        with mock.patch.object(module, "_is_link", side_effect=lambda path: os.path.normcase(path) == target
                               or original(path)):
            code, out, err = self.synchronize_with(module, template, product)
        self.assertEqual(code, 1)
        self.assertIn(".instructions is a link or not a folder", err)
        self.assertFalse((product / "20_ops" / "tools" / "comsect1_maint").exists())

    def synchronize_with(self, module, template, product):
        streams = [io.TextIOWrapper(io.BytesIO()) for _ in range(2)]
        with contextlib.redirect_stdout(streams[0]), contextlib.redirect_stderr(streams[1]):
            code = module.main(["--from", str(template), "--to", str(product)])
        for stream in streams:
            stream.flush()
        return (code,) + tuple(stream.buffer.getvalue().decode("utf-8") for stream in streams)

    def test_a_declined_file_is_printed_and_does_not_change_the_exit(self):
        template = self.template("template")
        first = self.commit(template)
        product = self.product("product", first)
        for name in ("README.md", "policy.md"):
            support.write(product / ".instructions" / name, "# %s\n" % name)
        code, out, err = self.synchronize(template, product)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("DECLINED .instructions/rules.md", out)
        self.assertEqual(self.run_main(product, ["--check"])[0], 0)

    def test_write_manifest_refuses_an_unreadable_instruction_name(self):
        template = self.template("template")
        manifest = template / "20_ops" / "tools" / "comsect1_maint" / "sync_manifest.json"
        sync.write_manifest(str(template / "20_ops" / "tools"))
        before = manifest.read_bytes()
        support.write(template / ".instructions" / "_draft.md", "# odd name\n")
        code, out, err = self.run_main(template, ["--write-manifest"])
        self.assertEqual(code, 1)
        self.assertIn("_draft.md has a name outside the allowed form .instructions/<name>.md", err)
        self.assertEqual(manifest.read_bytes(), before)

    def test_a_template_below_the_top_level_of_its_work_tree_is_refused(self):
        outer = self.base / "outer"
        template = outer / "template"
        common_tree(template)
        self.commit(template, git_root=outer)
        product = self.product("product", None)
        code, out, err = self.synchronize(template, product)
        self.assertEqual(code, 1)
        self.assertIn("is not the top level of its Git work tree", err)
        self.assertFalse((product / "20_ops" / "tools" / "comsect1_maint").exists())

    def test_only_the_template_sync_py_synchronizes(self):
        template = self.template("template")
        self.commit(template)
        other = self.template("other")
        self.commit(other)
        product = self.product("product", None)
        code, out, err = self.run_main(other, ["--from", str(template), "--to", str(product)])
        self.assertEqual(code, 1)
        self.assertIn("synchronization runs the sync.py of the template synchronized from", err)
        self.assertEqual(self.run_main(template, ["--from", str(template)])[0], 2)
        self.assertFalse((product / "20_ops" / "tools" / "comsect1_maint").exists())


if __name__ == "__main__":
    unittest.main()
