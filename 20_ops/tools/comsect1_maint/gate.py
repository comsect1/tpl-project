# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Run the native Gate lane on one registered Cell for one target.

Python standard library only. A thin wrapper of `native_gate.py`: the lane
configuration is the product's `native_gate_config.json` (`maint_config.json`
`native_gate.config`) with its `build` section replaced by the registry settings
of the Cell and target (triple, plain char, standard, definitions, include
roots, dependency membranes and passive vocabulary as external include roots,
hosted SDK, external header and standard roots as system include roots) and,
when the product defines the `gate_publications` hook, its publications. The
derived configuration lives in a task-owned scratch run that is moved through
`99_trash` afterwards (exact run, inventory and identity checked).

    python -I -B 20_ops/tools/gate.py --cell <name> --target <target> [--run-id ID]
                                      [--lane native|plain|both] [--accept-tool-drift]

Output: `90_temps/verification/native_gate/<run-id>/` (see native_gate.py). The
exit code is the one of native_gate.py (0 CONTINUE: no lane FAIL); 2 for a usage or
registry error.
"""

import argparse
import json
import os
import subprocess
import sys

from . import config as maint_config
from . import process
from . import registry

TOOLS_ROOT = process.TOOLS_ROOT
REPO_ROOT = registry.REPO_ROOT
_SETTINGS = maint_config.current() if maint_config.present() else None
BASE_CONFIG = _SETTINGS.path_of(_SETTINGS.native_gate["config"]) \
    if _SETTINGS is not None and _SETTINGS.native_gate else None
NATIVE_GATE = os.path.join(TOOLS_ROOT, "native_gate.py")


def new_run_id(cell, target):
    return process.new_run_id("gate-%s-%s" % (cell, target), precise=False)


def derived_config(cell, target):
    with open(BASE_CONFIG, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    gate = cell.targets[target]["gate"]
    # An adopted external header tree (external_headers, a firmware kernel) is read through system
    # include roots, like the Windows SDK, for the Gate's core; its configuration folder in this
    # repository is an external include root. A Cell that adopts the fnd-sdk-standard reads
    # comsect1_provider.h of the Foundation checkout through a system include root as well, the same
    # -isystem search the host and cross builds use. Either checkout is a declared dependency: outside
    # this repository (an override) or a submodule below 10_pkg/deps/, which native_gate admits as the
    # only in-repository system root; its files are external inputs, never governed by the Cell.
    external, configs = [], []
    if cell.external_headers is not None:
        external, configs = cell.external_roots(cell.external_headers["gate_core"])
    config["build"] = dict(config["build"],
                           target=gate["target"], plain_char=gate["plain_char"],
                           standard=cell.standard, definitions=dict(cell.definitions),
                           include_roots=list(cell.include_roots),
                           external_include_roots=cell.external_include_roots()
                           + [os.path.relpath(path, REPO_ROOT).replace("\\", "/") for path in configs]
                           + [os.path.relpath(path, REPO_ROOT).replace("\\", "/") for path in cell.passive_include_roots()],
                           system_include_roots=list(cell.hosted_include_roots) + external
                           + [path.replace("\\", "/") for path in cell.standard_roots()])
    hook = cell.settings.hook("gate_publications", required=False)
    if hook is not None:
        publications = hook(cell, config)
        if not isinstance(publications, list):
            raise registry.RegistryError("the gate_publications hook returned no list")
        if publications:
            config["publications"] = publications
    return config


def quarantine(scratch_dir, run_id):
    """Move the exact scratch run through 99_trash and delete only that child (process.quarantine)."""
    expected = os.path.join(REPO_ROOT, "90_temps", "scratch", "gate", run_id)
    process.quarantine(scratch_dir, expected, "gate_cleanup_" + run_id, REPO_ROOT,
                       announce=lambda line: (print("gate " + line), sys.stdout.flush()))


def main(argv):
    parser = argparse.ArgumentParser(description="Run the native Gate on one registered Cell.")
    parser.add_argument("--cell", required=True, help="Cell name of the registry")
    parser.add_argument("--target", required=True, choices=registry.TARGET_NAMES)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--lane", choices=("native", "plain", "both"), default="native")
    parser.add_argument("--accept-tool-drift", action="store_true")
    args = parser.parse_args(argv)
    try:
        if BASE_CONFIG is None:
            raise registry.RegistryError("maint_config.json configures no native Gate")
        cell = registry.get_cell(args.cell)
        if args.target not in cell.targets:
            raise registry.RegistryError("Cell %s does not support target %s" % (cell.name, args.target))
        run_id = args.run_id or new_run_id(cell.name, args.target)
        if not registry.RUN_ID_PATTERN.fullmatch(run_id):
            raise registry.RegistryError("run id %r does not match %s" % (run_id, registry.RUN_ID_PATTERN.pattern))
        config = derived_config(cell, args.target)
    except (registry.RegistryError, maint_config.ConfigError, OSError, ValueError) as error:
        sys.stderr.write("gate: %s\n" % error)
        return 2

    scratch = process.checked_path(os.path.join(REPO_ROOT, "90_temps", "scratch", "gate", run_id), REPO_ROOT)
    os.makedirs(scratch)
    process.checked_path(scratch, REPO_ROOT)
    config_path = os.path.join(scratch, "config.json")
    with open(config_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")
    command = [sys.executable, "-I", "-B", NATIVE_GATE, "--cell", cell.root, "--config", config_path,
               "--lane", args.lane, "--run-id", run_id]
    if args.accept_tool_drift:
        command.append("--accept-tool-drift")
    print("gate: cell=%s target=%s triple=%s plain_char=%s" % (
        cell.name, args.target, config["build"]["target"], config["build"]["plain_char"]))
    sys.stdout.flush()
    try:
        code = subprocess.run(command, stdin=subprocess.DEVNULL, cwd=REPO_ROOT,
                              creationflags=process.window_flags()).returncode
    finally:
        quarantine(scratch, run_id)
    return code
