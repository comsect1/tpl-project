# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Cross-build the registered freestanding Cells for ARM Cortex-M, objects and archives only.

Python standard library only. For each Cell of the registry that is freestanding
and supports the arm target, every `.c` file is compiled to an object (`-c`) for
each of the Cell's cross cores, and optionally collected into a static archive
(`arm-none-eabi-ar`). Nothing is linked: no executable, image or map is ever
produced. All output goes to `90_temps/build/cross/<run-id>/`. The toolchain
folder and the common flags come from `maint_config.json` `cross_build`.

    python -I -B 20_ops/tools/cross_build.py [--cell NAME] [--run-id ID] [--archive] [--accept-tool-drift]
                                             [--jobs N] [--no-object-cache]

A Cell that depends on another (`depends_on`) also compiles the sources of that
Cell, with that Cell's own flags and include roots, so the undefined-symbol
check covers the library the consumer links; `total` counts the Cell's own
sources and `dependency_total` the dependencies. Per Cell and per core the tool
records `.text`, `.data` and `.bss` (`arm-none-eabi-size`, Berkeley format,
summed over the objects) and every undefined symbol of every object
(`arm-none-eabi-nm -u`). The `external` list is the undefined set minus the
symbols the Cell's own objects define. `summary.json` holds the record and a
short table is printed. A Cell with a `budget` fails a core whose total exceeds
it; a Cell that adopts the fnd-sdk-standard reads `comsect1_provider.h` of the
Foundation checkout as a system header (its observed HEAD and dirty state are
recorded as `fnd_sdk_standard`, never required). Per core, the external-linkage
symbols every compiled unit defines (the Cells and their dependencies, each unit
once) must be unique (registry.require_unique_symbols) for the image without a
platform Cell and for each platform's image. Any failure exits non-zero with a
reason on stderr (fail closed).

A dependency Cell (registry key `dependency`: a Cell of a declared submodule
below `10_pkg/deps/`) is cross-built like every freestanding arm Cell, from the
checkout and with this product's flags, into this run directory; its archive
carries the dependency's own name (`lib<identity>.a`) and its result keeps the
checkout under `dependency`. Nothing is written below the checkout.

The standard (`c_standard`, ISO/IEC 9899:2018) is verified by value before any
unit compiles: once per distinct core, Cell and common flag set, a one-line
probe is preprocessed and the run fails closed unless `__STDC_VERSION__`
expands to the value of the standard; `summary.json` keeps every probe under
`c_standard`.

Build speed (`.instructions/build-optimization.md`): every (Cell, core, unit)
compile with its `size` and `nm` listings runs on `--jobs` worker threads
(default: the logical processors) through the object cache (`object_cache`,
development cache `90_temps/development/build/c_objects/`), so a dependency
unit that several Cells compile is compiled once per core and run; objects,
archive member order and the record keep the sequential order. A cached and an
uncached run give byte-identical objects, archives and records.
`--no-object-cache` reuses objects inside the run only (a release candidate).
"""

import argparse
import json
import os
import subprocess
import sys

from . import c_standard
from . import config as maint_config
from . import object_cache
from . import process
from . import registry

TOOL_TIMEOUT_SECONDS = 300
REPO_ROOT = registry.REPO_ROOT
BUILD_ROOT = os.path.join(REPO_ROOT, "90_temps", "build", "cross")
RUN_ID_PATTERN = registry.RUN_ID_PATTERN

_SETTINGS = maint_config.current() if maint_config.present() else None
_CROSS = _SETTINGS.cross_build if _SETTINGS is not None and _SETTINGS.language == "c" else None
# The product's Arm toolchain (None for a product without a cross-build, and in the template).
TOOLCHAIN_BIN_DIR = os.path.normpath(_CROSS["toolchain_bin_dir"]) if _CROSS else None
GCC_PATH = os.path.join(TOOLCHAIN_BIN_DIR, "arm-none-eabi-gcc.exe") if _CROSS else None
AR_PATH = os.path.join(TOOLCHAIN_BIN_DIR, "arm-none-eabi-ar.exe") if _CROSS else None
SIZE_PATH = os.path.join(TOOLCHAIN_BIN_DIR, "arm-none-eabi-size.exe") if _CROSS else None
NM_PATH = os.path.join(TOOLCHAIN_BIN_DIR, "arm-none-eabi-nm.exe") if _CROSS else None
# Flags common to every Cell and core; the standard follows the Cell and -funsigned-char the arm Gate setting.
COMMON_FLAGS = list(_CROSS["common_flags"]) if _CROSS else None


class CrossBuildError(Exception):
    """A step failed; the message is the reason. `details` (a dict) holds what the failure summary keeps
    beside the message."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def new_run_id():
    return process.new_run_id("cross", precise=False)


def run_tool(args, env, what):
    try:
        done = subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8",
                              stdin=subprocess.DEVNULL, timeout=TOOL_TIMEOUT_SECONDS,
                              creationflags=process.window_flags())
    except subprocess.TimeoutExpired:
        raise CrossBuildError("%s timed out after %d s" % (what, TOOL_TIMEOUT_SECONDS))
    except OSError as error:
        raise CrossBuildError("%s could not start: %s" % (what, error))
    if done.returncode != 0:
        raise CrossBuildError("%s failed (exit %d):\n%s%s" % (what, done.returncode, done.stdout, done.stderr))
    return done


def parse_size(text):
    """Berkeley size output for one object: (text, data, bss)."""
    rows = [line.split() for line in text.strip().splitlines()]
    if len(rows) != 2 or rows[0][:3] != ["text", "data", "bss"]:
        raise CrossBuildError("unexpected size output:\n%s" % text)
    return int(rows[1][0]), int(rows[1][1]), int(rows[1][2])


def parse_names(text):
    return sorted({line.split()[-1] for line in text.splitlines() if line.strip()})


def selected_cells(cell_name, settings=None):
    """The Cells to cross-build: the named one, or every freestanding arm Cell."""
    reg = registry.load_registry(settings)
    if cell_name is not None:
        if cell_name not in reg["cells"]:
            raise CrossBuildError("unknown Cell %r (registry holds: %s)" % (cell_name, ", ".join(sorted(reg["cells"]))))
        cells = [reg["cells"][cell_name]]
    else:
        cells = [c for _, c in sorted(reg["cells"].items())]
    cells = [c for c in cells if c.freestanding and "arm" in c.targets]
    if not cells:
        raise CrossBuildError("no freestanding Cell with an arm target selected")
    return reg["cores"], cells


def _open_cache(mode):
    if isinstance(mode, object_cache.ObjectCache):
        return mode
    if mode == "run":
        return object_cache.ObjectCache(None)
    if mode == "development":
        try:
            return object_cache.ObjectCache(object_cache.development_root(REPO_ROOT), REPO_ROOT)
        except (object_cache.CacheError, OSError) as error:
            raise CrossBuildError("object cache refused: %s" % error)
    raise CrossBuildError("unknown object cache mode %r" % (mode,))


def build(run_id, make_archive, cell_name=None, accept_tool_drift=False, object_cache_mode="development", jobs=None):
    """Cross-build the selected Cells into a new run directory; returns (run directory, record).
    `object_cache_mode` is `development`, `run` or an `object_cache.ObjectCache`; `jobs` bounds the
    workers (default: processors)."""
    jobs = process.default_jobs() if jobs is None else jobs
    if not isinstance(jobs, int) or jobs < 1:
        raise CrossBuildError("jobs must be a positive integer, found %r" % (jobs,))
    if _CROSS is None:
        raise CrossBuildError("maint_config.json configures no cross_build")
    try:
        tools = maint_config.maintainer_tools(REPO_ROOT, accept_tool_drift, _SETTINGS.language)
    except maint_config.ConfigError as error:
        raise CrossBuildError(str(error))
    cores, cells = selected_cells(cell_name)
    for tool in (GCC_PATH, AR_PATH, SIZE_PATH, NM_PATH):
        if not os.path.isfile(tool):
            raise CrossBuildError("toolchain file absent: %s" % tool)
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise CrossBuildError("run id %r does not match %s" % (run_id, RUN_ID_PATTERN.pattern))
    for part in (os.path.join(REPO_ROOT, "90_temps"), BUILD_ROOT):
        if process.is_link(part):
            raise CrossBuildError("link in output path: %s" % part)
    run_dir = os.path.join(BUILD_ROOT, run_id)
    if os.path.exists(run_dir):
        raise CrossBuildError("run directory exists: %s" % run_dir)
    tmp_dir = os.path.join(run_dir, "tmp")
    os.makedirs(tmp_dir)
    try:
        return _build_run(run_id, make_archive, tools, cores, cells, run_dir, tmp_dir, object_cache_mode, jobs)
    except (CrossBuildError, registry.RegistryError) as error:
        _write_failure_summary(run_dir, run_id, error, tools)
        raise


def _write_failure_summary(run_dir, run_id, error, tools):
    """summary.json of a failed run: the reason, its details (resolved paths, recorded identities) and the
    Foundation identity when it was resolved. Writing it never hides the original failure."""
    record = {"run_id": run_id, "result": "failed", "error": str(error),
              "details": getattr(error, "details", {}), "maintainer_tools": tools}
    try:
        record["fnd_sdk_standard"] = registry.standard_record()
    except Exception as note:  # unreadable metadata is a note in the record, never a second failure
        record["fnd_sdk_standard"] = {"note": str(note)}
    try:
        with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, indent=2, default=str)
            handle.write("\n")
    except (OSError, TypeError, ValueError) as note:
        print("cross_build: note: the failure summary could not be written: %s" % note, file=sys.stderr)


def _verify_standard(flag_sets, env, tmp_dir):
    """Verify the standard of every distinct flag set of the run by value: preprocess the one-line probe
    and refuse unless __STDC_VERSION__ expands to the standard's value. `flag_sets` holds (label, flags)
    in compile order; returns the `c_standard` member of summary.json (c_standard.run_record)."""
    probe_path = os.path.join(tmp_dir, c_standard.PROBE_FILE_NAME)
    with open(probe_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(c_standard.PROBE_SOURCE)
    probes = {}
    for label, flags in flag_sets:
        command = [GCC_PATH] + c_standard.PREPROCESS_FLAGS + list(flags) + [probe_path]
        if tuple(command) in probes:
            continue
        record = c_standard.verification(command, run_tool(command, env, "C standard probe of %s" % label).stdout)
        if not record["verified"]:
            raise CrossBuildError(c_standard.mismatch(label, record), {"c_standard": c_standard.run_record([record])})
        probes[tuple(command)] = record
    return c_standard.run_record(list(probes.values()))


def _build_run(run_id, make_archive, tools, cores, cells, run_dir, tmp_dir, object_cache_mode, jobs):
    env = dict(os.environ)
    env["PATH"] = TOOLCHAIN_BIN_DIR + os.pathsep + env.get("PATH", "")
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = tmp_dir

    version = run_tool([GCC_PATH, "--version"], env, "gcc --version").stdout.splitlines()[0]
    cache = _open_cache(object_cache_mode)
    cache_before = cache.statistics(REPO_ROOT)
    try:
        identity = object_cache.toolchain_identity(GCC_PATH, env)
    except (object_cache.CacheError, OSError, subprocess.SubprocessError) as error:
        raise CrossBuildError("the identity of %s is unavailable: %s" % (GCC_PATH, error))

    def tool_text(command, object_path, what):
        return cache.tool_output(command, object_path, lambda argv: run_tool(argv, env, what).stdout)

    def compile_unit(job):
        """Compile one unit for one core and list it: (sizes, undefined names, defined text)."""
        key, core, command, source_path, object_path = job
        cache.compile(command, object_path, source_path, env,
                      lambda argv: run_tool(argv, env, "compile %s (%s)" % (key, core)), tmp_dir, identity, core)
        sizes = parse_size(tool_text([SIZE_PATH, object_path], object_path, "size " + key))
        undefined = parse_names(tool_text([NM_PATH, "-u", object_path], object_path, "nm -u " + key))
        defined_text = tool_text([NM_PATH, "--defined-only", object_path], object_path, "nm defined " + key)
        return sizes, undefined, defined_text

    # Every compile of the run, in the sequential order of the record; the pool runs them and the
    # results are taken back in this order.
    plan = []
    compile_jobs = []
    record = {"compiler": GCC_PATH, "compiler_version": version, "common_flags": COMMON_FLAGS,
              "run_id": run_id, "result": "passed", "results": [], "unique_external_symbols": {}}
    external_by_core = {}  # core -> {unit label: external symbols}, each unit once
    standard_flag_sets = []  # (label, the flags that can select a standard), in compile order

    for cell in cells:
        cell_name = cell.name
        # The Cell's own sources and, compiled with their own Cell's flags, the sources of the
        # Cells it depends on (the library the consumer links). Only the Cell's own sources
        # count in `total`; the dependencies are reported apart in `dependencies`.
        units = [(dependency, False) for dependency in cell.dependency_closure()] + [(cell, True)]
        for core in cell.cross_cores():
            core_flags = cores[core]
            out_dir = os.path.join(run_dir, cell_name, core)
            os.makedirs(out_dir)
            unit_jobs = []
            for unit_cell, own in units:
                unit_flags = ["-std=%s" % unit_cell.standard]
                if unit_cell.targets["arm"]["gate"]["plain_char"] == "unsigned":
                    unit_flags.append("-funsigned-char")
                standard_flag_sets.append(("Cell %s on %s" % (unit_cell.name, core),
                                           core_flags + unit_flags + COMMON_FLAGS + unit_cell.definition_flags()))
                includes = []
                for root in unit_cell.include_roots:
                    includes.append("-I" + os.path.normpath(os.path.join(unit_cell.root, root)))
                for membrane in unit_cell.dependency_membranes():
                    includes.append("-I" + membrane)
                # An adopted external header tree (external_headers, a firmware kernel): its
                # configuration folder first, then the checkout's roots for this core, as
                # system headers (read only, outside the repository, not held to the warning bar).
                external, configs = unit_cell.external_roots(core)
                includes += ["-I" + path for path in configs] + ["-isystem" + path for path in external]
                # Declared passive vocabulary and the hosted vocabulary (comsect1_provider.h of the
                # Foundation checkout, the selected SDK's membranes) as system headers.
                includes += ["-isystem" + path for path in (unit_cell.passive_include_roots() + unit_cell.standard_roots())]
                for source in unit_cell.sources:
                    key = source if own else "%s:%s" % (unit_cell.name, source)
                    object_path = os.path.join(out_dir, registry.object_name(key))
                    source_path = os.path.join(unit_cell.root, *source.split("/"))
                    command = ([GCC_PATH, "-c"] + core_flags + unit_flags + COMMON_FLAGS
                               + unit_cell.definition_flags() + includes + [source_path, "-o", object_path])
                    unit_jobs.append((unit_cell, source, key, object_path))
                    compile_jobs.append((key, core, command, source_path, object_path))
            plan.append((cell, core, core_flags, out_dir, unit_jobs))

    record["c_standard"] = _verify_standard(standard_flag_sets, env, tmp_dir)
    results = iter(process.ordered_map(compile_unit, compile_jobs, jobs))
    for cell, core, core_flags, out_dir, unit_jobs in plan:
        cell_name = cell.name
        objects = []
        sizes = {}
        undefined = {}
        object_names = {}
        defined = set()
        for unit_cell, source, key, object_path in unit_jobs:
            unit_sizes, unit_undefined, defined_text = next(results)
            object_names[key] = os.path.basename(object_path)
            objects.append(object_path)
            sizes[key] = unit_sizes
            undefined[key] = unit_undefined
            defined.update(parse_names(defined_text))
            external_by_core.setdefault(core, {})["%s/%s" % (unit_cell.name, source)] = \
                registry.external_symbols(defined_text)
        archive = None
        if make_archive:
            archive = os.path.join(out_dir, cell.archive_name)
            # D: deterministic members (no time stamp, owner or mode), so one revision gives one archive.
            run_tool([AR_PATH, "rcsD", archive] + objects, env, "archive %s (%s)" % (cell_name, core))
        own_keys = list(cell.sources)
        dependency_keys = [key for key in sizes if key not in own_keys]
        total = [sum(sizes[k][i] for k in own_keys) for i in range(3)]
        dependency_total = [sum(sizes[k][i] for k in dependency_keys) for i in range(3)]
        record["results"].append({
            "cell": cell_name, "core": core, "core_flags": core_flags, "cell_flags": ["-std=%s" % cell.standard],
            "definitions": cell.definition_flags(), "include_roots": cell.include_roots,
            "depends_on": [dependency.name for dependency in cell.dependency_closure()],
            "sources": own_keys, "objects": object_names, "sizes": sizes,
            "total": {"text": total[0], "data": total[1], "bss": total[2]},
            "dependency_total": {"text": dependency_total[0], "data": dependency_total[1], "bss": dependency_total[2]},
            "undefined": undefined,
            "undefined_all": sorted({name for names in undefined.values() for name in names}),
            "external": sorted({name for names in undefined.values() for name in names} - defined),
            "archive": os.path.relpath(archive, run_dir).replace("\\", "/") if archive else None,
        })
        if cell.budget is not None:
            # The declared budget of the Cell's own objects (set from measurement).
            limits = cell.budget[core]
            record["results"][-1]["budget"] = dict(limits)
            over = ["%s %d > %d" % (part, value, limits[part])
                    for part, value in zip(("text", "data", "bss"), total) if value > limits[part]]
            if over:
                raise CrossBuildError("Cell %s on %s exceeds its budget: %s" % (cell_name, core, ", ".join(over)))
        if cell.dependency is not None:
            # The checkout of a dependency Cell (metadata, never a condition).
            record["results"][-1]["dependency"] = cell.dependency_record()
        if cell.external_headers is not None:
            external, configs = cell.external_roots(core)
            record["results"][-1]["external_headers"] = {
                "commit": cell.external_headers["commit"], "checkout": cell.external_checkout(),
                "include_roots": external,
                "config_roots": [os.path.relpath(p, REPO_ROOT).replace("\\", "/") for p in configs]}

    # One firmware image holds every Cell that is not a platform binding and at most one platform
    # binding (10_pkg/c/platform/<target>): platforms are alternatives, never linked together.
    # Uniqueness is checked for the image without a platform and for the image with each platform.
    platforms = {cell.name for cell in cells if cell.path.startswith("platform/")}
    for core, units in sorted(external_by_core.items()):
        base = {unit: symbols for unit, symbols in units.items() if unit.split("/", 1)[0] not in platforms}
        entry = {"units": len(base), "symbols": registry.require_unique_symbols(base), "platforms": {}}
        for platform in sorted(platforms):
            image = dict(base, **{unit: symbols for unit, symbols in units.items()
                                  if unit.split("/", 1)[0] == platform})
            entry["platforms"][platform] = {"units": len(image), "symbols": registry.require_unique_symbols(image)}
        record["unique_external_symbols"][core] = entry
    record["maintainer_tools"] = tools
    # The Foundation checkout read by this run (metadata, never a condition).
    standard = registry.standard_record()
    if standard is not None:
        record["fnd_sdk_standard"] = standard
    statistics = cache.statistics(REPO_ROOT)
    record["object_cache"] = {key: (statistics[key] - cache_before[key] if isinstance(statistics[key], int)
                                    else statistics[key]) for key in statistics}

    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return run_dir, record


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cell", default=None, help="one Cell of the registry (default: every freestanding arm Cell)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--archive", action="store_true", help="also create a static archive per Cell and core")
    parser.add_argument("--accept-tool-drift", action="store_true",
                        help="run although the maintainer tools differ from their template manifest (recorded)")
    parser.add_argument("--jobs", type=int, default=None,
                        help="parallel compile workers (default: the logical processors)")
    parser.add_argument("--no-object-cache", action="store_true",
                        help="reuse objects inside this run only (required for a release candidate build)")
    args = parser.parse_args(argv)
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    try:
        run_dir, record = build(args.run_id or new_run_id(), args.archive, args.cell, args.accept_tool_drift,
                                "run" if args.no_object_cache else "development", args.jobs)
    except (CrossBuildError, registry.RegistryError) as error:
        print("cross_build: %s" % error, file=sys.stderr)
        if getattr(error, "details", None):
            print("cross_build: details: %s" % json.dumps(error.details, sort_keys=True, default=str), file=sys.stderr)
        return 1
    print("compiler: %s" % record["compiler_version"])
    print("run_dir: %s" % run_dir)
    for result in record["results"]:
        total = result["total"]
        dependency = result["dependency_total"]
        print("%s %s: text=%d data=%d bss=%d dependencies_text=%d undefined=%s" % (
            result["cell"], result["core"], total["text"], total["data"], total["bss"],
            dependency["text"], ",".join(result["external"]) or "-"))
    for core, unique in sorted(record["unique_external_symbols"].items()):
        print("unique %s: units=%d external_symbols=%d duplicates=0" % (core, unique["units"], unique["symbols"]))
        for platform, image in sorted(unique["platforms"].items()):
            print("unique %s with %s: units=%d external_symbols=%d duplicates=0"
                  % (core, platform, image["units"], image["symbols"]))
    return 0
