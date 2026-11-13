# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Build one Cell into a shared library for Python-driven verification.

Python standard library only. The library is a product artifact: the exported
symbol set equals the functions declared in the Cell's public membrane header
(`membrane/xpt_*.h`): the functions named `<prefix>_<cell>_*`, or, for a Cell
that declares `function_prefix` in the registry, those named with that prefix.
The list is taken from the header as the compiler preprocesses it under the
Cell's flags: every file-scope declaration of a function with external linkage
that is no definition (no `static` or `inline` definition, typedef, pointer to
a function or struct member), wherever its lines break.
The ABI mirror `10_pkg/test/c/<path>/<prefix>_<cell>_abi.py` is checked by the C compiler with a compile-only `_Static_assert` probe. No
executable is ever produced. All output goes to `90_temps/build/<cell>/<run-id>/`.
The Cell list, standard, definitions, include roots and dependencies come from
the registry (the standard is the one of `c_standard`, ISO/IEC 9899:2018);
the compiler folder, the fnd-sdk-standard pin, the image target, the
Provider family rule, SDK link libraries and export aliases from
`maint_config.json` `host_build`; product logic that is not data from the
hooks of `product_hooks.py`.

A Cell that `depends_on` another Cell links that Cell's sources into its
library. Each Cell is compiled with its own standard, definitions and include
roots; the consumer additionally sees the membrane of every dependency as an
include root and nothing else of it. The exports are still the functions of the
consumer's own public header only.

A dependency Cell (registry key `dependency`: a Cell of a declared submodule
below `10_pkg/deps/`) is linked the same way: its sources are compiled from the
checkout with this product's flags into the library of every Cell that depends
on it, and its membrane is their include root. It has no library, ABI mirror or
test here (`--cell <dependency Cell>` is refused, `--all` skips it); a library
that names it in `exports` exports the functions of its public header through
the `.def` file of the run directory. Nothing is written below the checkout;
the build record keeps its checkout under `dependency_cells`.

A hosted Cell that binds the required operations of another Cell is the test
library of that Cell (`library_from`); `exports` lists the Cells whose public
header the library exports, `link_libraries` the system import libraries.
`--run-tests` runs the tests of the Cell and of every Cell whose library it
carries, against the one library. A Cell with both `library_from` and
`exports` owns its library (the product runtime library).

A Provider Cell (`provider/<name>`) builds its test image
`<prefix>_provider_<name>.dll`: it exports the static registration
`<prefix>_provider_<name>_registration` (as data) and the functions of its
membrane header, and gets its image build record beside it. Every library that
links Provider Cells exports their registrations as data and gets an image
build record with one row per Provider (its family from `provider_family`).

Every object of the library is listed with `nm`, and two units that define the
same external-linkage symbol fail the build (registry.require_unique_symbols).
A gcc command line longer than Windows accepts is passed through a response
file, recorded under `gcc_response_files`.

The standard is verified by value, not by option name: once per distinct
compiler and unit flag set of the run, a one-line probe is preprocessed and the
build fails closed unless `__STDC_VERSION__` expands to the value of the
standard; the build record keeps every probe under `c_standard`.

    python -I -B 20_ops/tools/build.py (--cell <name> ... | --all) [--run-id ID] [--run-tests]
                                      [--sanitize address|undefined] [--accept-tool-drift]
                                      [--jobs N] [--no-object-cache]
    python -I -B 20_ops/tools/build.py --prune-object-cache [--max-age-days D] [--max-bytes B]

Prints `<stem>_library: <path>` per named Cell; with `--run-tests` it then runs
the Python tests of each Cell with `<STEM>_LIBRARY` set to that path. Any
failure exits non-zero with a reason on stderr (fail closed). Several `--cell`
(or `--all`, every Cell with a windows target) build in one run: Cells that
share a library (the same `library_plan`) build it once, and every unit is
compiled once per run.

Build speed (`.instructions/build-optimization.md`): the units, the ABI probes
and the fixture units are compiled on `--jobs` worker threads (default: the
logical processors), and the link order stays the registry order. gcc objects
and their `nm` listings come from the content-addressed object cache
(`object_cache`, development cache `90_temps/development/build/c_objects/`);
`--no-object-cache` reuses objects inside the run only, which a release
candidate build must use. A cached and an uncached build give byte-identical
libraries, fixtures and image records. A product `run_command` hook observes
real compiles: with it, product units are never served from the cache, and
they compile one at a time unless the hook declares `concurrent = True` (it
then numbers its observations by the context's `unit_index`).

`--sanitize` builds a memory-safety verification variant of the same library;
it is never a product. The exported set, the ABI probe and the build record are
those of the plain build, and the record names the variant under `sanitize`:

- `undefined`: gcc with `-fsanitize=undefined` in trap mode (no runtime; a
  finding executes an illegal instruction, which ctypes reports as an OSError
  0xC000001D, or the process ends).
- `address`: MSVC `cl /fsanitize=address` with the dynamic ASan runtime of the
  same toolset, copied beside the library so a Python process loads it with the
  library. The ABI is also probed with cl. Test fixtures stay gcc-built test input.
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys

from . import c_standard
from . import config as maint_config
from . import object_cache
from . import process
from . import registry

_SETTINGS = maint_config.current() if maint_config.present() else None
_HOST = _SETTINGS.host_build if _SETTINGS is not None and _SETTINGS.language == "c" else None
# The product's host compiler (None in the template, which has no configuration).
GCC_BIN_DIR = os.path.normpath(_HOST["compiler_bin_dir"]) if _HOST else None
GCC_PATH = os.path.join(GCC_BIN_DIR, "gcc.exe") if _HOST else None
OBJDUMP_PATH = os.path.join(GCC_BIN_DIR, "objdump.exe") if _HOST else None
NM_PATH = os.path.join(GCC_BIN_DIR, "nm.exe") if _HOST else None

# The standard comes from the registry (no GNU extensions); the warning set is the build bar.
WARNING_FLAGS = ["-Wall", "-Wextra", "-Werror", "-pedantic"]

# A library link is reproducible: no PE time stamp, and a fixed image base instead of the one the
# linker derives from the output path (the run directory). The same sources give the same bytes.
REPRODUCIBLE_LINK_FLAGS = ["-Wl,--no-insert-timestamp", "-Wl,--disable-auto-image-base", "-Wl,--gc-sections"]
# A test fixture link is reproducible too (no time stamp, fixed image base); it keeps every section, since
# MinGW automatic export is its whole surface.
FIXTURE_LINK_FLAGS = ["-Wl,--no-insert-timestamp", "-Wl,--disable-auto-image-base"]

# Unit compilation optimization flags: speed optimization (-O2) with per-function/per-data sections
# and no compiler .ident strings so --gc-sections removes unreferenced code/data cleanly.
OPTIMIZE_FLAGS = ["-O2", "-ffunction-sections", "-fdata-sections", "-fno-ident"]

TOOLS_ROOT = process.TOOLS_ROOT
TOOL_TIMEOUT_SECONDS = 300
# A gcc command line at or beyond this length goes through a response file (CreateProcess limit 32767).
RESPONSE_FILE_THRESHOLD = 28000

# Memory-safety verification variants (--sanitize). UBSan in trap mode needs no runtime; float casts are
# checked too (an out-of-range float-to-integer conversion is undefined in C).
SANITIZERS = ("address", "undefined")
UBSAN_FLAGS = ["-fsanitize=undefined,float-cast-overflow", "-fsanitize-trap=all", "-fno-sanitize-recover=all"]
# MSVC installations searched for an ASan-capable toolset (newest first); nothing is installed or changed.
MSVC_ROOTS = [r"C:\Program Files\Microsoft Visual Studio", r"C:\Program Files (x86)\Microsoft Visual Studio"]
ASAN_RUNTIME = "clang_rt.asan_dynamic-x86_64.dll"
MSVC_WARNING_FLAGS = ["/W4", "/WX"]
# Symbols MSVC emits as external COMDAT in every unit that uses them (string literals, float constants,
# ASan bookkeeping); they are merged by the linker and are not definitions of a Cell.
MSVC_COMPILER_SYMBOL = re.compile(r"^(\?\?_C@|__real@|__xmm@|__ymm@|__asan|__odr_asan|_volmd$|\.)")

REPO_ROOT = registry.REPO_ROOT
OBJECT_CACHE_ROOT = object_cache.development_root(REPO_ROOT)
OBJECT_CACHE_MODES = ("development", "run")
RUN_ID_PATTERN = registry.RUN_ID_PATTERN
RegistryError = registry.RegistryError
get_cell = registry.get_cell
STANDARD_PIN_PATH = registry.STANDARD_PIN_PATH
IMAGE_TARGET = _HOST["image_target"] if _HOST else None
REGISTRATION_PATTERN = re.compile(r"^%s_provider_([a-z][a-z0-9]*(?:_[a-z0-9]+)*)_registration$"
                                  % re.escape(_SETTINGS.symbol_prefix)) if _HOST else None


class BuildError(Exception):
    """A build step failed; the message is the reason. `details` (a dict) holds what the build record of the
    failed run keeps beside the message (resolved paths, expected and found ABI values)."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def _settings():
    if _SETTINGS is None or _HOST is None:
        raise BuildError("maint_config.json configures no C host build")
    return _SETTINGS


def gcc_present():
    return GCC_PATH is not None and os.path.isfile(GCC_PATH)


def new_run_id(prefix="build"):
    return process.new_run_id(prefix)


def _child_env(tmp_dir):
    env = dict(os.environ)
    env["PATH"] = GCC_BIN_DIR + os.pathsep + env.get("PATH", "")
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = tmp_dir
    return env


def _gcc_response_command(args, cwd):
    """Retain exact long GCC arguments without exceeding Windows CreateProcess limits."""
    if os.name != "nt" or GCC_PATH is None or os.path.normcase(os.path.abspath(args[0])) != os.path.normcase(GCC_PATH):
        return args
    if len(subprocess.list2cmdline(args)) < RESPONSE_FILE_THRESHOLD:
        return args
    encoded = json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    stem = "gcc_" + hashlib.sha256(encoded).hexdigest()[:20]
    response = os.path.join(cwd, "tmp", stem + ".rsp")
    content = "".join('"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"\n' for arg in args[1:])
    with open(response, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    command = [args[0], "@" + response]
    with open(os.path.join(cwd, "tmp", stem + ".json"), "x", encoding="utf-8", newline="\n") as handle:
        json.dump({"argv": args, "command": command, "response_file": response,
                   "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}, handle, indent=2)
        handle.write("\n")
    return command


def _run(args, env, cwd, what):
    command = _gcc_response_command(args, cwd)
    try:
        done = subprocess.run(command, env=env, cwd=cwd, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=TOOL_TIMEOUT_SECONDS,
                              creationflags=process.window_flags())
    except subprocess.TimeoutExpired:
        raise BuildError("%s timed out after %d s" % (what, TOOL_TIMEOUT_SECONDS))
    except OSError as error:
        raise BuildError("%s could not start: %s" % (what, error))
    if done.returncode != 0:
        raise BuildError("%s failed (exit %d):\n%s%s" % (what, done.returncode, done.stdout, done.stderr))
    return done


def parse_sanitize(text):
    """The sanitizer variant named by --sanitize ("address" or "undefined"), or None. Both at once are
    refused: no toolchain provides ASan and UBSan for Windows together."""
    if text is None:
        return None
    names = sorted(set(part.strip() for part in text.split(",") if part.strip()))
    unknown = [name for name in names if name not in SANITIZERS]
    if not names or unknown:
        raise BuildError("unknown sanitizer %r (choose from %s)" % (text, ", ".join(SANITIZERS)))
    if len(names) > 1:
        raise BuildError("address and undefined are separate variants: ASan comes from MSVC cl, which has no "
                         "UBSan, and UBSan from gcc in trap mode, which has no ASan runtime for Windows")
    return names[0]


def _msvc_vcvars():
    """vcvars64.bat of the newest installed MSVC toolset that ships the dynamic ASan runtime, or None."""
    found = []
    for root in MSVC_ROOTS:
        if not os.path.isdir(root):
            continue
        for release in os.listdir(root):
            for edition in (os.listdir(os.path.join(root, release)) if os.path.isdir(os.path.join(root, release)) else []):
                vcvars = os.path.join(root, release, edition, "VC", "Auxiliary", "Build", "vcvars64.bat")
                tools = os.path.join(root, release, edition, "VC", "Tools", "MSVC")
                if not os.path.isfile(vcvars) or not os.path.isdir(tools):
                    continue
                for version in os.listdir(tools):
                    if os.path.isfile(os.path.join(tools, version, "bin", "Hostx64", "x64", ASAN_RUNTIME)):
                        found.append((tuple(int(p) for p in re.findall(r"\d+", version)), release, vcvars, version))
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][2], found[0][3]


def msvc_environment(tmp_dir):
    """The child environment of vcvars64 for the ASan-capable toolset, with the paths of cl, link and the
    ASan runtime. Fails closed when no such toolset is installed."""
    located = _msvc_vcvars()
    if located is None:
        raise BuildError("no MSVC toolset with %s is installed: ASan is unavailable" % ASAN_RUNTIME)
    vcvars, version = located
    try:
        done = subprocess.run('call "%s" -vcvars_ver=%s >nul && set' % (vcvars, version), shell=True,
                              capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL,
                              timeout=TOOL_TIMEOUT_SECONDS, creationflags=process.window_flags())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BuildError("vcvars64 could not run: %s" % error)
    env = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line and not line.startswith("="))
    tools = env.get("VCToolsInstallDir")
    if done.returncode != 0 or not tools:
        raise BuildError("vcvars64 failed (exit %d): %s" % (done.returncode, done.stderr.strip()))
    bin_dir = os.path.join(tools, "bin", "Hostx64", "x64")
    paths = {"cl": os.path.join(bin_dir, "cl.exe"), "link": os.path.join(bin_dir, "link.exe"),
             "asan_runtime": os.path.join(bin_dir, ASAN_RUNTIME), "version": env.get("VCToolsVersion", version)}
    for key in ("cl", "link", "asan_runtime"):
        if not os.path.isfile(paths[key]):
            raise BuildError("the MSVC toolset lacks %s" % paths[key])
    env["TMP"] = env["TEMP"] = tmp_dir
    return env, paths


def _msvc_unit_command(paths, unit_cell, includes, extra):
    """cl flags of one unit: the standard (c_standard.MSVC_FLAG), the warnings as errors, its definitions.
    `includes` is the gcc form ("-I", root, ...), joined to one argument per root."""
    joined = ["/I" + root for flag, root in zip(includes[0::2], includes[1::2]) if flag == "-I"]
    if len(joined) * 2 != len(includes):
        raise BuildError("unexpected include arguments for cl: %s" % includes)
    return ([paths["cl"], "/nologo", c_standard.MSVC_FLAG, "/TC"] + MSVC_WARNING_FLAGS
            + unit_cell.definition_flags() + joined + extra)


def _strip_comments(text):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)


# A preprocessor directive with its continuation lines (a header read without the preprocessor).
DIRECTIVE_PATTERN = re.compile(r"^[ \t]*#[^\n]*(?:\\\n[^\n]*)*", re.MULTILINE)
# The C++ linkage block of a header read without the preprocessor: its braces enclose file-scope declarations.
LINKAGE_BLOCK_PATTERN = re.compile(r'\bextern\s*"C"\s*\{?')
# A line marker of `gcc -E` output: `# <line> "<file>" <flags>`.
LINE_MARKER_PATTERN = re.compile(r'^#(?:line)?[ \t]+\d+[ \t]+"((?:[^"\\]|\\.)*)"')
C_TOKEN_PATTERN = re.compile(r"[A-Za-z_]\w*|\S")
C_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_]\w*")
# Specifiers whose declaration is no exported function: a type name and internal linkage.
UNEXPORTED_SPECIFIERS = ("typedef", "static")
# Words whose parenthesized argument is no parameter list: attributes, alignment and static assertions.
ARGUMENT_WORDS = ("__attribute__", "__attribute", "__declspec", "__asm__", "__asm", "_Alignas", "alignas",
                  "_Static_assert", "static_assert")
# The first token of a parenthesized declarator: a pointer (or block) to a function, not a function.
POINTER_DECLARATORS = ("*", "^")


def _group_end(tokens, start):
    """The index of the token that closes the parenthesis opened at `start`, or None."""
    depth = 0
    for index in range(start, len(tokens)):
        if tokens[index] == "(":
            depth += 1
        elif tokens[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _declared_function(statement, prefix):
    """The name a file-scope declaration (its tokens, without the final `;`) declares as a function with
    external linkage and the prefix, or None: no typedef, no static declaration, no pointer to a
    function, no object; the identifier directly before the parameter list is the name."""
    if any(word in statement for word in UNEXPORTED_SPECIFIERS):
        return None
    index = 0
    brackets = 0
    while index < len(statement):
        token = statement[index]
        if token == "[":
            brackets += 1
        elif token == "]":
            brackets -= 1
        elif token == "(" and brackets == 0:
            end = _group_end(statement, index)
            if end is None:
                return None
            if index and statement[index - 1] in ARGUMENT_WORDS:
                index = end
            else:
                name = statement[index - 1] if index else ""
                declarator_follows = end + 1 < len(statement) and statement[end + 1] in ("(", "[")
                if index < 2 or C_IDENTIFIER_PATTERN.fullmatch(name) is None or not name.startswith(prefix) \
                        or statement[index + 1] in POINTER_DECLARATORS or declarator_follows:
                    return None
                return name
        index += 1
    return None


def declared_functions(text, prefix):
    """The functions with external linkage that C text declares at file scope without defining them, whose
    name starts with `prefix`, in declaration order (a repeated declaration repeats the name).

    `text` is a header as the compiler preprocessed it, or as written: comments, directives and the C++
    linkage block are then dropped here. A declaration may span lines. A function definition (static,
    inline or plain), a static declaration, a typedef, a pointer to a function, an object and the member
    of a struct, union or enum body declare no exported function."""
    text = LINKAGE_BLOCK_PATTERN.sub(" ", DIRECTIVE_PATTERN.sub("", _strip_comments(text)))
    names = []
    statement = []
    depth = 0
    opened_after = None  # the token before the file-scope brace group being skipped
    for token in C_TOKEN_PATTERN.findall(text):
        if token == "{":
            if depth == 0:
                opened_after = statement[-1] if statement else None
            depth += 1
        elif token == "}":
            # A closing brace at file scope ends the C++ linkage block of a header read as written.
            if depth:
                depth -= 1
                if depth == 0 and opened_after == ")":
                    statement = []  # the body of a function definition: no declaration to export
        elif depth == 0:
            if token == ";":
                name = _declared_function(statement, prefix)
                if name is not None:
                    names.append(name)
                statement = []
            else:
                statement.append(token)
    return names


def header_text(preprocessed, header_path):
    """The lines of `gcc -E` output that come from the file `header_path` itself, by the line markers: the
    text of the headers it includes and of the unit that includes it is left out."""
    wanted = os.path.normcase(os.path.normpath(header_path))
    lines = []
    selected = False
    for line in preprocessed.splitlines():
        marker = LINE_MARKER_PATTERN.match(line)
        if marker is not None:
            origin = marker.group(1).replace("\\\\", "\\").replace('\\"', '"')
            selected = os.path.normcase(os.path.normpath(origin)) == wanted
        elif selected:
            lines.append(line + "\n")
    return "".join(lines)


def header_functions(cell, preprocessed=None):
    """Function names the public header declares for export, in declaration order (declared_functions with
    the Cell's function prefix). `preprocessed` is the `gcc -E` output of a unit that includes the header
    under the Cell's flags, which the build passes (_exported_functions): macros are expanded and only the
    selected conditional branches remain. Without it the header is read as written. A Provider Cell's
    public header may declare none: its static registration is its whole surface."""
    header_path = os.path.join(cell.root, cell.public_header)
    if preprocessed is None:
        with open(header_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = header_text(preprocessed, header_path)
    names = declared_functions(text, cell.function_prefix)
    if (not names and not cell.provider) or len(set(names)) != len(names):
        raise BuildError("public header yields no function set or a duplicate: %s" % header_path)
    return names


def _exported_functions(exported_cells, env, run_dir):
    """The export list of a library: the functions of the public header of every exported Cell, each header
    preprocessed by the compiler under its own Cell's flags and include roots (a one-line unit of the run
    directory includes it; nothing is compiled and nothing is written beside the header)."""
    functions = []
    for exported in exported_cells:
        header_path = os.path.join(exported.root, *exported.public_header.split("/"))
        unit_path = os.path.join(run_dir, "tmp", "export_%s.c" % exported.name)
        with open(unit_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write('#include "%s"\n' % header_path.replace("\\", "/"))
        done = _run(_unit_flags(exported) + ["-E"] + _unit_includes(exported) + [unit_path], env, run_dir,
                    "preprocess the public header of %s" % exported.name)
        functions.extend(header_functions(exported, done.stdout))
    if len(set(functions)) != len(functions):
        raise BuildError("the exported public headers declare a function twice")
    return functions


def _load_abi(cell):
    if not os.path.isfile(cell.abi_path):
        raise BuildError("ABI mirror missing: %s" % cell.abi_path)
    spec = importlib.util.spec_from_file_location("%s_%s_abi_probe" % (cell.settings.symbol_prefix, cell.name),
                                                  cell.abi_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _probe_key(c_name):
    """The macro key of a probed type: its typedef name, or its struct tag ("struct x" -> X)."""
    return c_name.replace("struct ", "").upper()


def _probe_source(cell, abi):
    lines = ["/* Compile-time ABI probe: no code, no executable. */"]
    lines.extend('#include "%s"' % name for name in cell.contract_headers)
    lines.append("")
    for c_name, layout in abi.struct_layout().items():
        key = _probe_key(c_name)
        lines.append('_Static_assert(sizeof(%s) == EXPECT_SIZE_%s, "size of %s");' % (c_name, key, c_name))
        for member in layout["offsets"]:
            lines.append('_Static_assert(offsetof(%s, %s) == EXPECT_OFFSET_%s_%s, "offset of %s.%s");'
                         % (c_name, member, key, member.upper(), c_name, member))
    for name in abi.CONSTANTS:
        lines.append('_Static_assert(EXPECT_CONST_%s_OK, "value of %s");' % (name, name))
    lines.append("")
    return "\n".join(lines)


# A measuring declaration per probed value: gcc names the array bound in its int-to-pointer diagnostic,
# "initialization of 'char (*)[N]' from 'int'", so the found value is read without running anything.
MEASURE_PATTERN = re.compile(r":(\d+):\d+: (?:error|warning): initialization of 'char \(\*\)\[(\d+)\]' from 'int'")


def _measure_source(cell, abi):
    """(source, [(line, key)]): one file-scope declaration per size, offset and non-negative constant, whose
    array bound is the value plus one (a zero bound is not portable C)."""
    lines = ["/* ABI measurement: syntax only, no code, no executable. */", "#include <stddef.h>"]
    lines.extend('#include "%s"' % name for name in cell.contract_headers)
    probes = []

    def add(key, expression):
        lines.append("static char (*measure_%d)[(%s) + 1] = 1;" % (len(probes), expression))
        probes.append((len(lines), key))
    for c_name, layout in abi.struct_layout().items():
        add("sizeof(%s)" % c_name, "sizeof(%s)" % c_name)
        for member in layout["offsets"]:
            add("offsetof(%s, %s)" % (c_name, member), "offsetof(%s, %s)" % (c_name, member))
    for name, value in abi.CONSTANTS.items():
        if isinstance(value, int) and 0 <= value < 2 ** 31:
            add(name, "(%s) >= 0 && (%s) < 0x7fffffff ? (%s) : 0" % (name, name, name))
    return "\n".join(lines) + "\n", probes


def _abi_expected(abi):
    expected = {}
    for c_name, layout in abi.struct_layout().items():
        expected["sizeof(%s)" % c_name] = layout["size"]
        for member, offset in layout["offsets"].items():
            expected["offsetof(%s, %s)" % (c_name, member)] = offset
    for name, value in abi.CONSTANTS.items():
        expected[name] = value
    return expected


def _abi_mismatch(probe_cell, abi, command, env, run_dir, failure):
    """A BuildError naming every ABI value that differs (expected from the mirror, found in the header as
    gcc measures it), the mirror and the header paths and the next step; the values go to `details`."""
    source, probes = _measure_source(probe_cell, abi)
    path = os.path.join(run_dir, "abi_measure_%s.c" % probe_cell.name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(source)
    found = {}
    try:
        done = subprocess.run(_gcc_response_command(command + [path], run_dir), env=env, cwd=run_dir,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=TOOL_TIMEOUT_SECONDS, creationflags=process.window_flags())
        by_line = dict(probes)
        for line, bound in MEASURE_PATTERN.findall(done.stdout + done.stderr):
            if int(line) in by_line:
                found[by_line[int(line)]] = int(bound) - 1
    except (OSError, subprocess.TimeoutExpired) as error:
        found = {}
        note = "the measurement could not run: %s" % error
    else:
        note = None if found else "gcc reported no measured value (the header may not compile)"
    expected = _abi_expected(abi)
    differs = {key: {"expected": value, "found": found.get(key)} for key, value in expected.items()
               if key in found and found[key] != value}
    unmeasured = sorted(key for key in expected if key not in found)
    headers = [os.path.join(probe_cell.root, name) for name in probe_cell.contract_headers]
    text = "; ".join("%s expected %s found %s" % (key, row["expected"], row["found"]) for key, row in sorted(differs.items()))
    message = ("ABI mismatch of Cell %s: %s. The mirror %s disagrees with the C headers %s%s. Next step: correct "
               "the layout or constant in the header, or update the mirror to the reviewed header layout and its "
               "consumers together.\n%s"
               % (probe_cell.name, text or "values not measurable", probe_cell.abi_path, ", ".join(headers),
                  "" if not unmeasured else " (not measured: %s)" % ", ".join(unmeasured[:8]), failure))
    return BuildError(message, {"abi_mismatch": {"cell": probe_cell.name, "mirror": probe_cell.abi_path,
                                                 "headers": headers, "differs": differs,
                                                 "unmeasured": unmeasured, "note": note}})


def _probe_definitions(abi):
    defs = []
    for c_name, layout in abi.struct_layout().items():
        key = _probe_key(c_name)
        defs.append("-DEXPECT_SIZE_%s=%du" % (key, layout["size"]))
        for member, offset in layout["offsets"].items():
            defs.append("-DEXPECT_OFFSET_%s_%s=%du" % (key, member.upper(), offset))
    for name, value in abi.CONSTANTS.items():
        if value < 0:
            check = "((long long)(%s) == (%dLL))" % (name, value)
        else:
            check = "((unsigned long long)(%s) == %dULL)" % (name, value)
        defs.append("-DEXPECT_CONST_%s_OK=%s" % (name, check))
    return defs


def _exported_names(dll_path, env, cwd):
    if OBJDUMP_PATH is None or not os.path.isfile(OBJDUMP_PATH):
        raise BuildError("objdump is absent: cannot verify the exported symbol set (%s)" % OBJDUMP_PATH)
    out = _run([OBJDUMP_PATH, "-p", dll_path], env, cwd, "objdump").stdout
    return _export_table(out, dll_path)


def _export_table(objdump_text, dll_path):
    """The exported names of `objdump -p` output (its Ordinal/Name Pointer table)."""
    if "[Ordinal/Name Pointer] Table" not in objdump_text:
        raise BuildError("objdump shows no export table for %s" % dll_path)
    table = objdump_text.split("[Ordinal/Name Pointer] Table", 1)[1]
    return re.findall(r"^\s*\[\s*\d+\]\s+\+base\[\s*\d+\]\s+[0-9a-fA-F]+\s+(\S+)\s*$", table,
                      flags=re.MULTILINE)


def _unit_flags(unit_cell):
    return [GCC_PATH, "-std=%s" % unit_cell.standard] + WARNING_FLAGS + unit_cell.definition_flags()


def _verify_standard(unit_cells, env, run_dir, msvc_paths=None, msvc_env=None):
    """Verify the standard of every distinct compiler and unit flag set of the run by value: preprocess the
    one-line probe and refuse unless __STDC_VERSION__ expands to the standard's value. Returns the
    `c_standard` member of the build record (c_standard.run_record)."""
    probe_path = os.path.join(run_dir, "tmp", c_standard.PROBE_FILE_NAME)
    with open(probe_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(c_standard.PROBE_SOURCE)
    probes = {}
    for unit_cell in unit_cells:
        commands = [(_unit_flags(unit_cell) + c_standard.PREPROCESS_FLAGS + [probe_path], env)]
        if msvc_paths is not None:
            commands.append((_msvc_unit_command(msvc_paths, unit_cell, [],
                                                c_standard.MSVC_PREPROCESS_FLAGS + [probe_path]), msvc_env))
        for command, command_env in commands:
            if tuple(command) in probes:
                continue
            out = _run(command, command_env, run_dir, "C standard probe of %s" % unit_cell.name).stdout
            record = c_standard.verification(command, out)
            if not record["verified"]:
                raise BuildError(c_standard.mismatch("Cell %s" % unit_cell.name, record),
                                 {"c_standard": c_standard.run_record([record])})
            probes[tuple(command)] = record
    return c_standard.run_record(list(probes.values()))


def _unit_includes(unit_cell):
    includes = []
    for root in unit_cell.include_roots:
        includes += ["-I", os.path.normpath(os.path.join(unit_cell.root, root))]
    for membrane in unit_cell.dependency_membranes():
        includes += ["-I", membrane]
    for root in unit_cell.passive_include_roots() + unit_cell.standard_roots():
        includes += ["-I", root]
    return includes


def standard_include_root():
    """The include directory of the Foundation's fnd-sdk-standard header (registry.standard_include_root,
    failing as a BuildError): a fixture is compiled against nothing else of the standard."""
    try:
        return registry.standard_include_root(_settings())
    except RegistryError as error:
        raise BuildError(str(error), error.details)


def export_aliases(cell, settings=None):
    """[(alias, target)] of `host_build.provider_export_aliases` for a Provider Cell: an alias
    `<prefix>_provider_<operation>` exports `<stem>_<operation>` under the common name."""
    settings = settings or _settings()
    if not cell.provider:
        return []
    base = "%s_provider_" % settings.symbol_prefix
    pairs = []
    for alias in settings.host_build["provider_export_aliases"]:
        if not alias.startswith(base) or len(alias) == len(base):
            raise BuildError("export alias %s must start with %s" % (alias, base))
        pairs.append((alias, "%s_%s" % (cell.stem, alias[len(base):])))
    return pairs


def definition_text(cell, providers, functions, aliases):
    """The .def file: the registrations of the linked Providers as data, the alias exports, the functions."""
    lines = ["LIBRARY %s\n" % cell.library_name, "EXPORTS\n"]
    lines += ["    %s DATA\n" % provider.registration for provider in providers]
    lines += ["    %s = %s\n" % pair for pair in aliases]
    lines += ["    %s\n" % name for name in functions]
    return "".join(lines)


class _StaticRegistration(ctypes.Structure):
    # comsect1_static_registration (comsect1_provider.h), read from the built image.
    _fields_ = [("struct_size", ctypes.c_uint32), ("reserved", ctypes.c_uint32), ("name", ctypes.c_char_p),
                ("negotiate", ctypes.c_void_p), ("open", ctypes.c_void_p), ("describe", ctypes.c_void_p),
                ("describe_length", ctypes.c_uint64)]


class _HostDescriptor(ctypes.Structure):
    # comsect1_host_descriptor (comsect1_provider.h).
    _fields_ = [("struct_size", ctypes.c_uint32), ("family", ctypes.c_uint32), ("interface_major", ctypes.c_uint16),
                ("interface_minimum_minor", ctypes.c_uint16), ("interface_maximum_minor", ctypes.c_uint16),
                ("pointer_bits", ctypes.c_uint16), ("target", ctypes.c_char_p)]


class _ModuleDescriptor(ctypes.Structure):
    # comsect1_module_descriptor (comsect1_provider.h).
    _fields_ = [("struct_size", ctypes.c_uint32), ("family", ctypes.c_uint32), ("interface_major", ctypes.c_uint16),
                ("interface_minor", ctypes.c_uint16), ("flags", ctypes.c_uint32),
                ("implementation_version", ctypes.c_char_p)]


_NEGOTIATE = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.POINTER(_HostDescriptor), ctypes.POINTER(_ModuleDescriptor))


def _provider_family(registration, name):
    """The family a statically bound Provider's negotiate serves (the Foundation family travels in
    negotiation; a describe document names none). `negotiate_offers`: each configured family is offered
    in turn, in configuration order; `hook`: the product's `provider_family(registration, name)`."""
    settings = _settings()
    rule = settings.host_build["provider_family"]
    if rule["kind"] == "hook":
        try:
            family = settings.hook("provider_family")(registration, name)
        except maint_config.ConfigError as error:
            raise BuildError(str(error))
        if not isinstance(family, str) or not family:
            raise BuildError("the registration of Provider %s negotiates no %s family"
                             % (name, settings.environment_prefix))
        return family
    negotiate = _NEGOTIATE(registration.negotiate) if registration.negotiate else None
    for family, number, major in rule["offers"] if negotiate is not None else ():
        host = _HostDescriptor(struct_size=ctypes.sizeof(_HostDescriptor), family=number, interface_major=major,
                               interface_minimum_minor=0, interface_maximum_minor=0xFFFF,
                               pointer_bits=ctypes.sizeof(ctypes.c_void_p) * 8, target=IMAGE_TARGET.encode("ascii"))
        module = _ModuleDescriptor(struct_size=ctypes.sizeof(_ModuleDescriptor))
        if negotiate(ctypes.byref(host), ctypes.byref(module)) == 0 and module.family == number:
            return family
    raise BuildError("the registration of Provider %s negotiates no %s family" % (name, settings.environment_prefix))


def _write_image_record(tested, fixture_path, exported, digest, compiled_inputs=None):
    """The image build record (fnd-sdk-standard image_build_record.schema.json) of a fixture image
    that links statically bound Providers: one row per <prefix>_provider_<name>_registration it exports,
    the registration's own describe giving the values layout. Returns the record file name, or None
    for a fixture that links no Provider."""
    settings = _settings()
    names = sorted(match.group(1) for match in map(REGISTRATION_PATTERN.match, exported) if match)
    if not names:
        return None
    inputs = compiled_inputs
    if inputs is None:
        sources = sorted(n for n in os.listdir(tested.fixture_root) if n.endswith((".c", ".h")))
        inputs = []
        for name in sources:
            path = os.path.join(tested.fixture_root, name)
            with open(path, "rb") as handle:
                inputs.append({"path": os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                               "sha256": hashlib.sha256(handle.read()).hexdigest()})
    image = ctypes.CDLL(fixture_path)
    providers = []
    for name in names:
        registration_name = "%s_provider_%s_registration" % (settings.symbol_prefix, name)
        registration = _StaticRegistration.in_dll(image, registration_name)
        if registration.name is None or registration.name.decode("ascii") != name or not registration.describe:
            raise BuildError("the registration of Provider %s names another Provider or no describe" % name)
        describe = json.loads(ctypes.string_at(registration.describe, registration.describe_length).decode("utf-8"))
        providers.append({
            "name": name,
            "family": _provider_family(registration, name),
            "registration": registration_name,
            "interface_version": {"major": 1, "minor": 0},
            "implementation_version": "0.0.0-0",
            "digest_of": "sources",
            "inputs": inputs,
            "values": {"size": describe["values"]["size"], "alignment": describe["values"]["alignment"]},
        })
    stem = os.path.splitext(os.path.basename(fixture_path))[0]
    record = {"file_format_version": 1, "schema": "comsect1.image_build_record", "name": stem,
              "product": settings.image_record_product, "target": IMAGE_TARGET,
              "image": {"path": os.path.basename(fixture_path), "sha256": digest}, "providers": providers}
    record_name = stem + ".image.json"
    with open(os.path.join(os.path.dirname(fixture_path), record_name), "w", encoding="utf-8",
              newline="\n") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return record_name


def _write_cell_image_record(stem, providers, library_path, digest):
    """The image build record of a library that links Provider Cells (`<stem>.image.json` beside it): one
    row per Provider, its family the one its negotiate serves, its inputs the .c and .h files of the Cell
    and of the library Cells it compiles in, the values layout from its describe."""
    settings = _settings()
    image = ctypes.CDLL(library_path)
    rows = []
    for cell in sorted(providers, key=lambda item: item.provider):
        inputs = []
        # The Provider's own files and those of the libraries it compiles in.
        for owner, relative in [(unit, relative) for unit in cell.dependency_closure() + [cell]
                                for _cell, relative, _guard in unit.stem_files()]:
            path = os.path.join(owner.root, *relative.split("/"))
            with open(path, "rb") as handle:
                inputs.append({"path": os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                               "sha256": hashlib.sha256(handle.read()).hexdigest()})
        inputs.sort(key=lambda item: item["path"].encode("utf-8"))
        registration = _StaticRegistration.in_dll(image, cell.registration)
        if registration.name is None or registration.name.decode("ascii") != cell.provider or not registration.describe:
            raise BuildError("%s names another Provider or carries no describe" % cell.registration)
        describe = json.loads(ctypes.string_at(registration.describe, registration.describe_length).decode("utf-8"))
        rows.append({"name": cell.provider, "family": _provider_family(registration, cell.provider),
                     "registration": cell.registration,
                     "interface_version": {"major": 1, "minor": 0}, "implementation_version": "0.0.0-0",
                     "digest_of": "sources", "inputs": inputs,
                     "values": {"size": describe["values"]["size"], "alignment": describe["values"]["alignment"]}})
    record = {"file_format_version": 1, "schema": "comsect1.image_build_record", "name": stem,
              "product": settings.image_record_product, "target": IMAGE_TARGET,
              "image": {"path": os.path.basename(library_path), "sha256": digest}, "providers": rows}
    record_name = stem + ".image.json"
    with open(os.path.join(os.path.dirname(library_path), record_name), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return record_name


def open_object_cache(mode="development"):
    """The object cache of a build: `development` (the repository's development cache), `run` (reuse
    inside this process only, for a release candidate), or an `object_cache.ObjectCache` passed on."""
    if isinstance(mode, object_cache.ObjectCache):
        return mode
    if mode == "run":
        return object_cache.ObjectCache(None)
    if mode == "development":
        try:
            return object_cache.ObjectCache(OBJECT_CACHE_ROOT, REPO_ROOT)
        except (object_cache.CacheError, OSError) as error:
            raise BuildError("object cache refused: %s" % error)
    raise BuildError("unknown object cache mode %r (choose from %s)" % (mode, ", ".join(OBJECT_CACHE_MODES)))


def _toolchain_identity(env):
    try:
        return object_cache.toolchain_identity(GCC_PATH, env)
    except (object_cache.CacheError, OSError, subprocess.SubprocessError) as error:
        raise BuildError("the identity of %s is unavailable: %s" % (GCC_PATH, error))


def _compile_cached(cache, identity, command, object_path, source_path, env, run_dir, what):
    """One gcc compile through the object cache (a hit copies a verified object)."""
    return cache.compile(command, object_path, source_path, env, lambda argv: _run(argv, env, run_dir, what),
                         os.path.join(run_dir, "tmp"), identity, IMAGE_TARGET)


def _defined_symbols(cache, object_path, env, run_dir, what):
    text = cache.tool_output([NM_PATH, "--defined-only", object_path], object_path,
                             lambda argv: _run(argv, env, run_dir, what).stdout)
    return registry.external_symbols(text)


def _cache_delta(before, after):
    delta = {key: after[key] - before[key] for key in after if isinstance(after[key], int)}
    delta.update(mode=after["mode"], root=after["root"])
    return delta


def _build_fixture(tested, cell, run_dir, object_dir, env, cache=None, identity=None, jobs=1):
    """Build the test-only fixture library of `tested` (registry `test_fixture`) beside the library
    of `cell` that carries its tests, and return its record entry.

    The fixture is test input, never a product: its C sources live under the Cell's tests, it sees
    the membranes of the Cells the carrying library exports with their dependency membranes and
    passive vocabulary (the public header a Provider is built against), and it exports every
    external symbol it defines (MinGW automatic export), so the tests reach its static
    registrations as data. It is compiled with the Cell's standard and the warnings, linked with
    -shared, and never linked into the product library. It includes the Foundation's fnd-sdk-standard
    header as a Provider author does (standard_include_root resolves the checkout); a fixture that
    links statically bound Providers gets its image build record beside it. An explicit
    fixture_units selection also compiles actual owner units with the owner's private headers."""
    includes = ["-I", standard_include_root()]
    if tested.link_fixture:
        # A board double defines the operations the Cell itself requires: it sees that Cell's membrane
        # (which publishes them) and the membranes of its dependencies, nothing else.
        for membrane in [os.path.join(tested.root, "membrane")] + tested.dependency_membranes():
            includes += ["-I", membrane]
    for exported in cell.exported_cells():
        includes += ["-I", os.path.join(exported.root, "membrane")]
    for exported in cell.exported_cells():
        for root in exported.dependency_membranes() + exported.passive_include_roots():
            includes += ["-I", root]
    if tested.fixture_units:
        includes += _unit_includes(tested)
    sources = [(name, os.path.join(tested.fixture_root, name)) for name in tested.fixture_sources]
    sources += [(name, os.path.join(tested.root, *name.split("/"))) for name in tested.fixture_units]
    cache = cache if cache is not None else open_object_cache("run")
    identity = identity if identity is not None else _toolchain_identity(env)
    flags = _unit_flags(tested)

    def compile_fixture(item):
        name, source_path = item
        object_path = os.path.join(object_dir, registry.object_name("fixture:%s:%s" % (tested.name, name)))
        _compile_cached(cache, identity, flags + ["-O2", "-c"] + includes + ["-o", object_path, source_path],
                        object_path, source_path, env, run_dir, "compile fixture %s/%s" % (tested.name, name))
        return object_path

    objects = process.ordered_map(compile_fixture, sources, jobs)
    fixture_path = os.path.join(run_dir, tested.fixture_library_name)
    _run([GCC_PATH, "-shared", "-static-libgcc"] + FIXTURE_LINK_FLAGS + ["-o", fixture_path] + objects,
         env, run_dir, "fixture link")
    with open(fixture_path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    exported = sorted(_exported_names(fixture_path, env, run_dir))
    entry = {"library": tested.fixture_library_name, "library_sha256": digest,
             "sources": [os.path.relpath(path, REPO_ROOT).replace("\\", "/") for _, path in sources],
             "exported": exported}
    if tested.fixture_units:
        paths = {path for _, path in sources}
        paths.update(os.path.join(tested.fixture_root, name) for name in os.listdir(tested.fixture_root)
                     if name.endswith(".h"))
        paths.update(os.path.join(owner.root, *relative.split("/"))
                     for owner in tested.dependency_closure() + [tested]
                     for _, relative, _ in owner.stem_files() if relative.endswith(".h"))
        entry["inputs"] = []
        for path in sorted(paths):
            with open(path, "rb") as source:
                entry["inputs"].append({"path": os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                                        "sha256": hashlib.sha256(source.read()).hexdigest()})
    image_record = _write_image_record(tested, fixture_path, exported, digest, entry.get("inputs"))
    if image_record is not None:
        entry["image_record"] = image_record
    return entry


def _response_file(path, args):
    """Write the arguments of a cl command to a response file (one quoted argument per line)."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for arg in args:
            handle.write('"%s"\n' % arg.replace('"', '\\"'))
    return "@" + path


def _execute(kind, command, env, cwd, what, context):
    """Run one compile or link command: through the product's `run_command` hook when it defines one
    (for example to observe the command), else directly."""
    hook = _hook("run_command")
    if hook is None:
        return _run(command, env, cwd, what)
    return hook(kind, _gcc_response_command(command, cwd) if command[0] == GCC_PATH else command,
                env, cwd, what, context)


def _hook(name, required=False):
    try:
        return _settings().hook(name, required)
    except maint_config.ConfigError as error:
        raise BuildError(str(error))


def build_library(cell_name, run_id, sanitize=None, accept_tool_drift=False, object_cache_mode="development",
                  jobs=None):
    """Build the Cell library into a new run directory; returns the library path.

    A Cell with `library_from` (its required operations are bound by another Cell) is built as the
    library of that Cell; with `exports` as well it builds its own library over that Cell's binding
    (registry.Cell.library_plan). The library links the Cell, its dependency closure and its
    `link_with` Cells; it exports the public functions of the Cells named by `exports` (default: the
    Cell). `sanitize` ("address" or "undefined") builds the memory-safety verification variant. The
    maintainer tools must match their template manifest unless `accept_tool_drift`.
    `object_cache_mode` is `development`, `run` or an `object_cache.ObjectCache` shared by several
    builds of one run (open_object_cache); `jobs` bounds the compile workers (default: processors)."""
    settings = _settings()
    if sanitize is not None and sanitize not in SANITIZERS:
        raise BuildError("unknown sanitizer %r" % sanitize)
    jobs = process.default_jobs() if jobs is None else jobs
    if not isinstance(jobs, int) or jobs < 1:
        raise BuildError("jobs must be a positive integer, found %r" % (jobs,))
    cache = open_object_cache(object_cache_mode)
    cache_before = cache.statistics(REPO_ROOT)
    try:
        tools = maint_config.maintainer_tools(settings.repo_root, accept_tool_drift, settings.language)
    except maint_config.ConfigError as error:
        raise BuildError(str(error))
    requested = get_cell(cell_name)
    if requested.dependency is not None:
        raise BuildError("Cell %s is a Cell of the dependency %s: it has no library of its own in this repository. "
                         "Its objects are linked into the library of every Cell that depends on it; its tests and "
                         "its ABI belong to its own repository" % (requested.name, requested.dependency))
    cell, linked = requested.library_plan()
    if "windows" not in cell.targets:
        if "arm" in settings.registry["target_names"]:
            raise BuildError("Cell %s has no windows target and no host library: it is verified by "
                             "cross_build.py and gate.py --target arm" % cell.name)
        raise BuildError("Cell %s has no Windows host library" % cell.name)
    if sanitize == "address" and cell.link_fixture:
        raise BuildError("Cell %s links a gcc-built board fixture; its ASan variant is not built" % cell.name)
    if sanitize is not None and any(unit.sdk_dependencies for unit in [cell] + linked):
        raise BuildError("the selected precompiled SDK libraries are not sanitizer-instrumented")
    if not gcc_present():
        raise BuildError("gcc is absent: %s" % GCC_PATH)
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise BuildError("run id %r does not match %s" % (run_id, RUN_ID_PATTERN.pattern))
    # Every Cell whose layout is probed: the exported ones, the built one and every Cell tested on it.
    # An exported dependency Cell has no ABI mirror here: its layout is verified in its own repository.
    probed = []
    for probe_cell in cell.exported_cells() + cell.tested_cells():
        if probe_cell not in probed and probe_cell.dependency is None:
            probed.append(probe_cell)
    for unit_cell in [cell] + linked:
        for name in unit_cell.sources + unit_cell.contract_headers:
            if not os.path.isfile(os.path.join(unit_cell.root, name)):
                raise BuildError("Cell file missing: %s/%s" % (unit_cell.name, name))
    for exported in cell.exported_cells():
        if not os.path.isfile(os.path.join(exported.root, exported.public_header)):
            raise BuildError("Cell file missing: %s/%s" % (exported.name, exported.public_header))

    abis = {probe_cell.name: _load_abi(probe_cell) for probe_cell in probed}

    run_dir = os.path.join(cell.build_root, run_id)
    for part in (os.path.join(REPO_ROOT, "90_temps"), os.path.join(REPO_ROOT, "90_temps", "build")):
        if process.is_link(part):
            raise BuildError("refusing a linked output root: %s" % part)
    os.makedirs(cell.build_root, exist_ok=True)
    if process.is_link(cell.build_root) or os.path.realpath(cell.build_root) != os.path.abspath(cell.build_root):
        raise BuildError("refusing a linked output root: %s" % cell.build_root)
    try:
        os.mkdir(run_dir)
    except FileExistsError:
        raise BuildError("run directory already exists: %s" % run_dir)
    try:
        return _build_in_run(cell, linked, run_id, run_dir, sanitize, cache, cache_before, tools, probed, abis,
                             jobs)
    except (BuildError, RegistryError) as error:
        _write_failure_record(run_dir, run_id, cell, error, cache, cache_before, tools)
        raise


def _write_failure_record(run_dir, run_id, cell, error, cache, cache_before, tools):
    """The build record of a failed run: the reason, its details (paths, expected and found values), the
    Foundation identity when it was resolved, the tools and the cache counts. Writing it never hides the
    original failure."""
    record = {"run_id": run_id, "cell": cell.name, "result": "failed", "error": str(error),
              "details": getattr(error, "details", {}), "maintainer_tools": tools}
    try:
        record["fnd_sdk_standard"] = registry.standard_record(registry._settings(None))
    except Exception as note:  # unreadable metadata is a note in the record, never a second failure
        record["fnd_sdk_standard"] = {"note": str(note)}
    try:
        record["object_cache"] = _cache_delta(cache_before, cache.statistics(REPO_ROOT))
        with open(os.path.join(run_dir, "build_record.json"), "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
    except (OSError, TypeError, ValueError) as note:
        sys.stderr.write("build: note: the failure record could not be written: %s\n" % note)


def _build_in_run(cell, linked, run_id, run_dir, sanitize, cache, cache_before, tools, probed, abis, jobs):
    settings = _settings()
    tmp_dir = os.path.join(run_dir, "tmp")
    os.mkdir(tmp_dir)
    env = _child_env(tmp_dir)
    standard_flag = "-std=%s" % cell.standard
    definitions = cell.definition_flags()
    msvc_env = msvc_paths = None
    if sanitize == "address":
        msvc_env, msvc_paths = msvc_environment(tmp_dir)
    sanitize_gcc = UBSAN_FLAGS if sanitize == "undefined" else []
    msvc_unit_flags = ["/O2", "/Z7", "/MD", "/fsanitize=address"]
    standard_cells = []
    for unit_cell in linked + [cell] + probed:
        if unit_cell not in standard_cells:
            standard_cells.append(unit_cell)
    standard_record = _verify_standard(standard_cells, env, run_dir, msvc_paths, msvc_env)
    # The export list: what the compiler sees each exported public header declare under the Cell's flags.
    functions = _exported_functions(cell.exported_cells(), env, run_dir)

    # ABI probes: compile only (-fsyntax-only), so no object or executable exists. One probe per
    # probed Cell, built with that Cell's own flags and include roots; the probes run in parallel.
    probe_jobs = []
    for probe_cell in probed:
        probe_path = os.path.join(run_dir, "abi_probe.c" if probe_cell is cell else "abi_probe_%s.c" % probe_cell.name)
        with open(probe_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_probe_source(probe_cell, abis[probe_cell.name]))
        probe_includes = ["-I", probe_cell.root]
        for membrane in probe_cell.dependency_membranes():
            probe_includes += ["-I", membrane]
        for root in probe_cell.passive_include_roots() + probe_cell.standard_roots():
            probe_includes += ["-I", root]
        probe_jobs.append((probe_cell, probe_path, probe_includes, _unit_flags(probe_cell)))

    def run_probe(job):
        probe_cell, probe_path, probe_includes, probe_flags = job
        try:
            _run(probe_flags + ["-fsyntax-only"] + probe_includes + _probe_definitions(abis[probe_cell.name])
                 + [probe_path], env, run_dir, "ABI probe of %s (mirror disagrees with the C header)" % probe_cell.name)
        except BuildError as failure:
            raise _abi_mismatch(probe_cell, abis[probe_cell.name],
                                probe_flags + ["-fsyntax-only"] + probe_includes, env, run_dir, failure)
        if msvc_paths is not None:
            # The ASan variant is compiled by cl: its layout is probed by cl too (/Zs: syntax only).
            response = _response_file(os.path.join(tmp_dir, os.path.basename(probe_path)[:-2] + ".msvc.rsp"),
                                      _msvc_unit_command(msvc_paths, probe_cell, probe_includes,
                                                         _probe_definitions(abis[probe_cell.name]) + ["/Zs", probe_path])[1:])
            _run([msvc_paths["cl"], response], msvc_env, run_dir,
                 "cl ABI probe of %s (mirror disagrees with the C header under MSVC)" % probe_cell.name)

    process.ordered_map(run_probe, probe_jobs, jobs)
    identity = _toolchain_identity(env)

    # Product input capture (an SDK receipt) before any Provider library is compiled.
    providers = [unit for unit in [cell] + linked if unit.provider]
    inputs_required = settings.registry["sdk_dependencies"] is not None
    inputs_begin = _hook("build_inputs_begin", inputs_required) if providers else None
    inputs_end = _hook("build_inputs_end", inputs_required) if providers else None
    inputs_token = inputs_begin(cell, run_dir) if inputs_begin is not None else None

    # Export list derived from the public headers only, and the static registration of every Provider Cell
    # the library links, as data, plus the configured alias exports of a Provider's own image.
    aliases = export_aliases(cell, settings)
    def_path = os.path.join(run_dir, cell.definition_file)
    with open(def_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(definition_text(cell, providers, functions, aliases))
    functions = [provider.registration for provider in providers] + functions + [alias for alias, _ in aliases]

    library_path = os.path.join(run_dir, cell.library_name)
    object_dir = os.path.join(tmp_dir, "objects")
    os.mkdir(object_dir)
    objects = []
    defined = {}
    if NM_PATH is None or not os.path.isfile(NM_PATH):
        raise BuildError("nm is absent: cannot verify the external symbols are unique (%s)" % NM_PATH)
    # Linked Cells first, then the Cell itself; each unit is compiled with its own Cell's flags. The units
    # compile on the worker pool; the object list keeps this order, so the link order is the registry's.
    # A product run_command hook observes every real compile: then the units compile one at a time.
    run_hook = _hook("run_command")
    hooked = run_hook is not None
    # A hook that declares `concurrent = True` takes compiles from several workers; `unit_index` (the
    # unit's place in the link order) lets it number its observations deterministically.
    unit_jobs = jobs if not hooked or getattr(run_hook, "concurrent", False) is True else 1
    cell_flags = {unit_cell.name: (_unit_flags(unit_cell), _unit_includes(unit_cell)) for unit_cell in linked + [cell]}
    units = [(unit_cell, name) for unit_cell in linked + [cell] for name in unit_cell.sources]
    unit_index = {(unit_cell.name, name): index for index, (unit_cell, name) in enumerate(units)}

    def compile_unit(job):
        unit_cell, name = job
        unit_flags, unit_includes = cell_flags[unit_cell.name]
        source_path = os.path.join(unit_cell.root, *name.split("/"))
        context = {"cell": unit_cell, "source": source_path, "includes": unit_includes, "flags": unit_flags,
                   "unit_index": unit_index[(unit_cell.name, name)]}
        what = "compile %s/%s" % (unit_cell.name, name)
        if msvc_paths is not None:
            object_path = os.path.join(object_dir, registry.object_name("%s:%s" % (unit_cell.name, name), ".obj"))
            context["object"] = object_path
            _execute("compile", _msvc_unit_command(msvc_paths, unit_cell, unit_includes, msvc_unit_flags
                                                   + ["/c", "/Fo" + object_path, source_path]),
                     msvc_env, run_dir, "cl %s/%s" % (unit_cell.name, name), context)
        else:
            object_path = os.path.join(object_dir, registry.object_name("%s:%s" % (unit_cell.name, name)))
            context["object"] = object_path
            command = unit_flags + OPTIMIZE_FLAGS + sanitize_gcc + ["-c"] + unit_includes + ["-o", object_path, source_path]
            if hooked:
                _execute("compile", command, env, run_dir, what, context)
            else:
                _compile_cached(cache, identity, command, object_path, source_path, env, run_dir, what)
        symbols = _defined_symbols(cache, object_path, env, run_dir, "nm %s/%s" % (unit_cell.name, name))
        if msvc_paths is not None:
            symbols = [symbol for symbol in symbols if not MSVC_COMPILER_SYMBOL.match(symbol)]
        return object_path, symbols

    for (unit_cell, name), (object_path, symbols) in zip(units, process.ordered_map(compile_unit, units, unit_jobs)):
        objects.append(object_path)
        defined["%s/%s" % (unit_cell.name, name)] = symbols
    try:
        symbol_count = registry.require_unique_symbols(defined)
    except RegistryError as error:
        raise BuildError(str(error))
    # The system import libraries every linked Cell names (a library over a binding Cell needs its).
    libraries = []
    for unit_cell in [cell] + linked:
        libraries += ["-l%s" % name for name in unit_cell.link_libraries if "-l%s" % name not in libraries]
    # Precompiled SDK libraries a Cell with SDK dependencies links (host_build.sdk_link_libraries).
    if any(unit.sdk_dependencies for unit in [cell] + linked):
        for library in settings.host_build["sdk_link_libraries"]:
            root = os.environ.get(library["environment"], "")
            if not root or not os.path.isabs(root):
                raise BuildError("%s must name the SDK for %s" % (library["environment"], library["member"]))
            libraries.append(os.path.join(root, *library["member"].split("/")))
    # A fixture that defines the Cell's own required operations (link_fixture) is built first and the
    # library links against it; it is test input and its symbols stay out of the export list.
    fixtures = {}
    if cell.link_fixture:
        fixtures[cell.name] = _build_fixture(cell, cell, run_dir, object_dir, env, cache, identity, jobs)
        libraries.append(os.path.join(run_dir, cell.fixture_library_name))
    runtime_sha256 = None
    link_context = {"cell": cell, "objects": objects, "definition": def_path, "libraries": libraries,
                    "library": library_path}
    if msvc_paths is not None:
        system_libraries = []
        for unit_cell in [cell] + linked:
            system_libraries += ["%s.lib" % name for name in unit_cell.link_libraries
                                 if "%s.lib" % name not in system_libraries]
        link_context["libraries"] = system_libraries
        _execute("link", [msvc_paths["link"], "/nologo", "/DLL", "/INCREMENTAL:NO", "/DEBUG", "/DEF:" + def_path,
                          "/OUT:" + library_path, "/IMPLIB:" + os.path.join(tmp_dir, cell.stem + ".lib"),
                          "/PDB:" + os.path.join(tmp_dir, cell.stem + ".pdb")] + objects + system_libraries,
                 msvc_env, run_dir, "MSVC library link", link_context)
        # The dynamic ASan runtime of the same toolset, beside the library: a process that loads the
        # library resolves it from the library's directory.
        runtime_copy = os.path.join(run_dir, ASAN_RUNTIME)
        with open(msvc_paths["asan_runtime"], "rb") as source, open(runtime_copy, "wb") as target:
            data = source.read()
            target.write(data)
        runtime_sha256 = hashlib.sha256(data).hexdigest()
    else:
        _execute("link", [GCC_PATH, "-shared", "-O2", "-static-libgcc"] + sanitize_gcc + REPRODUCIBLE_LINK_FLAGS
                 + ["-o", library_path, def_path] + objects + libraries,
                 env, run_dir, "library link", link_context)

    exported = _exported_names(library_path, env, run_dir)
    if sorted(exported) != sorted(functions):
        headers = [os.path.join(exported_cell.root, exported_cell.public_header) for exported_cell in cell.exported_cells()]
        extra, missing = sorted(set(exported) - set(functions)), sorted(set(functions) - set(exported))
        raise BuildError("exported symbols of %s differ from the public headers %s: extra=%s missing=%s. Next step: "
                         "define every declared function in the Cell sources (missing) or declare it in the public "
                         "header (extra)." % (library_path, ", ".join(headers), extra, missing),
                         {"export_mismatch": {"library": library_path, "headers": headers, "extra": extra,
                                              "missing": missing}})

    for tested in cell.tested_cells():
        if tested.test_fixture is not None and tested.name not in fixtures:
            fixtures[tested.name] = _build_fixture(tested, cell, run_dir, object_dir, env, cache, identity, jobs)

    stray = [name for name in os.listdir(run_dir)
             if name.lower().endswith((".exe", ".o", ".obj", ".a"))]
    if stray:
        raise BuildError("unexpected build products in the run directory: %s" % stray)

    with open(library_path, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    image_record = _write_cell_image_record(cell.stem, providers, library_path, digest) if providers else None
    if inputs_end is not None:
        inputs_end(inputs_token, cell, library_path, image_record)
    record = {
        "run_id": run_id,
        "cell": cell.name,
        "result": "passed",
        "compiler": msvc_paths["cl"] if msvc_paths is not None else GCC_PATH,
        "depends_on": [dependency.name for dependency in cell.dependency_closure()],
        "linked": [linked_cell.name for linked_cell in linked],
        "exports": [exported_cell.name for exported_cell in cell.exported_cells()],
        "flags": ([c_standard.MSVC_FLAG, "/TC"] + MSVC_WARNING_FLAGS + definitions + msvc_unit_flags
                  if msvc_paths is not None else [standard_flag] + WARNING_FLAGS + definitions + OPTIMIZE_FLAGS + sanitize_gcc),
        "library_sha256": digest,
        "exported": sorted(exported),
        "abi_probe": "passed",
        "c_standard": standard_record,
        "unique_external_symbols": symbol_count,
        "struct_layout": abis[cell.name].struct_layout(),
        "constants": abis[cell.name].CONSTANTS,
        "probed": {probe_cell.name: {"struct_layout": abis[probe_cell.name].struct_layout(),
                                     "constants": abis[probe_cell.name].CONSTANTS}
                   for probe_cell in probed},
        "fixtures": fixtures,
        "gcc_response_files": [json.loads(open(os.path.join(tmp_dir, name), encoding="utf-8").read())
                               for name in sorted(os.listdir(tmp_dir))
                               if name.startswith("gcc_") and name.endswith(".json")],
        "maintainer_tools": tools,
        "object_cache": _cache_delta(cache_before, cache.statistics(REPO_ROOT)),
    }
    if image_record is not None:
        record["image_record"] = image_record
    # The dependency checkouts whose Cells this library links: declared path, pinned commit, observed
    # HEAD and dirty state (metadata for debugging, never a condition of the build).
    dependency_cells = [unit.dependency_record() for unit in linked if unit.dependency is not None]
    if dependency_cells:
        record["dependency_cells"] = dependency_cells
    # The Foundation checkout read by this build: root, pinned commit, observed HEAD and dirty state
    # (metadata for debugging, never a condition of the build).
    standard = registry.standard_record(_settings())
    if standard is not None:
        record["fnd_sdk_standard"] = standard
    if sanitize is not None:
        record["sanitize"] = {"variant": sanitize, "fixtures_sanitized": False}
        if msvc_paths is not None:
            record["sanitize"].update({"toolset": msvc_paths["version"], "abi_probe_msvc": "passed",
                                       "runtime": ASAN_RUNTIME, "runtime_sha256": runtime_sha256})
        else:
            record["sanitize"]["mode"] = "trap"
    evidence = _hook("build_evidence")
    if evidence is not None:
        record = evidence(cell, run_dir, record)
        if not isinstance(record, dict):
            raise BuildError("the build_evidence hook returned no record")
    with open(os.path.join(run_dir, "build_record.json"), "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return library_path


def _requested_cells(args):
    """The Cells named by --cell (in order, once each) or, with --all, every Cell of this repository with a
    windows target (a dependency Cell has no library of its own: registry.authored_cells)."""
    if args.all:
        return [cell for cell in registry.authored_cells(registry.load_registry()["cells"]) if "windows" in cell.targets]
    names = []
    for name in args.cell:
        if name not in names:
            names.append(name)
    return [get_cell(name) for name in names]


def prune_object_cache(max_age_days=object_cache.DEFAULT_MAX_AGE_DAYS, max_bytes=object_cache.DEFAULT_MAX_BYTES,
                       announce=None):
    """Evict unused entries of the development object cache (object_cache.prune); returns the counts."""
    try:
        return object_cache.prune(OBJECT_CACHE_ROOT, REPO_ROOT, max_age_days, max_bytes, announce=announce)
    except (object_cache.CacheError, OSError, RuntimeError) as error:
        raise BuildError("object cache prune failed: %s" % error)


def main(argv):
    parser = argparse.ArgumentParser(description="Build Cells as shared libraries.")
    parser.add_argument("--cell", action="append", default=[], help="Cell name of the registry (repeatable)")
    parser.add_argument("--all", action="store_true", help="every Cell with a windows target")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--run-tests", action="store_true",
                        help="run the Python tests of the Cell against the built library")
    parser.add_argument("--sanitize", default=None,
                        help="memory-safety variant: address (MSVC ASan) or undefined (gcc UBSan, trap mode)")
    parser.add_argument("--accept-tool-drift", action="store_true",
                        help="build although the maintainer tools differ from their template manifest (recorded)")
    parser.add_argument("--jobs", type=int, default=None,
                        help="parallel compile workers (default: the logical processors)")
    parser.add_argument("--no-object-cache", action="store_true",
                        help="reuse objects inside this run only (required for a release candidate build)")
    parser.add_argument("--prune-object-cache", action="store_true",
                        help="evict unused entries of the development object cache, then exit")
    parser.add_argument("--max-age-days", type=float, default=object_cache.DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--max-bytes", type=int, default=object_cache.DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    if args.prune_object_cache:
        if args.cell or args.all:
            parser.error("--prune-object-cache builds nothing; drop --cell and --all")
        try:
            counts = prune_object_cache(args.max_age_days, args.max_bytes, announce=print)
        except BuildError as error:
            sys.stderr.write("build: FAIL: %s\n" % error)
            return 1
        print("object cache: %s" % json.dumps(counts, sort_keys=True))
        return 0
    if bool(args.cell) == bool(args.all):
        parser.error("name the Cells with --cell, or use --all")
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be at least 1")
    run_id = args.run_id or new_run_id()
    built = {}
    libraries = []
    try:
        cells = _requested_cells(args)
        sanitize = parse_sanitize(args.sanitize)
        # One cache for the whole run: a unit that two libraries link is compiled once.
        cache = open_object_cache("run" if args.no_object_cache else "development")
        for cell in cells:
            library_cell = cell.library_plan()[0].name
            if library_cell not in built:
                built[library_cell] = build_library(cell.name, run_id, sanitize, args.accept_tool_drift, cache,
                                                    args.jobs)
            libraries.append((cell, built[library_cell]))
    except (BuildError, RegistryError) as error:
        sys.stderr.write("build: FAIL: %s\n" % error)
        if getattr(error, "details", None):
            sys.stderr.write("build: details: %s\n" % json.dumps(error.details, sort_keys=True, default=str))
        return 1
    for cell, library in libraries:
        print("%s_library: %s" % (cell.stem, library))
    if not args.run_tests:
        return 0
    # The Cells whose tests run: each requested one and, when it carries their library, the bound Cells
    # (not a Cell that owns a library of its own over this one's binding); each Cell's tests run once.
    code = 0
    tested = set()
    for cell, library in libraries:
        for test_cell in cell.tested_cells():
            if test_cell.name in tested:
                continue
            tested.add(test_cell.name)
            env = dict(os.environ)
            env[test_cell.library_environment] = library
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            if sanitize == "address":
                # Stop at the first report; leak detection is not supported by the Windows runtime.
                env.setdefault("ASAN_OPTIONS", "halt_on_error=1:detect_leaks=0")
            try:
                done = subprocess.run([sys.executable, "-I", "-B", "-m", "unittest", "discover", "-v",
                                       "-s", test_cell.tests_root, "-p", test_cell.test_pattern],
                                      env=env, stdin=subprocess.DEVNULL, timeout=TOOL_TIMEOUT_SECONDS * 2,
                                      creationflags=process.window_flags())
            except subprocess.TimeoutExpired:
                sys.stderr.write("build: FAIL: tests of %s timed out\n" % test_cell.name)
                return 1
            code = max(code, done.returncode)
    return code
