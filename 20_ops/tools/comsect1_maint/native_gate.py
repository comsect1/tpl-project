# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Native C Gate lane driver for Comsect1 Cells (Python standard library only).

Usage:
    python -I -B 20_ops/tools/native_gate.py --cell <cell-root> \
        --config <lane-config.json> [--lane native|plain|both] [--run-id ID] \
        [--accept-tool-drift]

The Cell is read only. All output goes to
`90_temps/verification/native_gate/<run-id>/` of this repository. The tool
makes no network access; it only starts the installed Gate set binaries and
clang (header closure and resource directory). Every process runs without a
window, with stdin closed, under a timeout, and its whole process tree is
killed on timeout. Text is decoded as UTF-8 explicitly everywhere. Every path
the plan reads is checked component by component before canonicalization:
a linked or reparse component is refused.

Consumed tools are never locked by content. `expected_sha256` holds the Gate
binary digests last recorded for the configuration; the run reads the actual
digests, records both and every differing key (`tool_sha256`,
`tool_sha256_recorded`, `tool_sha256_differs`) and never refuses on a
difference.

The clang compiler and resource headers come from the one reference toolchain
pin (`reference_toolchain.json`, synchronized with this module) located by
`reference_tools` (the `COMSECT1_REFERENCE_TOOLS` override, else the nearest
`00_reference/tools/` ancestor of the checkout); the lane configuration names
no toolchain. The pinned bundle's inventory is admitted (its members are the
complete directory, locked for the run, and the closure may only read
inventory resource headers; the manifest digest, each member's size and
SHA-256 and the semantic runtime digest are read and recorded, with every
difference from the pin and the manifest, in `native_toolchain`, never
refused). A missing bundle fails closed; there is no discovery.
The analysis standard is the build standard, and the build standard is C23
(`C_STANDARD`). There is no downgrade: a frontend that cannot analyse C23
fails the lane closed with the Gate's own message.
`publications` (optional) selects the input owner protocol: when the member is
present (even `[]`), each `comsect1-native-input` call names the original
source Cells of its external inputs (`--source-cell`) and the selected SDK
publication roots (`--publication`), which needs a native-input that accepts
them; when it is absent, external inputs are passed by id only.

Only an established violation blocks. The Gate verdict fixes the handoff and the
Gate exit: FAIL is BLOCK and exit 1; INDETERMINATE, REQ and WARN are
CONTINUE_WITH_RISK and exit 0; PASS is CONTINUE and exit 0; an execution error
writes no conclusion and exits 2. A lane proceeds if and only if the Gate exit
is 0, the handoff is not BLOCK and `outcome_summary.violation_count` is 0; the
assessment count, `assessment_blocked` and a verdict other than PASS never
block. The report's `assessment` member (`reasons`, `missing_capabilities`,
`unassessed_site_count`) is recorded in the lane and printed after its line.

Exit codes (tool codes, not Gate codes; the Gate exit code is recorded in the
summary). With `--lane both` the highest code of the lanes is returned.

     0  CONTINUE             no selected lane has Gate verdict FAIL (PASS, WARN, REQ, INDETERMINATE)
     1  FAIL                 a lane has Gate verdict FAIL (handoff BLOCK)
     2  USAGE                invalid command line
        (3 is retired: INDETERMINATE no longer blocks)
     4  CONFIG               lane configuration unreadable or invalid, or maintainer tools drift
     5  TOOL_SET_UNAVAILABLE installed Gate set or a binary is absent
     7  CLANG_UNAVAILABLE    the pinned reference toolchain cannot be located or admitted
     8  CELL_INVALID         Cell root missing, not a Cell, link, or no C unit
     9  OUTPUT_INVALID       run id or output directory refused
    10  PLAN_FAILED          header closure or native plan could not be built
    11  NATIVE_INPUT_FAILED  comsect1-native-input failed
    12  GATE_REFUSED         Gate wrote no usable report or contradicted itself
    13  TIMEOUT              a process exceeded its timeout and was killed
    14  INTERNAL             unexpected tool error
"""

import argparse
from contextlib import ExitStack, contextmanager
import ctypes
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import config as maint_config
from . import path_guard as _path_guard
from . import process
from . import reference_tools
from .process import is_link, sha256_file, sha256_text, write_text, write_json

EXIT_CONTINUE = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_CONFIG = 4
EXIT_TOOL_SET_UNAVAILABLE = 5
EXIT_CLANG_UNAVAILABLE = 7
EXIT_CELL_INVALID = 8
EXIT_OUTPUT_INVALID = 9
EXIT_PLAN_FAILED = 10
EXIT_NATIVE_INPUT_FAILED = 11
EXIT_GATE_REFUSED = 12
EXIT_TIMEOUT = 13
EXIT_INTERNAL = 14

EXIT_NAMES = {
    0: "CONTINUE", 1: "FAIL", 2: "USAGE", 4: "CONFIG",
    5: "TOOL_SET_UNAVAILABLE", 7: "CLANG_UNAVAILABLE",
    8: "CELL_INVALID", 9: "OUTPUT_INVALID", 10: "PLAN_FAILED",
    11: "NATIVE_INPUT_FAILED", 12: "GATE_REFUSED", 13: "TIMEOUT", 14: "INTERNAL",
}

TOOL_KEYS = ("gate", "native_input", "c_frontend", "libclang")
# The one ISO C edition of every Cell: C23 (ISO/IEC 9899:2024), owner rule 2026-11-02.
C_STANDARD = "c23"
CONFIG_KEYS = ("gate_set", "expected_sha256", "build", "timeouts_seconds")
GATE_SET_KEYS = ("runtime_root", "active_set_file", "binary_subdirectory", "binaries")
# Members that earlier configurations carried and the current rule replaces: each is refused by name.
REPLACED_MEMBERS = {
    "clang_resource": "the toolchain comes from the synchronized reference toolchain pin "
                      "(comsect1_maint/reference_toolchain.json); delete clang_resource",
    "frontend_standards": "the analysis standard is the build standard C23 with no downgrade; "
                          "delete gate_set.frontend_standards",
}
RUN_ID_PATTERN = process.RUN_ID_PATTERN
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
AUTHORITY_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
REPARSE_FLAG = process.REPARSE_FLAG
CREATE_NO_WINDOW = process.CREATE_NO_WINDOW
CREATE_NEW_PROCESS_GROUP = process.CREATE_NEW_PROCESS_GROUP
REPO_ROOT = Path(process.REPO_ROOT)
DEPS_FOLDER = process.DEPS_FOLDER
COUNT_NAMES = ("assessment_count", "debt_count", "limit_count", "notice_count", "rework_count", "violation_count")
VERDICTS = ("PASS", "WARN", "REQ", "INDETERMINATE", "FAIL")
# The Gate handoff and exit of each verdict; Gate exit 2 is an execution error without a conclusion.
VERDICT_HANDOFF = {"PASS": "CONTINUE", "WARN": "CONTINUE_WITH_RISK", "REQ": "CONTINUE_WITH_RISK",
                   "INDETERMINATE": "CONTINUE_WITH_RISK", "FAIL": "BLOCK"}
VERDICT_GATE_EXIT = {verdict: 1 if verdict == "FAIL" else 0 for verdict in VERDICTS}
ASSESSMENT_KEYS = {"reasons", "missing_capabilities", "unassessed_site_count"}


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def checked_original_path(path):
    try:
        return _path_guard.checked_path(path)
    except (OSError, ValueError) as error:
        raise ToolError(EXIT_PLAN_FAILED, "unsafe original path %s: %s" % (path, error)) from error


def expand(text, what):
    """Expand %NAME% references; an unset or non-absolute variable is an error."""
    missing = []

    def replace(match):
        value = os.environ.get(match.group(1))
        if value is None or not os.path.isabs(value):
            missing.append(match.group(1))
            return match.group(0)
        return value

    result = re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", replace, text)
    if missing:
        raise ToolError(EXIT_CONFIG, "%s uses unset or non-absolute variable %s" % (what, ", ".join(missing)))
    return result


# ---------------------------------------------------------------- configuration

def require(condition, message):
    if not condition:
        raise ToolError(EXIT_CONFIG, message)


def check_keys(section, name, keys):
    require(isinstance(section, dict), "%s must be an object" % name)
    require(set(section) == set(keys), "%s must have exactly the members %s" % (name, ", ".join(sorted(keys))))


def load_config(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
        config = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ToolError(EXIT_CONFIG, "configuration %s unreadable: %s" % (path, error))
    require(isinstance(config, dict), "configuration must be an object")
    replaced = [name for name in REPLACED_MEMBERS
                if name in config or name in (config.get("gate_set") if isinstance(config.get("gate_set"), dict) else {})]
    require(not replaced, "configuration %s: %s" % (path, "; ".join(REPLACED_MEMBERS[name] for name in replaced)))
    check_keys(config, "configuration", CONFIG_KEYS + (("publications",) if "publications" in config else ()))
    gate_set = config["gate_set"]
    check_keys(gate_set, "gate_set", GATE_SET_KEYS)
    for key in ("runtime_root", "active_set_file", "binary_subdirectory"):
        require(isinstance(gate_set[key], str) and gate_set[key], "gate_set.%s must be a non-empty string" % key)
    check_keys(gate_set["binaries"], "gate_set.binaries", TOOL_KEYS)
    for key in TOOL_KEYS:
        name = gate_set["binaries"][key]
        require(isinstance(name, str) and name and Path(name).name == name, "gate_set.binaries.%s must be a file name" % key)
    check_keys(config["expected_sha256"], "expected_sha256", TOOL_KEYS)
    for key in TOOL_KEYS:
        value = config["expected_sha256"][key]
        require(isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None,
                "expected_sha256.%s must be 64 lowercase hex digits" % key)
    build = config["build"]
    check_keys(build, "build", ("target", "standard", "plain_char", "virtual_root",
                                "preprocessing_epoch_seconds", "definitions", "include_roots",
                                "external_include_roots", "system_include_roots"))
    for key in ("target", "virtual_root"):
        require(isinstance(build[key], str) and build[key], "build.%s must be a non-empty string" % key)
    require(build["standard"] == C_STANDARD,
            "build.standard is %r; every Cell is analysed as %s, with no downgrade" % (build["standard"], C_STANDARD))
    require(build["plain_char"] in ("signed", "unsigned"), "build.plain_char must be signed or unsigned")
    require(isinstance(build["preprocessing_epoch_seconds"], int) and not isinstance(build["preprocessing_epoch_seconds"], bool)
            and build["preprocessing_epoch_seconds"] >= 0, "build.preprocessing_epoch_seconds must be a non-negative integer")
    definitions = build["definitions"]
    require(isinstance(definitions, dict), "build.definitions must be an object")
    for name, value in definitions.items():
        require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is not None, "build.definitions name %r is not an identifier" % name)
        require(isinstance(value, str) and value != "" and "\n" not in value,
                "build.definitions.%s must be a non-empty single-line string (flag-style -DX is \"1\")" % name)
    roots = build["include_roots"]
    require(isinstance(roots, list) and roots, "build.include_roots must be a non-empty list")
    for root in roots:
        require(isinstance(root, str) and root, "build.include_roots entries must be non-empty strings")
        parts = root.replace("\\", "/").split("/")
        require(not os.path.isabs(root) and ".." not in parts and ":" not in root,
                "build.include_roots entry %r must be Cell-relative (\".\" is the Cell root)" % root)
    require(len(set(roots)) == len(roots), "build.include_roots has a duplicate entry")
    external_roots = build["external_include_roots"]
    require(isinstance(external_roots, list), "build.external_include_roots must be a list")
    for root in external_roots:
        require(isinstance(root, str) and root, "build.external_include_roots entries must be non-empty strings")
        parts = root.replace("\\", "/").split("/")
        require(not os.path.isabs(root) and ".." not in parts and ":" not in root and root != ".",
                "build.external_include_roots entry %r must be repository-relative (the membrane of a dependency Cell)" % root)
    require(len(set(external_roots)) == len(external_roots), "build.external_include_roots has a duplicate entry")
    system_roots = build["system_include_roots"]
    require(isinstance(system_roots, list), "build.system_include_roots must be a list")
    for root in system_roots:
        require(isinstance(root, str) and root and os.path.isabs(root) and ".." not in root.replace("\\", "/").split("/"),
                "build.system_include_roots entry %r must be an absolute directory (an external SDK, read only)" % (root,))
    require(len(set(system_roots)) == len(system_roots), "build.system_include_roots has a duplicate entry")
    publications = config.get("publications", [])
    require(isinstance(publications, list), "publications must be a list")
    for publication in publications:
        check_keys(publication, "publication", ("canonical_root", "inventory_member", "declaration_member"))
        require(isinstance(publication["canonical_root"], str) and publication["canonical_root"],
                "publication.canonical_root must explicitly select a root")
        for key in ("inventory_member", "declaration_member"):
            member = publication[key]
            require(isinstance(member, str) and member and "\\" not in member and ":" not in member
                    and all(part not in ("", ".", "..") and not part.endswith((" ", "."))
                            for part in member.split("/")),
                    "publication.%s must be an unaliased relative member" % key)
    timeouts = config["timeouts_seconds"]
    check_keys(timeouts, "timeouts_seconds", ("clang", "native_input", "gate"))
    for key, value in timeouts.items():
        require(isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3600,
                "timeouts_seconds.%s must be an integer from 1 to 3600" % key)
    config["_sha256"] = sha256_text(raw)
    return config


# ---------------------------------------------------------------- process control

def run_process(name, command, timeout, cwd, log_path=None):
    """Run one process; return (exit code, stdout text, stderr text)."""
    try:
        return process.run_process(name, command, timeout, cwd, log_path)
    except process.ProcessError as error:
        raise ToolError(EXIT_TIMEOUT if error.kind == "timeout" else EXIT_INTERNAL, error.message)


# ---------------------------------------------------------------- tools and clang

def resolve_gate_set(config):
    """Return (bin_dir, {key: Path}, release_set_id) of the active installed set."""
    gate_set = config["gate_set"]
    root = Path(expand(gate_set["runtime_root"], "gate_set.runtime_root"))
    active = root / gate_set["active_set_file"]
    try:
        record = json.loads(active.read_text(encoding="utf-8"))
        set_path = record["set_path"]
        set_id = record["release_set_id"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ToolError(EXIT_TOOL_SET_UNAVAILABLE, "active Gate set record %s (gate_set.runtime_root %s) unusable: %s. "
                        "Next step: install a Gate set there or correct gate_set.runtime_root." % (active, root, error))
    if not isinstance(set_path, str) or not isinstance(set_id, str) or os.path.isabs(set_path) \
            or ".." in set_path.replace("\\", "/").split("/"):
        raise ToolError(EXIT_TOOL_SET_UNAVAILABLE, "active Gate set record has an unsafe set_path")
    bin_dir = root / set_path / gate_set["binary_subdirectory"]
    if not bin_dir.is_dir():
        raise ToolError(EXIT_TOOL_SET_UNAVAILABLE, "Gate set binary directory %s of release set %s (from %s) is absent. "
                        "Next step: reinstall that set or select an installed one in %s." % (bin_dir, set_id, active, active))
    paths = {}
    for key in TOOL_KEYS:
        path = bin_dir / gate_set["binaries"][key]
        if not path.is_file():
            raise ToolError(EXIT_TOOL_SET_UNAVAILABLE, "Gate set binary %s (%s) of release set %s (from %s) is absent. "
                            "Next step: reinstall that set or select an installed one in %s."
                            % (path, key, set_id, active, active))
        paths[key] = path
    return bin_dir, paths, set_id


def tool_hashes(config, paths):
    """(actual digests, keys whose digest differs from the recorded expected_sha256). Metadata only:
    a difference is printed and recorded, never refused (consumers do not re-verify tools)."""
    actual = {key: sha256_file(paths[key]) for key in TOOL_KEYS}
    differs = [key for key in TOOL_KEYS if actual[key] != config["expected_sha256"][key]]
    for key in differs:
        print("tool_hash %s actual=%s recorded=%s differs (recorded, not a condition)"
              % (key, actual[key], config["expected_sha256"][key]))
    return actual, differs


def find_clang(config, runtime_sha256=None, locks=None, environ=None, pin_path=None):
    """Return (clang executable, resource include directory Path) of the pinned reference toolchain;
    `locks` holds the admitted inputs for the run. A missing pin or bundle fails closed."""
    try:
        selection = reference_tools.native_c_toolchain(REPO_ROOT, environ, pin_path)
    except reference_tools.ReferenceToolsError as error:
        raise ToolError(EXIT_CLANG_UNAVAILABLE, str(error)) from error
    return admit_toolchain(config, selection, runtime_sha256, locks)


def toolchain_member(root, relative):
    """Resolve only manifest-owned ordinary paths; never follow reparse points."""
    require(isinstance(relative, str) and relative and not relative.startswith("/"),
            "toolchain member must be a relative path")
    parts = relative.split("/")
    require(all(re.fullmatch(r"[A-Za-z0-9_.+-]+", part) and part not in (".", "..")
                and not part.endswith((".", " ")) for part in parts),
            "unsafe toolchain member path: %r" % relative)
    path = root.joinpath(*parts)
    for ancestor in (path, *path.parents):
        if ancestor.exists() and is_link(ancestor):
            raise ToolError(EXIT_CLANG_UNAVAILABLE, "toolchain reparse input: %s" % ancestor)
    return path


@contextmanager
def lock_toolchain_input(path, directory=False):
    """Keep selected Windows input bytes and path parents stable through Gate."""
    if os.name != "nt":
        raise ToolError(EXIT_CLANG_UNAVAILABLE, "native toolchain locking requires Windows")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0 if directory else 0x80000000, 1,
                                None, 3, 0x00200000 | (0x02000000 if directory else 0), None)
    if handle == ctypes.c_void_p(-1).value:
        raise ToolError(EXIT_CLANG_UNAVAILABLE, "cannot lock toolchain input: %s (winerror %s)" %
                        (path, ctypes.get_last_error()))
    try:
        if is_link(path):
            raise ToolError(EXIT_CLANG_UNAVAILABLE, "toolchain reparse input: %s" % path)
        yield
    finally:
        kernel.CloseHandle(handle)


def toolchain_inventory(root, manifest_path):
    observed = set()
    for directory, subdirs, files in os.walk(root, followlinks=False):
        for name in subdirs + files:
            path = Path(directory) / name
            if is_link(path):
                raise ToolError(EXIT_CLANG_UNAVAILABLE, "toolchain reparse input: %s" % path)
        for name in files:
            path = Path(directory) / name
            if path != manifest_path:
                observed.add(path.relative_to(root).as_posix())
    return observed


def admit_toolchain(config, selection, runtime_sha256, locks):
    """Admit the selected compiler/resource set (`reference_tools.native_c_toolchain`): its inventory is
    the complete directory and is locked for the run. Digests (manifest, members, semantic runtime) are
    read and recorded with every difference from the pin and the manifest; a difference is never refused."""
    manifest_path = Path(selection["manifest"])
    require(manifest_path.is_absolute(), "the reference toolchain provenance path must be absolute")
    try:
        raw = manifest_path.read_bytes()
        manifest_sha256 = hashlib.sha256(raw).hexdigest()
        record = json.loads(raw)
        check_keys(record, "native toolchain", ("kind", "source", "compiler", "semantic_runtime",
                                                "resource_directory", "files"))
        require(record["kind"] == "native_c_toolchain_inputs", "unexpected toolchain manifest kind")
        check_keys(record["source"], "toolchain source", ("release_url", "release_tag", "archive_url",
                                                          "archive_name", "archive_size", "archive_sha256"))
        require(all(isinstance(record["source"][key], str) and record["source"][key]
                    for key in ("release_url", "release_tag", "archive_url", "archive_name")),
                "toolchain source identity is incomplete")
        require(SHA256_PATTERN.fullmatch(record["source"]["archive_sha256"]) is not None
                and type(record["source"]["archive_size"]) is int and record["source"]["archive_size"] > 0,
                "toolchain archive identity is invalid")
        root = manifest_path.parent
        toolchain_member(root, manifest_path.name)
        require(isinstance(record["files"], list) and record["files"], "toolchain files are absent")
        members = {}
        folded = set()
        for member in record["files"]:
            check_keys(member, "toolchain file", ("path", "size", "sha256"))
            path = toolchain_member(root, member["path"])
            require(member["path"].casefold() not in folded and path != manifest_path,
                    "duplicate or manifest-alias toolchain member")
            folded.add(member["path"].casefold())
            require(type(member["size"]) is int and member["size"] >= 0
                    and isinstance(member["sha256"], str)
                    and SHA256_PATTERN.fullmatch(member["sha256"]) is not None,
                    "invalid toolchain file identity")
            members[member["path"]] = (path, member)
        compiler = toolchain_member(root, record["compiler"])
        runtime = toolchain_member(root, record["semantic_runtime"])
        resource = toolchain_member(root, record["resource_directory"]) / "include"
        require(record["compiler"] in members and record["semantic_runtime"] in members,
                "compiler/runtime absent from toolchain inventory")
        require(runtime == members[record["semantic_runtime"]][0], "toolchain semantic runtime path differs")
        directories = {root, *root.parents}
        for path, _ in members.values():
            directories.update(path.parents)
        for directory in sorted(directories, key=lambda path: (len(path.parts), str(path))):
            locks.enter_context(lock_toolchain_input(directory, True))
        locks.enter_context(lock_toolchain_input(manifest_path))
        require(manifest_path.read_bytes() == raw, "toolchain provenance changed during admission")
        actual = {}
        for name, (path, member) in members.items():
            locks.enter_context(lock_toolchain_input(path))
            require(path.is_file(), "toolchain member is absent: %s" % path)
            actual[name] = sha256_file(path)
        differs = sorted(name for name, (path, member) in members.items()
                         if path.stat().st_size != member["size"] or actual[name] != member["sha256"])
        require(toolchain_inventory(root, manifest_path) == set(members),
                "toolchain directory differs from its complete inventory")
        require((resource / "stddef.h").is_file() and (resource / "stdint.h").is_file(),
                "toolchain resource headers are absent")
        config["_resource_members"] = {str(path.resolve()).casefold(): actual[name]
                                       for name, (path, member) in members.items()}
        config["_native_toolchain"] = {
            "manifest": str(manifest_path), "manifest_sha256": manifest_sha256,
            "manifest_sha256_recorded": selection["manifest_sha256"],
            "pin": selection["pin"], "pin_path": selection["pin_path"],
            "reference_tools_root": selection["reference_tools_root"], "root_source": selection["root_source"],
            "source": record["source"], "compiler": str(compiler),
            "compiler_sha256": actual[record["compiler"]],
            "semantic_runtime_sha256": runtime_sha256,
            "semantic_runtime_sha256_listed": members[record["semantic_runtime"]][1]["sha256"],
            "resource_include": str(resource), "member_count": len(members), "members_differ": differs}
        for name in differs:
            print("toolchain member %s differs from its manifest entry (recorded, not a condition)" % name)
        if manifest_sha256 != selection["manifest_sha256"]:
            print("toolchain manifest digest %s differs from the recorded %s (recorded, not a condition)"
                  % (manifest_sha256, selection["manifest_sha256"]))
        if record["source"]["archive_sha256"] != selection["pin"]["archive_sha256"]:
            print("toolchain archive digest %s differs from the pinned %s (recorded, not a condition)"
                  % (record["source"]["archive_sha256"], selection["pin"]["archive_sha256"]))
        config["_native_toolchain_manifest"] = record
        config["_toolchain_inventory_check"] = lambda: require(
            toolchain_inventory(root, manifest_path) == set(members), "toolchain inventory changed during execution")
        return compiler, resource
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ToolError(EXIT_CLANG_UNAVAILABLE, "native toolchain input unavailable: %s" % error) from error


# ---------------------------------------------------------------- Cell and output

def check_cell(cell_argument):
    cell = checked_original_path(cell_argument)
    if not cell.is_dir():
        raise ToolError(EXIT_CELL_INVALID, "Cell root %s is not a directory" % cell)
    if is_link(cell):
        raise ToolError(EXIT_CELL_INVALID, "Cell root %s is a link or reparse point" % cell)
    manifest = cell / "comsect1.json"
    if not manifest.is_file() or is_link(manifest):
        raise ToolError(EXIT_CELL_INVALID, "%s is not a regular file; not a Cell" % manifest)
    units = []
    for directory, subdirs, files in os.walk(cell, followlinks=False):
        subdirs.sort()
        for entry in subdirs + files:
            if is_link(os.path.join(directory, entry)):
                raise ToolError(EXIT_CELL_INVALID, "link or reparse point inside the Cell: %s" % os.path.join(directory, entry))
        for entry in files:
            if entry.endswith(".c"):
                units.append(Path(directory) / entry)
    if not units:
        raise ToolError(EXIT_CELL_INVALID, "Cell %s has no C translation unit" % cell)
    units.sort(key=lambda p: p.relative_to(cell).as_posix())
    return cell, units


def prepare_output(run_id, cell):
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ToolError(EXIT_OUTPUT_INVALID, "run id must match %s" % RUN_ID_PATTERN.pattern)
    parent = REPO_ROOT
    for part in ("90_temps", "verification", "native_gate"):
        parent = parent / part
        if parent.exists():
            if not parent.is_dir() or is_link(parent):
                raise ToolError(EXIT_OUTPUT_INVALID, "%s is a link or not a directory" % parent)
    run_dir = parent / run_id
    if run_dir.exists():
        raise ToolError(EXIT_OUTPUT_INVALID, "output directory %s already exists" % run_dir)
    resolved = parent.resolve() / run_id
    if cell == resolved or resolved in cell.parents or cell in resolved.parents:
        raise ToolError(EXIT_OUTPUT_INVALID, "output directory and Cell must not contain each other")
    return run_dir


def create_output(run_dir):
    try:
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        for ancestor in (run_dir.parent, run_dir.parent.parent, run_dir.parent.parent.parent):
            if is_link(ancestor):
                raise ToolError(EXIT_OUTPUT_INVALID, "%s is a link" % ancestor)
        run_dir.mkdir()
    except OSError as error:
        raise ToolError(EXIT_OUTPUT_INVALID, "cannot create %s: %s" % (run_dir, error))


# ---------------------------------------------------------------- plan

def native_path(path):
    text = str(Path(path).resolve()).replace("\\", "/")
    if re.match(r"[A-Za-z]:/", text) is None:
        raise ToolError(EXIT_PLAN_FAILED, "path %s has no drive letter" % text)
    return "fs/%s/%s" % (text[0].upper(), text[3:])


def include_directories(config, cell):
    """Include roots in compiler order: the Cell's own, then the dependency membranes.

    A dependency membrane lies outside the Cell, inside this repository; its files
    enter the closure as external inputs and are never governed by this Cell."""
    result = []
    for rule in config["build"]["include_roots"]:
        directory = checked_original_path(cell / rule)
        if cell != directory and cell not in directory.parents or not directory.is_dir():
            raise ToolError(EXIT_PLAN_FAILED, "include root %r is not a directory inside the Cell" % rule)
        result.append(directory)
    for rule in config["build"]["external_include_roots"]:
        directory = checked_original_path(REPO_ROOT / rule) if (REPO_ROOT / rule).exists() else (REPO_ROOT / rule)
        if REPO_ROOT.resolve() not in directory.parents or not directory.is_dir() \
                or cell == directory or cell in directory.parents or directory in cell.parents:
            raise ToolError(EXIT_PLAN_FAILED,
                            "external include root %r is not a directory of this repository outside the Cell" % rule)
        if is_link(directory):
            raise ToolError(EXIT_PLAN_FAILED, "external include root %r is a link or reparse point" % rule)
        result.append(directory)
    return result


def system_directories(config):
    """External SDK include roots (read only), searched after the clang resource directory, so
    the freestanding headers resolve to the resource directory and the SDK headers to the SDK.

    A root lies outside this repository or inside a declared dependency checkout below
    10_pkg/deps/ (a Git submodule: the Foundation, an adopted header tree); no other folder of
    this repository, which holds the Cells, is a system root."""
    result = []
    repo = REPO_ROOT.resolve()
    deps = repo.joinpath(*DEPS_FOLDER)
    for root in config["build"]["system_include_roots"]:
        directory = checked_original_path(root) if Path(root).exists() else Path(root)
        if not directory.is_dir():
            raise ToolError(EXIT_PLAN_FAILED, "system include root %r is not a directory" % root)
        if (repo in directory.parents or directory == repo) and deps not in directory.parents:
            raise ToolError(EXIT_PLAN_FAILED, "system include root %r lies inside this repository outside %s/ "
                            "(only a declared dependency checkout below it is a system root)"
                            % (root, "/".join(DEPS_FOLDER)))
        if is_link(directory):
            raise ToolError(EXIT_PLAN_FAILED, "system include root %r is a link or reparse point" % root)
        result.append(directory)
    return result


def compile_flags(config, roots):
    build = config["build"]
    flags = ["--target=" + build["target"], "-std=" + build["standard"], "-ffreestanding",
             "-f%s-char" % build["plain_char"]]
    for name, value in build["definitions"].items():
        flags.append("-D%s=%s" % (name, value))
    for root in roots:
        flags += ["-I", str(root)]
    return flags


def header_closure(config, clang, resource_include, roots, entry, cwd):
    command = [clang] + compile_flags(config, roots) + ["-M", "-nostdinc", "-isystem", resource_include]
    for system_root in system_directories(config):
        command += ["-isystem", str(system_root)]
    command.append(entry)
    code, out, err = run_process("clang -M", command, config["timeouts_seconds"]["clang"], cwd)
    if code != 0:
        raise ToolError(EXIT_PLAN_FAILED, "clang -M failed for %s: %s" % (entry, err.strip()))
    tokens = re.findall(r"(?:\\ |\S)+", re.sub(r"\\\r?\n", " ", out))[1:]
    return sorted({str(checked_original_path(Path(token.replace("\\ ", " "))
                   if Path(token.replace("\\ ", " ")).is_absolute()
                   else Path(cwd) / token.replace("\\ ", " "))) for token in tokens})


def configuration_for(config, clang, resource_include, roots, cell, entry, cwd):
    build = config["build"]
    governed, external, inputs = [], [], {}
    admitted = config.get("_resource_members")
    for file in header_closure(config, clang, resource_include, roots, entry, cwd):
        path = Path(file)
        if admitted is not None and (resource_include == path or resource_include in path.parents):
            require(str(path.resolve()).casefold() in admitted,
                    "header closure selected an unadmitted resource member: %s" % path)
        if path == cell or cell in path.parents:
            governed.append({"native_path": native_path(path), "cell_path": path.relative_to(cell).as_posix()})
        else:
            location = native_path(path)
            # A short stable id: a large SDK closure would overflow the command line of
            # comsect1-native-input (32767 characters on Windows) if every id repeated its path;
            # 64 bits of the path digest keep a hosted closure inside it. Two paths of one closure
            # with one id are refused.
            identifier = "x" + sha256_text(location)[:16]
            if identifier in inputs:
                raise ToolError(EXIT_PLAN_FAILED, "external input id %s repeats for %s" % (identifier, location))
            external.append({"native_path": location, "external_input_id": identifier})
            inputs[identifier] = file
    if not governed:
        raise ToolError(EXIT_PLAN_FAILED, "closure of %s has no Cell file" % entry)
    relative = entry.relative_to(cell).as_posix()
    return {
        "configuration_id": re.sub(r"[^A-Za-z0-9_.-]", "_", relative),
        "publication_authority_id": None,
        "virtual_root": build["virtual_root"],
        "target": build["target"],
        "standard": build["standard"],
        "plain_char": build["plain_char"],
        "forced_includes": [],
        "entry": native_path(entry),
        "include_roots": [native_path(root) for root in roots] + [native_path(resource_include)]
                         + [native_path(root) for root in system_directories(config)],
        "definitions": dict(build["definitions"]),
        "preprocessing_epoch_seconds": build["preprocessing_epoch_seconds"],
        "governed_inputs": governed,
        "external_inputs": external,
        "configured_candidate_paths": [item["cell_path"] for item in governed],
    }, inputs


# ---------------------------------------------------------------- lanes

def report_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Gate report key: %s" % key)
        result[key] = value
    return result


def report_constant(value):
    raise ValueError("non-finite Gate report constant: %s" % value)


def assessment_summary(assessment):
    """The report's `assessment` member, checked: finding counts by reason, missing capabilities, unassessed sites."""
    if not (isinstance(assessment, dict) and set(assessment) == ASSESSMENT_KEYS
            and isinstance(assessment["reasons"], dict)
            and all(isinstance(k, str) and type(v) is int and v > 0 for k, v in assessment["reasons"].items())
            and isinstance(assessment["missing_capabilities"], list)
            and all(isinstance(item, str) for item in assessment["missing_capabilities"])
            and type(assessment["unassessed_site_count"]) is int and assessment["unassessed_site_count"] >= 0):
        raise ToolError(EXIT_GATE_REFUSED, "Gate report has an unexpected assessment member")
    return {"reasons": dict(sorted(assessment["reasons"].items())),
            "missing_capabilities": sorted(assessment["missing_capabilities"]),
            "unassessed_site_count": assessment["unassessed_site_count"]}


def read_report(report_path, gate_text, gate_code):
    """The lane result of one Gate report: unique keys, finite numbers, known verdict and handoff, the
    finding count of the list, the outcome summary with every count, the assessment member, a
    concluding Gate exit (0 or 1), and the handoff and exit of the verdict. Only FAIL blocks, and
    exactly when a violation is established; an incomplete assessment never blocks."""
    if not report_path.is_file():
        raise ToolError(EXIT_GATE_REFUSED, "Gate wrote no report (exit %d): %s" % (gate_code, gate_text.strip()[:400]))
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"),
                            object_pairs_hook=report_object, parse_constant=report_constant)
        verdict, count, handoff, findings = report["verdict"], report["finding_count"], report["handoff"], report["findings"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ToolError(EXIT_GATE_REFUSED, "Gate report unusable: %s" % error)
    if not (isinstance(verdict, str) and isinstance(handoff, str) and isinstance(findings, list)
            and type(count) is int):
        raise ToolError(EXIT_GATE_REFUSED, "Gate report has unexpected member types")
    if count != len(findings):
        raise ToolError(EXIT_GATE_REFUSED, "Gate finding count differs from the findings list")
    outcome = report.get("outcome_summary")
    if not (verdict in VERDICTS and handoff in ("CONTINUE", "CONTINUE_WITH_RISK", "BLOCK")
            and count >= 0 and all(isinstance(f, dict) for f in findings)
            and isinstance(outcome, dict) and type(outcome.get("assessment_blocked")) is bool
            and all(type(outcome.get(k)) is int and 0 <= outcome[k] <= count for k in COUNT_NAMES)
            and type(gate_code) is int and gate_code in (0, 1)):
        raise ToolError(EXIT_GATE_REFUSED, "Gate report has unexpected member types")
    assessment = assessment_summary(report.get("assessment"))
    codes = {}
    for finding in findings:
        code = finding.get("code")
        codes[str(code)] = codes.get(str(code), 0) + 1
    if gate_code != VERDICT_GATE_EXIT[verdict]:
        raise ToolError(EXIT_GATE_REFUSED, "inconsistent Gate result: verdict %s with exit %d" % (verdict, gate_code))
    if handoff != VERDICT_HANDOFF[verdict]:
        raise ToolError(EXIT_GATE_REFUSED, "inconsistent Gate result: verdict %s with handoff %s" % (verdict, handoff))
    if (verdict == "FAIL") != (outcome["violation_count"] > 0):
        raise ToolError(EXIT_GATE_REFUSED, "inconsistent Gate result: verdict %s with %d violations"
                        % (verdict, outcome["violation_count"]))
    if verdict == "PASS" and count != 0:
        raise ToolError(EXIT_GATE_REFUSED, "inconsistent Gate result: verdict PASS with %d findings" % count)
    return {"verdict": verdict, "finding_count": count, "handoff": handoff, "gate_exit": gate_code,
            "finding_codes": dict(sorted(codes.items())), "outcome_summary": outcome,
            "assessment": assessment, "report_sha256": sha256_file(report_path)}


def verdict_exit(verdict):
    """The tool code of a lane verdict: only FAIL blocks; WARN, REQ and INDETERMINATE continue."""
    if verdict == "FAIL":
        return EXIT_FAIL
    if verdict in VERDICTS:
        return EXIT_CONTINUE
    raise ToolError(EXIT_GATE_REFUSED, "unknown Gate verdict %r" % verdict)


def gate_command(gate, cell, report, review):
    return [gate, "check", "--root", cell, "--cell", "--out", report, "--review-out", review, "--quiet"]


def run_plain_lane(config, paths, cell, lane_dir):
    lane_dir.mkdir()
    command = gate_command(paths["gate"], cell, lane_dir / "report.json", lane_dir / "review.json")
    code, out, err = run_process("gate (plain)", command, config["timeouts_seconds"]["gate"], lane_dir, lane_dir / "gate.txt")
    return read_report(lane_dir / "report.json", out + err, code)


def input_owner_selection(config, inputs, cell):
    """Select actual external source Cells and explicitly configured SDKs.

    Only original ancestry identifies a Cell. The owner CLI validates controls,
    public entries, support and inventory; this driver does not create receipts.
    Selections are per translation unit, so unused owners are never submitted.
    """
    publications = []
    for record in config.get("publications", []):
        root = Path(expand(record["canonical_root"], "publication.canonical_root"))
        if not root.is_absolute() or ".." in root.parts or not root.is_dir():
            raise ToolError(EXIT_PLAN_FAILED, "publication root must be an existing absolute directory")
        root = checked_original_path(root)
        if any(root == other or root in other.parents or other in root.parents
               for other, _ in publications):
            raise ToolError(EXIT_PLAN_FAILED, "overlapping publication roots are ambiguous")
        publications.append((root, dict(record, canonical_root=root.as_posix())))
    selected, sources, controls = {}, set(), {}
    for value in inputs.values():
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts or not path.is_file():
            raise ToolError(EXIT_PLAN_FAILED, "external input must be an existing absolute file")
        path = checked_original_path(path)
        matches = [(root, record) for root, record in publications if root in path.parents]
        if matches:
            root, record = matches[0]
            selected[root] = record
            continue
        owners = []
        for directory in path.parents:
            if directory not in controls:
                control = directory / "comsect1.json"
                present = os.path.lexists(control)
                if present and (is_link(control) or not control.is_file()):
                    raise ToolError(EXIT_PLAN_FAILED, "invalid original Cell control: %s" % control)
                controls[directory] = present
            if controls[directory]:
                owners.append(directory)
        if len(owners) > 1:
            raise ToolError(EXIT_PLAN_FAILED, "nested original Cell controls: %s" % path)
        if owners:
            if owners[0] == cell:
                raise ToolError(EXIT_PLAN_FAILED, "governed Cell input cannot be external")
            sources.add(owners[0])
    return sorted(sources, key=str), [selected[root] for root in sorted(selected, key=str)]


def run_native_lane(config, paths, cell, units, clang, resource_include, lane_dir):
    lane_dir.mkdir()
    roots = include_directories(config, cell)
    configs, inputs_of, receipts = [], {}, []
    for entry in units:
        item, inputs = configuration_for(config, clang, resource_include, roots, cell, entry, lane_dir)
        configs.append(item)
        inputs_of[item["configuration_id"]] = inputs
    ids = [item["configuration_id"] for item in configs]
    if len(set(ids)) != len(ids):
        raise ToolError(EXIT_PLAN_FAILED, "configuration ids are not unique")
    plan_path = lane_dir / "plan.json"
    plan = {"schema": "comsect1.gate.native-analysis-plan",
            "targets": [{"cell_root": str(cell).replace("\\", "/"), "configurations": configs}]}
    timeout = config["timeouts_seconds"]["native_input"]
    for item in configs:
        write_json(plan_path, plan)
        cid = item["configuration_id"]
        receipt = lane_dir / ("receipt-%s.json" % cid)
        command = [paths["native_input"], "--plan", plan_path, "--configuration", cid,
                   "--snapshot", lane_dir / ("snapshot-" + cid), "--receipt", receipt]
        for key, path in inputs_of[cid].items():
            command += ["--input", "%s=%s" % (key, path)]
        sources, publications = input_owner_selection(config, inputs_of[cid], cell)             if "publications" in config else ([], [])
        for source in sources:
            command += ["--source-cell", source]
        for index, publication in enumerate(publications):
            descriptor = lane_dir / ("publication-%s-%d.json" % (cid, index))
            write_json(descriptor, publication)
            command += ["--publication", descriptor]
        code, out, err = run_process("native-input " + cid, command, timeout, lane_dir,
                                     lane_dir / ("native-input-%s.txt" % cid))
        authority = out.strip()
        if code != 0 or AUTHORITY_PATTERN.fullmatch(authority) is None:
            raise ToolError(EXIT_NATIVE_INPUT_FAILED, "native-input failed for %s (exit %d): %s" % (cid, code, err.strip()[:400]))
        item["publication_authority_id"] = authority
        receipts.append(receipt)
    write_json(plan_path, plan)
    command = [paths["gate"], "check", "--root", cell, "--cell", "--native-analysis", plan_path,
               "--out", lane_dir / "report.json", "--review-out", lane_dir / "review.json", "--quiet"]
    for receipt in receipts:
        command += ["--native-publication-receipt", receipt]
    code, out, err = run_process("gate (native)", command, config["timeouts_seconds"]["gate"], lane_dir, lane_dir / "gate.txt")
    result = read_report(lane_dir / "report.json", out + err, code)
    result["plan_sha256"] = sha256_file(plan_path)
    result["translation_units"] = len(configs)
    return result


def lane_line(name, result):
    if "error" in result:
        return "%s: ERROR class=%s code=%d %s" % (name, EXIT_NAMES[result["exit"]], result["exit"], result["error"])
    codes = ",".join("%s:%d" % item for item in result["finding_codes"].items()) or "-"
    assessment = result["assessment"]
    reasons = ",".join("%s:%d" % item for item in assessment["reasons"].items()) or "-"
    missing = ",".join(assessment["missing_capabilities"]) or "-"
    return ("%s: verdict=%s findings=%d handoff=%s gate_exit=%d violations=%d codes=%s\n"
            "%s: assessment unassessed_sites=%d reasons=%s missing_capabilities=%s") % (
        name, result["verdict"], result["finding_count"], result["handoff"], result["gate_exit"],
        result["outcome_summary"]["violation_count"], codes,
        name, assessment["unassessed_site_count"], reasons, missing)


# ---------------------------------------------------------------- main

def parse_arguments(argv):
    parser = argparse.ArgumentParser(prog="native_gate.py", description="Run the native C Gate lane on one Cell.")
    parser.add_argument("--cell", required=True, help="Cell root (read only; may be outside this repository)")
    parser.add_argument("--config", required=True, help="lane configuration JSON")
    parser.add_argument("--lane", choices=("native", "plain", "both"), default="native")
    parser.add_argument("--run-id", default=None, help="run id (default: UTC timestamp)")
    parser.add_argument("--accept-tool-drift", action="store_true",
                        help="run although the maintainer tools differ from their template manifest (recorded)")
    return parser.parse_args(argv)


def maintainer_tools(accept_drift):
    try:
        return maint_config.maintainer_tools(str(REPO_ROOT), accept_drift)
    except maint_config.ConfigError as error:
        raise ToolError(EXIT_CONFIG, str(error))


def run(arguments):
    config = load_config(arguments.config)
    tools = maintainer_tools(arguments.accept_tool_drift)
    cell, units = check_cell(arguments.cell)
    run_id = arguments.run_id or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = prepare_output(run_id, cell)
    _, paths, set_id = resolve_gate_set(config)
    hashes, differs = tool_hashes(config, paths)
    with ExitStack() as locks:
        clang, resource_include = (None, None)
        if arguments.lane in ("native", "both"):
            clang, resource_include = find_clang(config, hashes["libclang"], locks)
        create_output(run_dir)
        if clang is not None:
            write_json(run_dir / "toolchain_provenance.json", config["_native_toolchain_manifest"])
        lanes = ("native", "plain") if arguments.lane == "both" else (arguments.lane,)
        results, code = {}, EXIT_CONTINUE
        for lane in lanes:
            try:
                if lane == "native":
                    results[lane] = run_native_lane(config, paths, cell, units, clang, resource_include, run_dir / "native")
                else:
                    results[lane] = run_plain_lane(config, paths, cell, run_dir / "plain")
                lane_code = verdict_exit(results[lane]["verdict"])
            except ToolError as error:
                results[lane] = {"error": error.message, "exit": error.code}
                lane_code = error.code
            print(lane_line(lane, results[lane]))
            code = max(code, lane_code)
        if clang is not None:
            config["_toolchain_inventory_check"]()
        summary = {
            "run_id": run_id, "lane": arguments.lane, "exit": code, "exit_class": EXIT_NAMES[code],
            "release_set_id": set_id, "tool_sha256": hashes,
            "tool_sha256_recorded": dict(config["expected_sha256"]), "tool_sha256_differs": differs,
            "config_sha256": config["_sha256"], "cell_manifest_sha256": sha256_file(cell / "comsect1.json"),
            "standard": config["build"]["standard"], "target": config["build"]["target"],
        }
        if clang is not None:
            summary["native_toolchain"] = config["_native_toolchain"]
        summary["maintainer_tools"] = tools
        summary["lanes"] = results
        write_json(run_dir / "summary.json", summary)
        print("run_dir=%s exit=%d %s" % (run_dir.relative_to(REPO_ROOT).as_posix(), code, EXIT_NAMES[code]))
        return code


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    try:
        arguments = parse_arguments(argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code not in (0, None) else 0
    try:
        return run(arguments)
    except ToolError as error:
        print("native_gate: %s: %s" % (EXIT_NAMES[error.code], error.message), file=sys.stderr)
        return error.code
    except Exception as error:  # fail closed on anything unexpected
        import traceback
        traceback.print_exc()
        print("native_gate: INTERNAL: %s" % error, file=sys.stderr)
        return EXIT_INTERNAL
