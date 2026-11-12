# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Synchronize and check the common layer of the maintainer tools (Python standard library only).

The common layer is `20_ops/tools/comsect1_maint/**`, the entry shims
`native_gate.py`, `cell_registry.py`, `cross_build.py`, `build.py`, `gate.py`
(a Rust product keeps its own `build.py`, `gate.py` and has no use for the C
shims: only `cell_registry.py` is common there), `text_format.py`, and the
common instruction files: every `*.md` directly in `.instructions/`. Every other
file of `20_ops/tools/` is local and never written here; `.instructions/kind/`,
`.instructions/local/`, `.instructions/templates/` and
`.instructions/template-origin.json` are never written either.

    python -I -B 20_ops/tools/comsect1_maint/sync.py --write-manifest
    python -I -B 20_ops/tools/comsect1_maint/sync.py --check
    python -I -B <this file> --from <template checkout> [--to <product root>]

`--write-manifest` (in a template, a derived template included) records the
sha256 of every common file in `comsect1_maint/sync_manifest.json`. `--check`
(in a product) reports `MODIFIED`, `MISSING` or `EXTRA` common paths and exits 1
on any. `--from` copies the common layer from a clean template checkout whose
own files match its manifest, refuses links, moves stale package files through
`99_trash`, and prints the template commit to record as `baseline_commit` in
`.instructions/template-origin.json`. Tool files are copied verbatim. A common
instruction file is merged three ways against the template's file at the
product's recorded `baseline_commit`: it is copied when the product lacks it or
holds the baseline bytes (`ADDED`, `UPDATED`), kept when only the product
changed it (`KEPT`), and kept and reported when both changed it or the baseline
is unknown (`CONFLICT`, exit 1). Exit 0 success, 1 a finding, conflict or
refusal, 2 usage. `__pycache__` folders are ignored (`-B` writes none).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys

SCHEMA = "comsect1.maint_sync_manifest.v1"
PACKAGE = "comsect1_maint"
MANIFEST_RELATIVE = PACKAGE + "/sync_manifest.json"
SHIMS = ("build.py", "cell_registry.py", "cross_build.py", "gate.py", "native_gate.py")
RUST_SHIMS = ("cell_registry.py",)
ROOT_FILES = ("text_format.py",)
IGNORED_DIRECTORIES = ("__pycache__",)
# The common instruction files: the *.md files directly in .instructions/ (repository-relative manifest
# keys); the record of the template origin, read for its baseline commit and never written.
INSTRUCTIONS = ".instructions"
INSTRUCTION_PATTERN = re.compile(r"\.instructions/[A-Za-z0-9][A-Za-z0-9._-]*\.md")
ORIGIN_RELATIVE = INSTRUCTIONS + "/template-origin.json"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
# --from outcomes of a common instruction file that differs from the template's.
ADDED, UPDATED, KEPT, CONFLICT = "ADDED", "UPDATED", "KEPT", "CONFLICT"


class SyncError(Exception):
    """The common layer cannot be read, written or trusted; the message is the reason."""


def _is_link(path):
    if not os.path.lexists(path):
        return False
    info = os.lstat(path)
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or (hasattr(os.path, "isjunction") and os.path.isjunction(path)))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _package_files(tools_root):
    """Every file below comsect1_maint/ except the manifest, as tools-relative paths; refuses links."""
    root = os.path.join(tools_root, PACKAGE)
    if _is_link(root) or not os.path.isdir(root):
        raise SyncError("%s is absent or a link" % root)
    found = []
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in IGNORED_DIRECTORIES)
        for name in names + files:
            if _is_link(os.path.join(directory, name)):
                raise SyncError("link inside the common layer: %s" % os.path.join(directory, name))
        for name in sorted(files):
            relative = os.path.relpath(os.path.join(directory, name), tools_root).replace("\\", "/")
            if relative != MANIFEST_RELATIVE:
                found.append(relative)
    return sorted(found)


def _repo_root(tools_root):
    return os.path.dirname(os.path.dirname(os.path.abspath(tools_root)))


def _instruction_files(repo_root):
    """Every regular *.md directly in .instructions/ as repository-relative paths (none without the
    folder); kind/, local/, templates/ and template-origin.json are not among them; refuses links."""
    root = os.path.join(repo_root, INSTRUCTIONS)
    if not os.path.lexists(root):
        return []
    if _is_link(root) or not os.path.isdir(root):
        raise SyncError("%s is a link or not a folder" % root)
    found = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not name.endswith(".md"):
            continue
        if _is_link(path):
            raise SyncError("link among the common instructions: %s" % path)
        if os.path.isfile(path):
            found.append(INSTRUCTIONS + "/" + name)
    return found


def selected(names, language):
    """The common paths of a product of `language` ("c", "rust" or None for every path)."""
    if language not in (None, "c", "rust"):
        raise SyncError("unknown language %r" % language)
    if language == "rust":
        excluded = set(SHIMS) - set(RUST_SHIMS)
        return [name for name in names if name not in excluded]
    return list(names)


def product_language(tools_root):
    """The `language` of the product's maint_config.json, or None when it has none (the template)."""
    path = os.path.join(tools_root, "maint_config.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle).get("language")
    except (OSError, ValueError, AttributeError) as error:
        raise SyncError("maint_config.json unreadable: %s" % error)


def read_manifest(tools_root):
    """(tool files, instruction files) of the manifest: tools-relative and repository-relative path -> sha256."""
    path = os.path.join(tools_root, *MANIFEST_RELATIVE.split("/"))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise SyncError("manifest %s unreadable: %s" % (path, error))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    instructions = manifest.get("instructions") if isinstance(manifest, dict) else None
    if (manifest.get("schema") != SCHEMA or set(manifest) != {"schema", "files", "instructions"}
            or not isinstance(files, dict) or not isinstance(instructions, dict)
            or not all(isinstance(value, str) and len(value) == 64
                       for value in list(files.values()) + list(instructions.values()))
            or not all(INSTRUCTION_PATTERN.fullmatch(name) for name in instructions)):
        raise SyncError("manifest %s is not a %s record" % (path, SCHEMA))
    return files, instructions


def write_manifest(tools_root):
    """Record every common file of the template; returns the manifest path."""
    names = _package_files(tools_root) + list(SHIMS) + list(ROOT_FILES)
    files = {}
    for name in sorted(names):
        path = os.path.join(tools_root, *name.split("/"))
        if _is_link(path) or not os.path.isfile(path):
            raise SyncError("common file %s is absent or a link" % path)
        files[name] = _sha256(path)
    repo_root = _repo_root(tools_root)
    instructions = {name: _sha256(os.path.join(repo_root, *name.split("/"))) for name in _instruction_files(repo_root)}
    if not instructions:
        raise SyncError("the template holds no common instruction file (*.md) in %s"
                        % os.path.join(repo_root, INSTRUCTIONS))
    path = os.path.join(tools_root, *MANIFEST_RELATIVE.split("/"))
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"schema": SCHEMA, "files": files, "instructions": instructions},
                                indent=2, sort_keys=True) + "\n")
    return path


def check(tools_root, language=None):
    """[(MODIFIED|MISSING|EXTRA, path)] of the common tool files against the manifest, sorted by path."""
    files = read_manifest(tools_root)[0]
    expected = selected(sorted(files), language)
    problems = []
    for name in expected:
        path = os.path.join(tools_root, *name.split("/"))
        if _is_link(path):
            problems.append(("MODIFIED", name))
        elif not os.path.isfile(path):
            problems.append(("MISSING", name))
        elif _sha256(path) != files[name]:
            problems.append(("MODIFIED", name))
    for name in _package_files(tools_root):
        if name not in files:
            problems.append(("EXTRA", name))
    return sorted(problems, key=lambda item: (item[1], item[0]))


def check_instructions(tools_root):
    """[(MODIFIED|MISSING|EXTRA, path)] of the common instruction files against the manifest, sorted by path.

    Kept apart from `check`: the build and Gate drift gate covers the tool files only."""
    instructions = read_manifest(tools_root)[1]
    repo_root = _repo_root(tools_root)
    problems = []
    for name in sorted(instructions):
        path = os.path.join(repo_root, *name.split("/"))
        if _is_link(path):
            problems.append(("MODIFIED", name))
        elif not os.path.isfile(path):
            problems.append(("MISSING", name))
        elif _sha256(path) != instructions[name]:
            problems.append(("MODIFIED", name))
    for name in _instruction_files(repo_root):
        if name not in instructions:
            problems.append(("EXTRA", name))
    return sorted(problems, key=lambda item: (item[1], item[0]))


def _baseline_commit(product_root):
    """The baseline_commit of the product's template origin record, or None when it has no usable one."""
    path = os.path.join(product_root, *ORIGIN_RELATIVE.split("/"))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    commit = record.get("baseline_commit") if isinstance(record, dict) else None
    return commit if isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit) else None


def _baseline_sha256(template_root, commit, name):
    """sha256 of the template's file `name` at `commit`, or None when the checkout lacks either."""
    if commit is None:
        return None
    try:
        done = subprocess.run(["git", "-C", template_root, "cat-file", "blob", "%s:%s" % (commit, name)],
                              capture_output=True, stdin=subprocess.DEVNULL, timeout=120,
                              creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SyncError("git on %s failed: %s" % (template_root, error))
    return hashlib.sha256(done.stdout).hexdigest() if done.returncode == 0 else None


def _merge_instructions(template_root, product_root, instructions):
    """Bring the template's common instruction files into the product; returns [(outcome, path)].

    Against the template's file at the product's baseline_commit: absent -> ADDED; the product holds
    the baseline bytes -> UPDATED (copied); only the product changed it -> KEPT; both changed it or
    the baseline is unknown -> CONFLICT (kept for a resolution by hand). Equal files are left alone."""
    baseline = _baseline_commit(product_root)
    outcomes = []
    for name in sorted(instructions):
        source = os.path.join(template_root, *name.split("/"))
        target = os.path.join(product_root, *name.split("/"))
        if not os.path.isfile(target):
            outcome = ADDED
        else:
            present = _sha256(target)
            if present == instructions[name]:
                continue
            base = _baseline_sha256(template_root, baseline, name)
            outcome = UPDATED if present == base else KEPT if base == instructions[name] else CONFLICT
        if outcome in (ADDED, UPDATED):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target)
            if _sha256(target) != instructions[name]:
                raise SyncError("copied common instruction %s does not verify" % target)
        outcomes.append((outcome, name))
    return outcomes


def _git(root, arguments):
    try:
        done = subprocess.run(["git", "-C", root] + arguments, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=120, creationflags=0x08000000 if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SyncError("git on %s failed: %s" % (root, error))
    if done.returncode != 0:
        raise SyncError("git %s on %s failed: %s" % (" ".join(arguments), root, done.stderr.strip()))
    return done.stdout.strip()


def _quarantine_files(product_root, paths):
    """Move stale common files through 99_trash/<cleanup-id>/ and delete only that child."""
    trash = os.path.join(product_root, "99_trash")
    if _is_link(trash):
        raise SyncError("99_trash is a link")
    child = os.path.join(trash, "maint_sync_%s_%d" % (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"), os.getpid()))
    os.makedirs(child)
    for index, path in enumerate(paths):
        os.rename(path, os.path.join(child, "%d_%s" % (index, os.path.basename(path))))
        if os.path.lexists(path):
            raise SyncError("stale file still present: %s" % path)
    shutil.rmtree(child)


def copy_from(template_root, product_root):
    """Copy the common layer of a clean template checkout into the product; returns the template commit
    and the [(outcome, path)] of every common instruction file that was added, updated, kept or is in
    conflict (`_merge_instructions`)."""
    template_root, product_root = os.path.abspath(template_root), os.path.abspath(product_root)
    source_tools = os.path.join(template_root, "20_ops", "tools")
    target_tools = os.path.join(product_root, "20_ops", "tools")
    if os.path.normcase(source_tools) == os.path.normcase(target_tools):
        raise SyncError("template and product are the same checkout")
    for root in (template_root, product_root, source_tools, target_tools):
        if _is_link(root) or not os.path.isdir(root):
            raise SyncError("%s is absent or a link" % root)
    if _git(template_root, ["status", "--porcelain=v1", "--untracked-files=all"]):
        raise SyncError("the template checkout %s has changes; synchronize from a commit" % template_root)
    commit = _git(template_root, ["rev-parse", "HEAD"])
    files, instructions = read_manifest(source_tools)
    drift = check(source_tools) + check_instructions(source_tools)
    if drift:
        raise SyncError("the template's own common layer differs from its manifest: %s"
                        % ", ".join("%s %s" % item for item in drift))
    language = product_language(target_tools)
    names = selected(sorted(files), language) + [MANIFEST_RELATIVE]
    for name in names:
        target = os.path.join(target_tools, *name.split("/"))
        cursor = os.path.dirname(target)
        while os.path.normcase(cursor) != os.path.normcase(target_tools):
            if _is_link(cursor):
                raise SyncError("link on the target path: %s" % cursor)
            cursor = os.path.dirname(cursor)
        if _is_link(target):
            raise SyncError("target is a link: %s" % target)
    target_instructions = os.path.join(product_root, INSTRUCTIONS)
    if os.path.lexists(target_instructions) and (_is_link(target_instructions)
                                                 or not os.path.isdir(target_instructions)):
        raise SyncError("%s is a link or not a folder" % target_instructions)
    for name in instructions:
        target = os.path.join(product_root, *name.split("/"))
        if _is_link(target) or (os.path.lexists(target) and not os.path.isfile(target)):
            raise SyncError("common instruction target is a link or not a file: %s" % target)
    stale = []
    if os.path.isdir(os.path.join(target_tools, PACKAGE)):
        stale = [os.path.join(target_tools, *name.split("/")) for name in _package_files(target_tools)
                 if name not in files]
    for name in names:
        source = os.path.join(source_tools, *name.split("/"))
        target = os.path.join(target_tools, *name.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
    if stale:
        _quarantine_files(product_root, stale)
    remaining = check(target_tools, language)
    if remaining:
        raise SyncError("copied common layer does not verify: %s" % ", ".join("%s %s" % item for item in remaining))
    return commit, _merge_instructions(template_root, product_root, instructions)


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    parser = argparse.ArgumentParser(prog="sync.py", description="Synchronize or check the maintainer tools.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write-manifest", action="store_true")
    action.add_argument("--check", action="store_true")
    action.add_argument("--from", dest="source", metavar="TEMPLATE")
    parser.add_argument("--to", default=None, metavar="PRODUCT", help="product root for --from (default: this checkout)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return 2 if stop.code not in (0, None) else 0
    own_tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        if args.write_manifest:
            print("manifest: %s" % write_manifest(own_tools))
            return 0
        if args.check:
            problems = check(own_tools, product_language(own_tools)) + check_instructions(own_tools)
            for state, name in problems:
                print("%s %s" % (state, name))
            print("sync check: %s" % ("FAIL (%d)" % len(problems) if problems else "PASS"))
            return 1 if problems else 0
        product_root = os.path.abspath(args.to) if args.to else os.path.dirname(os.path.dirname(own_tools))
        commit, outcomes = copy_from(args.source, product_root)
        for outcome, name in outcomes:
            print("%s %s" % (outcome, name))
        print("synchronized from %s" % os.path.abspath(args.source))
        print("baseline_commit: %s" % commit)
        conflicts = [name for outcome, name in outcomes if outcome == CONFLICT]
        if conflicts:
            print("sync: CONFLICT: %d common instruction file(s) changed both here and in the template since the "
                  "recorded baseline_commit (or it is unknown) and were kept unchanged: %s. Next step: merge each "
                  "by hand, then record baseline_commit." % (len(conflicts), ", ".join(conflicts)), file=sys.stderr)
            return 1
        return 0
    except (OSError, SyncError) as error:
        print("sync: FAIL: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
