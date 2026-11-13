# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Support for the comsect1_maint tests: the package, task-owned run folders and fixture products.

The package is loaded by file path like the entry shims do. Every test module
writes only below `90_temps/verification/comsect1_maint/<run-id>/` of the
checkout that holds the package and moves its run through `99_trash`
(`process.quarantine`) when it ends. Python standard library only.
"""

import importlib
import importlib.util
import json
import os
import pathlib
import sys

TEST_ROOT = pathlib.Path(__file__).resolve().parent
PACKAGE_ROOT = TEST_ROOT.parent
TOOLS_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = TOOLS_ROOT.parent.parent
FIXTURES = TEST_ROOT / "fixtures"
SHIMS = {"native_gate.py": "native_gate", "cell_registry.py": "registry", "cross_build.py": "cross_build",
         "build.py": "host_build", "gate.py": "gate"}


def package_module(name):
    """`comsect1_maint.<name>` of this checkout."""
    package = sys.modules.get("comsect1_maint")
    if package is None:
        spec = importlib.util.spec_from_file_location("comsect1_maint", PACKAGE_ROOT / "__init__.py",
                                                      submodule_search_locations=[str(PACKAGE_ROOT)])
        package = importlib.util.module_from_spec(spec)
        sys.modules["comsect1_maint"] = package
        spec.loader.exec_module(package)
    return importlib.import_module("comsect1_maint." + name)


def load_file(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


process = package_module("process")
config = package_module("config")
c_standard = package_module("c_standard")


class Run:
    """One task-owned run folder of a test module, removed through 99_trash by `close()`."""

    def __init__(self, purpose):
        self.run_id = process.new_run_id(purpose)
        self.path = REPO_ROOT / "90_temps" / "verification" / "comsect1_maint" / self.run_id
        self.path.mkdir(parents=True)

    def folder(self, name):
        path = self.path / name
        path.mkdir(parents=True)
        return path

    def close(self):
        if self.path.exists():
            process.quarantine(str(self.path), str(self.path), "comsect1_maint_" + self.run_id, str(REPO_ROOT))


def fixture_config(name):
    with open(FIXTURES / "config" / (name + ".json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


def write(path, text):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def c_cell(product, path, prefix, header=True, contract="pvo_%s.h", manifest=None, sources=("smo_%s.c",)):
    """Write a minimal C Cell below `<product>/10_pkg/c/<path>`."""
    name = path.split("/")[-1]
    root = pathlib.Path(product) / "10_pkg" / "c" / path
    write(root / "comsect1.json", json.dumps(manifest or {"cell": name}) + "\n")
    for source in sources:
        write(root / "core" / (source % ("%s_%s" % (prefix, name))), "int x_%s_%s;\n" % (name, source[:3]))
    write(root / "contract" / (contract % ("%s_%s" % (prefix, name))), "/* contract */\n")
    if header:
        write(root / "membrane" / ("xpt_%s_%s.h" % (prefix, name)),
              "#ifndef X_%s\n#define X_%s\nint %s_%s_open(void);\n#endif\n" % (name.upper(), name.upper(), prefix, name))
    return root


def c_entry(path, targets=("windows",), **extra):
    entry = {"path": path, "standard": c_standard.C_STANDARD, "definitions": {}, "include_roots": ["."], "depends_on": [],
             "freestanding": True, "targets": {}}
    for target in targets:
        entry["targets"][target] = {"gate": {"target": "x86_64-pc-windows-msvc", "plain_char": "signed"}}
        if target == "arm":
            entry["targets"][target] = {"gate": {"target": "thumbv7em-none-eabihf", "plain_char": "unsigned"},
                                        "cross_cores": ["cortex-m4"]}
    entry.update(extra)
    return entry


def product(root, shape, cells, cores=None, changes=None):
    """Write a fixture product: maint_config.json of `shape` (with `changes` applied to its registry) and
    cells.json; returns its Settings."""
    raw = fixture_config(shape)
    if changes:
        raw["registry"].update(changes)
    tools = pathlib.Path(root) / "20_ops" / "tools"
    write(tools / "maint_config.json", json.dumps(raw, indent=2) + "\n")
    if raw["language"] == "c":
        registry = {"cores": cores if cores is not None else {"cortex-m4": ["-mthumb"]}, "cells": cells}
    else:
        registry = {"cells": cells}
    write(tools / "cells.json", json.dumps(registry, indent=2) + "\n")
    return config.load(str(tools / "maint_config.json"))
