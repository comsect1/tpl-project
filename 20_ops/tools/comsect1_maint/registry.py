# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Reader of a product's Cell registry (`maint_config.json` `registry.path`, usually `20_ops/tools/cells.json`).

Python standard library only. Language `c`: the registry is the single list of
Cells, per Cell the path, the language standard, the definitions, the include
roots, the dependencies, the freestanding flag and the supported targets. The
standard is the one token of `c_standard` (ISO/IEC 9899:2018), nothing else.
Everything else follows from the layout rule (`<prefix>` is the product's
`symbol_prefix`, `<PREFIX>` its `environment_prefix`):

    Cell root        10_pkg/c/<path>        (<path> is <name>, or <category>/<name>)
    Python tests     10_pkg/test/c/<path>   (<prefix>_<name>_abi.py, test_<prefix>_<name>_*.py)
    public header    the one membrane/xpt_*.h (product suffix exclusions apply)
    public functions <prefix>_<name>_* of the public header, or <function_prefix>* when the
                     Cell declares `function_prefix` (public names that are an external
                     compatibility surface and carry no product prefix)
    sources          every .c below the Cell root
    contract headers every contract/*.h
    library          <prefix>_<name>.dll, found through <PREFIX>_<NAME>_LIBRARY

A Provider Cell lives at `provider/<name>`; its stem `<prefix>_provider_<name>`
names the ABI mirror, the tests, the test image, the static registration
`<prefix>_provider_<name>_registration` and its variable. It is freestanding,
depends only on library Cells and declares the cross-build `budget` of its cores
(always, or only when cross-built: `registry.provider_budget_required`). A Cell
whose `comsect1.json` names `fnd-sdk-standard` among its hosted vocabularies
reads `comsect1_provider.h` of the Foundation checkout (`standard_include_root`).

A consumed checkout (the Foundation of `host_build.standard_pin`, an adopted
`external_headers` tree) is never locked: its pinned commit is recorded
metadata. The Foundation is found at the pin's `root_variable` (an override) or
else its `deps_path`, the path of the Git submodule that `.gitmodules` declares
below `10_pkg/deps/`; the pin holds no machine path outside the repository. An
`external_headers` checkout is found only through its environment variable. A
consumed checkout inside the repository lies below `10_pkg/deps/`, and no
consumed checkout path holds a link or reparse component. The tools resolve the
checkout, read its HEAD and dirty state when it is its own Git work tree, record
them beside the pinned commit (`standard_checkout`, `Cell.external_checkout`)
and never refuse on a mismatch or a dirty tree. The object cache keys every
compile by the bytes of the headers actually read, so correctness never rests
on the pin.

A dependency Cell is a Cell of a declared dependency that this product compiles
into its own libraries and archives from where it is: an entry with the key
`dependency`, the path of the Git submodule that `.gitmodules` declares below
`10_pkg/deps/`. Nothing is copied and nothing is written below the checkout.

    Cell root        <dependency>/10_pkg/c/<path>
    identity         the one `public_identity_surfaces` entry of the Cell's own `comsect1.json`:
                     `identity` is the stem, `path` the public header (membrane/xpt_*.h)
    public functions <identity>_* of that header, or <function_prefix>* when the entry declares it
    sources          every .c below the Cell root
    archive          lib<identity>.a (the dependency's own name, without the product prefix)
    library, tests   none: its objects are linked into the libraries of the Cells that depend on
                     it; its tests, ABI mirror and Gate verdict belong to its own repository

The entry holds the keys every Cell holds (the consumer's flags: `standard`,
`definitions`, `include_roots`, `freestanding`, `targets`) and may hold
`function_prefix` and `budget`; every other optional key is refused. The
manifest of the dependency Cell must name the language `c`; a Cell manifest
carries no edition of the standard, so the entry carries the one token of
`c_standard` like every Cell and the edition is verified by value under the
consumer's flags. A dependency Cell depends only on dependency Cells and is no
Provider. The registry and the configuration of the dependency's own repository
are never read. The checkout is resolved at the declared submodule path only
(no override variable); its HEAD, dirty state and the commit of the pin under
`10_pkg/components/` that names the same `deps_path` are recorded
(`Cell.dependency_record`), never a condition. `Cell.dependency_deliverables`
names what a product ships of it under the dependency's own names.

Every Cell name and path segment is one lowercase word. Across the Cells no two
C file stems may be equal (a stem lives in one folder of one Cell; the only
exception is a header-only stem whose copies carry one include guard), and no
two compiled units may define the same external-linkage symbol
(`require_unique_symbols`, applied by host_build and cross_build).

Language `rust`: Cells at `10_pkg/rust/<path>` (crate root, `cell/` holds
`comsect1.json`), keys `path`, `edition`, `depends_on` (`RustCell`).

Any deviation fails closed with a RegistryError.
"""

import hashlib
import json
import os
import re
import subprocess

from . import c_standard
from . import config as _config
from . import path_guard as _guard
from . import process

TOOLS_ROOT = process.TOOLS_ROOT
REPO_ROOT = process.REPO_ROOT
RUN_ID_PATTERN = process.RUN_ID_PATTERN
DEPS_FOLDER = process.DEPS_FOLDER
# One lowercase word: a Cell name and each segment of a Cell path.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9]{0,31}")
EDITION_PATTERN = re.compile(r"20[0-9]{2}")
# function_prefix of a Cell: lowercase words joined and ended by an underscore (horizon_, gate_port_).
FUNCTION_PREFIX_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*_")

CELL_KEYS = {"path", "standard", "definitions", "include_roots", "depends_on", "freestanding", "targets"}
# Optional keys of a Cell (see Cell below); sdk_dependencies only when the product configures them.
OPTIONAL_CELL_KEYS = {"library_from", "link_with", "link_libraries", "exports", "hosted_include_roots",
                      "test_fixture", "fixture_units", "link_fixture", "external_headers", "budget",
                      "passive_include_roots", "function_prefix"}
# dependency of a dependency Cell: the path of the declared submodule that holds the Cell. Its entry
# declares none of the keys below: tests, fixtures, bindings, exports of its own library and adopted
# vocabulary belong to the dependency's own repository.
DEPENDENCY_KEY = "dependency"
DEPENDENCY_REFUSED_KEYS = {"library_from", "link_with", "link_libraries", "exports", "hosted_include_roots",
                           "test_fixture", "fixture_units", "link_fixture", "external_headers",
                           "passive_include_roots", "sdk_dependencies"}
# The folder of the C Cells below a repository root, of this repository and of a dependency alike.
CELL_FOLDER = ("10_pkg", "c")
# The manifest of a dependency Cell: the language it must name, its public identity (lowercase words
# joined by an underscore) and its public header, a membrane/xpt_*.h path relative to the Cell root.
MANIFEST_FILE = "comsect1.json"
C_LANGUAGE = "c"
IDENTITY_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
PUBLIC_HEADER_PATTERN = re.compile(r"membrane/xpt_[a-z0-9_]+\.h")
# The licence text and the notices at the root of a dependency checkout (the names every repository of
# the template carries); the licence is required of a shipped dependency, the notices ship when present.
LICENSE_FILE = "LICENSE.md"
NOTICE_FILE = "NOTICE.md"
# The folder of the pin files; a pin describes the dependency whose submodule path its deps_path names.
COMPONENTS_FOLDER = ("10_pkg", "components")
RUST_CELL_KEYS = {"path", "edition", "depends_on"}
# The category folder of the Provider Cells (provider/<name>).
PROVIDER_CATEGORY = "provider"
# budget: the cross-build size budget of every cross core, in bytes of the Cell's own objects.
BUDGET_KEYS = {"text", "data", "bss"}
# external_headers of a Cell that adopts an external header tree (a firmware kernel), read only, a
# declared dependency reached only through its environment variable (outside this repository, or a
# submodule below 10_pkg/deps/): the variable naming the checkout, the commit it was pinned at
# (recorded metadata, never a lock), the include roots of every core and of the Gate's core, and
# repository folders of build inputs (a kernel configuration).
EXTERNAL_HEADER_KEYS = {"environment", "commit", "include_roots", "core_include_roots", "gate_core",
                        "config_roots"}
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
TARGET_KEYS = {"windows": {"gate"}, "arm": {"gate", "cross_cores"}}
GATE_KEYS = {"target", "plain_char"}

_PRESENT = _config.present()
_SETTINGS = _config.current() if _PRESENT else None
# The product view of this repository (None in the template, which has no configuration).
REGISTRY_PATH = _SETTINGS.registry_path() if _SETTINGS else None
TARGET_NAMES = tuple(_SETTINGS.registry["target_names"]) if _SETTINGS and _SETTINGS.language == "c" \
    else _config.TARGET_NAMES
ENVIRONMENT_PATTERN = re.compile(r"%s_[A-Z][A-Z0-9_]{0,62}" % _SETTINGS.environment_prefix) if _SETTINGS else None
STANDARD_PIN_PATH = _SETTINGS.path_of(_SETTINGS.host_build["standard_pin"]) \
    if _SETTINGS and _SETTINGS.host_build else None


class RegistryError(Exception):
    """The registry or a Cell layout is invalid; the message is the reason. `details` (a dict) holds the
    resolved paths and the recorded identity of a failed resolution for the build or run record."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def _require(condition, message):
    if not condition:
        raise RegistryError(message)


def _keys(section, expected, what):
    _require(isinstance(section, dict), "%s must be an object" % what)
    _require(set(section) == set(expected), "%s must hold exactly %s, found %s"
             % (what, sorted(expected), sorted(section)))


def _settings(settings):
    if settings is not None:
        return settings
    try:
        return _config.current()
    except _config.ConfigError as error:
        raise RegistryError(str(error))


def checked_original_path(path):
    """The resolved path after refusing a linked or reparse component on the way (str)."""
    try:
        return str(_guard.checked_path(path))
    except (OSError, ValueError) as error:
        raise RegistryError("unsafe input path %s: %s" % (path, error)) from error


class Cell:
    """One validated C registry entry plus the paths its name implies."""

    def __init__(self, name, entry, cores, settings=None):
        settings = _settings(settings)
        self.settings = settings
        policy = settings.registry
        prefix = settings.symbol_prefix
        target_names = tuple(policy["target_names"])
        optional = OPTIONAL_CELL_KEYS | {DEPENDENCY_KEY} \
            | ({"sdk_dependencies"} if policy["sdk_dependencies"] is not None else set())
        _require(NAME_PATTERN.fullmatch(name) is not None, "Cell name %r is not one lowercase word" % name)
        _require(isinstance(entry, dict) and CELL_KEYS <= set(entry) <= CELL_KEYS | optional,
                 "Cell %s must hold %s and may hold %s, found %s"
                 % (name, sorted(CELL_KEYS), sorted(optional), sorted(entry) if isinstance(entry, dict) else entry))
        self.name = name
        # A dependency Cell (DEPENDENCY_KEY): the declared submodule path that holds it, else None.
        self.dependency = entry.get(DEPENDENCY_KEY)
        if DEPENDENCY_KEY in entry:
            _require_dependency_path(name, self.dependency)
            refused = sorted(DEPENDENCY_REFUSED_KEYS & set(entry))
            _require(not refused, "Cell %s: a dependency Cell declares no %s: its tests, fixtures, bindings and "
                     "adopted vocabulary belong to its own repository (%s %r)"
                     % (name, ", ".join(refused), DEPENDENCY_KEY, self.dependency))
        self.path = entry["path"]
        segments = self.path.split("/") if isinstance(self.path, str) else []
        _require(segments and all(NAME_PATTERN.fullmatch(s) is not None for s in segments)
                 and segments[-1] == name and len(segments) <= 2,
                 "Cell %s: path %r must be %s or <category>/%s, one lowercase word per segment"
                 % (name, self.path, name, name))
        self.standard = entry["standard"]
        _require(self.standard == c_standard.C_STANDARD, c_standard.token_refusal("Cell %s" % name, self.standard))
        self.definitions = entry["definitions"]
        _require(isinstance(self.definitions, dict)
                 and all(isinstance(k, str) and isinstance(v, str) for k, v in self.definitions.items()),
                 "Cell %s: definitions must map names to strings" % name)
        self.include_roots = entry["include_roots"]
        _require(isinstance(self.include_roots, list) and self.include_roots
                 and all(isinstance(v, str) for v in self.include_roots),
                 "Cell %s: include_roots must be a non-empty list of strings" % name)
        self.depends_on = entry["depends_on"]
        _require(isinstance(self.depends_on, list) and all(isinstance(v, str) for v in self.depends_on)
                 and len(set(self.depends_on)) == len(self.depends_on) and name not in self.depends_on,
                 "Cell %s: depends_on must be a list of other Cell names without duplicates" % name)
        self.sdk_dependencies = entry.get("sdk_dependencies", [])
        sdk = policy["sdk_dependencies"]
        if sdk is not None:
            required = list(sdk["allowed"]) if self.path.startswith(sdk["required_for_path_prefix"]) else []
            _require(self.sdk_dependencies == required,
                     "Cell %s: Cells below %s declare exactly the SDK dependencies %s, others none"
                     % (name, sdk["required_for_path_prefix"], sdk["allowed"]))
        self.dependencies = []  # Cell objects, filled by load_registry once every Cell exists
        # A Cell that cannot be built alone (its required operations are bound by another Cell)
        # names the Cell whose library carries its tests; with exports as well, it instead builds a
        # library of its own over that Cell's binding (library_plan); link_with adds Cells to the
        # test library beyond the dependencies; exports lists the Cells whose public header the
        # library exports (default: this Cell); link_libraries are system import libraries;
        # hosted_include_roots are absolute directories of an external SDK the native Gate lane
        # reads (never edited); passive_include_roots are repository folders of declared passive
        # vocabulary the Cell's units include (no publication authority implied).
        self.library_from = entry.get("library_from")
        self.link_with_names = entry.get("link_with", [])
        self.link_libraries = entry.get("link_libraries", [])
        self.export_names = entry.get("exports")
        self.hosted_include_roots = entry.get("hosted_include_roots", [])
        self.passive_roots = entry.get("passive_include_roots", [])
        for key, value in (("link_with", self.link_with_names), ("link_libraries", self.link_libraries),
                           ("hosted_include_roots", self.hosted_include_roots),
                           ("passive_include_roots", self.passive_roots)):
            _require(isinstance(value, list) and all(isinstance(v, str) and v for v in value)
                     and len(set(value)) == len(value), "Cell %s: %s must be a list of distinct strings" % (name, key))
        _require(self.library_from is None or (isinstance(self.library_from, str) and self.library_from != name),
                 "Cell %s: library_from must name another Cell" % name)
        _require(self.export_names is None or (isinstance(self.export_names, list) and self.export_names
                 and all(isinstance(v, str) for v in self.export_names)
                 and len(set(self.export_names)) == len(self.export_names)),
                 "Cell %s: exports must be a non-empty list of distinct Cell names" % name)
        for root in self.hosted_include_roots:
            _require(os.path.isabs(root), "Cell %s: hosted_include_roots entry %r must be absolute" % (name, root))
        for root in self.passive_roots:
            _require(not os.path.isabs(root) and ":" not in root and "\\" not in root
                     and all(part not in ("", ".", "..") for part in root.split("/"))
                     and os.path.isdir(os.path.join(settings.repo_root, *root.split("/"))),
                     "Cell %s: passive_include_roots entry %r must be a repository folder" % (name, root))
        # test_fixture names a folder of the Cell's tests holding the C sources of a test-only library,
        # built by host_build beside the library that carries the Cell's tests. It is test input, never
        # a Cell and never linked into a product.
        self.test_fixture = entry.get("test_fixture")
        _require(self.test_fixture is None
                 or (isinstance(self.test_fixture, str) and NAME_PATTERN.fullmatch(self.test_fixture) is not None),
                 "Cell %s: test_fixture must be one lowercase word" % name)
        self.link_with = []  # Cell objects, filled by load_registry
        self.freestanding = entry["freestanding"]
        _require(isinstance(self.freestanding, bool), "Cell %s: freestanding must be true or false" % name)
        targets = entry["targets"]
        _require(isinstance(targets, dict) and targets and set(targets) <= set(target_names),
                 "Cell %s: targets must hold windows, arm or both" % name if set(target_names) == {"windows", "arm"}
                 else "Cell %s: targets must contain only %s" % (name, ", ".join(target_names)))
        self.targets = {}
        for target, section in targets.items():
            _keys(section, TARGET_KEYS[target], "Cell %s target %s" % (name, target))
            _keys(section["gate"], GATE_KEYS, "Cell %s target %s gate" % (name, target))
            _require(section["gate"]["plain_char"] in ("signed", "unsigned"),
                     "Cell %s target %s: plain_char must be signed or unsigned" % (name, target))
            for core in section.get("cross_cores", []):
                _require(core in cores, "Cell %s: cross core %r is not in cores" % (name, core))
            self.targets[target] = section
        if self.cross_cores():
            _require(self.freestanding, "Cell %s: cross-build needs freestanding true" % name)

        repo = settings.repo_root
        # A Provider (provider/<name>) keeps every public symbol in <prefix>_provider_<name>_.
        self.provider = name if len(segments) == 2 and segments[0] == PROVIDER_CATEGORY else None
        if self.dependency is None:
            self.dependency_checkout = None
            self.root = os.path.join(repo, *CELL_FOLDER, *segments)
            self.tests_root = os.path.join(repo, "10_pkg", "test", "c", *segments)
            self.stem = "%s_provider_%s" % (prefix, name) if self.provider else "%s_%s" % (prefix, name)
            self.abi_path = os.path.join(self.tests_root, "%s_abi.py" % self.stem)
            self.test_pattern = "test_%s_*.py" % self.stem
            self.library_name = "%s.dll" % self.stem
            self.definition_file = "%s.def" % self.stem
            self.library_environment = "%s_LIBRARY" % self.stem.upper()
            published_header = None
        else:
            # The Cell of a dependency, read where it is: the declared submodule folder (its real path,
            # checked, is dependency_checkout), the identity and the public header its own manifest
            # publishes. It has no library, ABI mirror, test or library variable in this repository.
            _require(not self.provider, "Cell %s: a dependency Cell is no Provider: a Provider is a Cell of the "
                     "product whose family it serves (path %r)" % (name, self.path))
            self.dependency_checkout = dependency_checkout(self.dependency, settings, name)
            self.root = os.path.join(settings.path_of(self.dependency), *CELL_FOLDER, *segments)
            require_unlinked_cell(name, self.dependency, settings.path_of(self.dependency), self.root)
            manifest_path = os.path.join(self.root, MANIFEST_FILE)
            _require(os.path.isfile(manifest_path),
                     "Cell %s: %s has no %s: the dependency %s declares no Cell at %s. Next step: correct the path to "
                     "a Cell of the dependency, or select a commit of the dependency that holds it."
                     % (name, self.root, MANIFEST_FILE, self.dependency, "/".join(CELL_FOLDER + tuple(segments))))
            self.stem, published_header = _dependency_publication(manifest_path, name)
            self.tests_root = self.abi_path = self.test_pattern = None
            self.library_name = self.definition_file = self.library_environment = None
        self.registration = self.stem + "_registration" if self.provider else None
        self.archive_name = "lib%s.a" % self.stem
        # The prefix of every function the public header declares and the library exports: the stem,
        # unless the Cell declares the prefix of its public names (function_prefix).
        self.function_prefix = entry.get("function_prefix", self.stem + "_")
        _require(isinstance(self.function_prefix, str)
                 and FUNCTION_PREFIX_PATTERN.fullmatch(self.function_prefix) is not None,
                 "Cell %s: function_prefix %r must be lowercase words joined and ended by an underscore"
                 % (name, self.function_prefix))
        _require(not self.provider or "function_prefix" not in entry,
                 "Cell %s: a Provider Cell keeps every public symbol in %s_ and declares no function_prefix"
                 % (name, self.stem))
        self.build_root = os.path.join(repo, "90_temps", "build", name)
        self.budget = entry.get("budget")
        if self.provider:
            # A port Provider requires operations its target platform defines: it names that platform's
            # Cell as library_from and is tested on its library; any other Provider builds its own image.
            _require(self.freestanding
                     and self.export_names is None and not self.link_with_names and not self.link_libraries
                     and not self.hosted_include_roots and entry.get("test_fixture") is None,
                     "Cell %s: a Provider Cell is freestanding, depends only on library Cells and builds its own "
                     "test image or names the platform Cell whose library binds its required operations" % name)
            if policy["provider_budget_required"] == "always":
                _require(self.budget is not None, "Cell %s: a Provider Cell declares its cross-build budget" % name)
            elif self.cross_cores():
                _require(self.budget is not None, "Cell %s: a cross-built Provider declares its budget" % name)
        if self.budget is not None:
            _require(isinstance(self.budget, dict) and set(self.budget) == set(self.cross_cores())
                     and self.budget,
                     "Cell %s: budget must name exactly the cross cores %s" % (name, self.cross_cores()))
            for core, limits in self.budget.items():
                _keys(limits, BUDGET_KEYS, "Cell %s budget %s" % (name, core))
                _require(all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in limits.values()),
                         "Cell %s budget %s: sizes must be non-negative integers" % (name, core))
        _require(os.path.isfile(os.path.join(self.root, MANIFEST_FILE)),
                 "Cell %s: %s has no %s" % (name, self.root, MANIFEST_FILE))
        # A Cell that declares the fnd-sdk-standard as a hosted vocabulary includes comsect1_provider.h
        # of the pinned checkout (standard_roots).
        self.adopts_standard = _declares_standard(os.path.join(self.root, MANIFEST_FILE))
        self.sources = self._sources()
        if self.dependency is None:
            self.public_header = self._public_header(allow_none=self.export_names is not None
                                                    or self.library_from is not None)
            self.contract_headers = self._contract_headers()
        else:
            # The owner's layout is not judged here: the published header must exist, nothing more.
            self.public_header = published_header
            _require(os.path.isfile(os.path.join(self.root, *published_header.split("/"))),
                     "Cell %s: the public header %s is missing below %s, although %s of the dependency publishes it. "
                     "Next step: select a commit of the dependency %s that holds it."
                     % (name, published_header, self.root, MANIFEST_FILE, self.dependency))
            self.contract_headers = self._contract_headers(required=False)
        self.fixture_root = None
        self.fixture_sources = []
        self.fixture_library_name = None
        if self.test_fixture is not None:
            self.fixture_root = os.path.join(self.tests_root, self.test_fixture)
            self.fixture_sources = sorted(n for n in os.listdir(self.fixture_root) if n.endswith(".c")) \
                if os.path.isdir(self.fixture_root) else []
            _require(self.fixture_sources, "Cell %s: test_fixture %s holds no .c file" % (name, self.fixture_root))
            self.fixture_library_name = "%s_%s_%s.dll" % (prefix, name, self.test_fixture)
        # link_fixture: the library of this Cell links against its fixture library, which defines the
        # operations the Cell requires (a board double of a platform Cell). Test input only.
        self.link_fixture = entry.get("link_fixture", False)
        _require(isinstance(self.link_fixture, bool) and (not self.link_fixture or self.test_fixture is not None),
                 "Cell %s: link_fixture must be true or false and needs a test_fixture" % name)
        # A unit fixture compiles selected actual owner units with test effects. It can see this Cell's
        # private headers, but is never a product input.
        self.fixture_units = entry.get("fixture_units", [])
        _require(isinstance(self.fixture_units, list)
                 and all(isinstance(unit, str) and unit in self.sources for unit in self.fixture_units)
                 and len(set(self.fixture_units)) == len(self.fixture_units),
                 "Cell %s: fixture_units must name distinct current Cell source units" % name)
        _require(not self.fixture_units or (self.test_fixture is not None and not self.link_fixture),
                 "Cell %s: fixture_units needs a separate test_fixture and cannot link into the product" % name)
        self.external_headers = entry.get("external_headers")
        if self.external_headers is not None:
            self._check_external_headers(cores)

    def _check_external_headers(self, cores):
        section = self.external_headers
        prefix = self.settings.environment_prefix
        _keys(section, EXTERNAL_HEADER_KEYS, "Cell %s external_headers" % self.name)
        _require(isinstance(section["environment"], str)
                 and re.fullmatch(r"%s_[A-Z][A-Z0-9_]{0,62}" % prefix, section["environment"]),
                 "Cell %s: external_headers.environment must be a %s_* variable name" % (self.name, prefix))
        _require(isinstance(section["commit"], str) and COMMIT_PATTERN.fullmatch(section["commit"]),
                 "Cell %s: external_headers.commit must be a full commit id" % self.name)
        relative = [section["include_roots"]] + list(section["core_include_roots"].values()) \
            if isinstance(section["core_include_roots"], dict) else []
        _require(relative and all(isinstance(v, list) and all(isinstance(r, str) and r and not os.path.isabs(r)
                                                              and ".." not in r.split("/") for r in v)
                                  for v in relative),
                 "Cell %s: external_headers include roots must be relative to the checkout" % self.name)
        _require(set(section["core_include_roots"]) == set(self.cross_cores())
                 and all(core in cores for core in section["core_include_roots"]),
                 "Cell %s: external_headers.core_include_roots must name exactly the cross cores" % self.name)
        _require(section["gate_core"] in section["core_include_roots"],
                 "Cell %s: external_headers.gate_core must be one of the cross cores" % self.name)
        repo = self.settings.repo_root
        _require(isinstance(section["config_roots"], list)
                 and all(isinstance(r, str) and os.path.isdir(os.path.join(repo, *r.split("/")))
                         and ".." not in r.split("/") and r.startswith("10_pkg/test/") for r in section["config_roots"]),
                 "Cell %s: external_headers.config_roots must be folders below 10_pkg/test" % self.name)

    def external_roots(self, core):
        """(absolute include roots of the external checkout for the core, absolute config roots).

        The checkout is read only from the named environment variable: a folder outside this
        repository or a dependency submodule below 10_pkg/deps/ (dependency_checkout_root). Its commit
        and dirty state are recorded (external_checkout), never required."""
        section = self.external_headers
        if section is None:
            return [], []
        repo_root = self.settings.repo_root
        checkout = self._external_checkout_root()
        roots = []
        for relative in section["include_roots"] + section["core_include_roots"][core]:
            path = os.path.join(checkout, *relative.split("/"))
            if not os.path.isdir(path):
                observed = observed_checkout(checkout)
                raise RegistryError(
                    "Cell %s: external include root %s is missing in the checkout %s (from %s; HEAD %s, dirty %s, "
                    "pinned commit %s). Next step: select a checkout of that header tree that holds %s."
                    % (self.name, path, checkout, section["environment"], observed["head"], observed["dirty"],
                       section["commit"], relative),
                    dict(observed, root=checkout, environment=section["environment"], pinned_commit=section["commit"],
                         missing=path))
            roots.append(path)
        configs = [os.path.join(repo_root, *r.split("/")) for r in section["config_roots"]]
        return roots, configs

    def _external_checkout_root(self):
        variable = self.external_headers["environment"]
        checkout = os.environ.get(variable)
        details = {"environment": variable, "value": checkout, "pinned_commit": self.external_headers["commit"]}
        if not checkout or not os.path.isdir(checkout):
            raise RegistryError(
                "Cell %s: the checkout of its external headers is not found: %s is %s (pinned commit %s, "
                "recorded metadata). Next step: set %s to a checkout of that header tree that holds %s (a "
                "dependency submodule below %s/ or a folder outside this repository)."
                % (self.name, variable, "unset" if not checkout else "%s, not a directory" % checkout,
                   self.external_headers["commit"], variable, ", ".join(self.external_headers["include_roots"]),
                   "/".join(DEPS_FOLDER)),
                details)
        return dependency_checkout_root(checkout, self.settings.repo_root,
                                        "Cell %s: the external header checkout" % self.name,
                                        "from %s" % variable, details)

    def external_checkout(self):
        """The recorded identity of the external header checkout (None without external_headers):
        its root and variable, the pinned commit of the registry, the observed HEAD and dirty state,
        and whether HEAD equals the pin. Metadata only; a mismatch or a dirty tree is never refused."""
        if self.external_headers is None:
            return None
        checkout = self._external_checkout_root()
        observed = observed_checkout(checkout)
        return dict(observed, root=checkout, environment=self.external_headers["environment"],
                    pinned_commit=self.external_headers["commit"],
                    matches_pin=observed["head"] == self.external_headers["commit"])

    def cross_cores(self):
        return list(self.targets.get("arm", {}).get("cross_cores", []))

    def standard_roots(self):
        """The hosted vocabulary roots: [the Foundation's fnd-sdk-standard include directory] for a Cell
        whose comsect1.json declares the standard, then, for a Cell with SDK dependencies, the selected
        SDK's include directory and its shell membrane. Fails closed on an unresolvable checkout or SDK
        selection, never on the pinned commit."""
        roots = [standard_include_root(self.settings)] if self.adopts_standard else []
        if self.sdk_dependencies:
            sdk_policy = self.settings.registry["sdk_dependencies"]
            variable = sdk_policy["root_environment"]
            sdk = os.environ.get(variable, "")
            _require(sdk and os.path.isabs(sdk), "%s must be an absolute SDK directory" % variable)
            sdk = checked_original_path(sdk)
            include = os.path.join(sdk, "include")
            _require(os.path.isfile(os.path.join(include, sdk_policy["vocabulary_header"])),
                     "%s must identify an SDK with the family vocabulary %s" % (variable, sdk_policy["vocabulary_header"]))
            membrane = os.path.join(include, *sdk_policy["shell_membrane"].split("/"))
            _require(os.path.isfile(membrane), "%s must contain the shell public membrane %s"
                     % (variable, sdk_policy["shell_membrane"]))
            roots.extend([checked_original_path(include), checked_original_path(os.path.dirname(membrane))])
        return roots

    def passive_include_roots(self):
        """Owner source inputs for declared passive vocabulary (cells.json passive_include_roots), absolute."""
        return [os.path.join(self.settings.repo_root, *root.split("/")) for root in self.passive_roots]

    def dependency_closure(self):
        """Every Cell this Cell depends on, direct or not, in dependency order, without repeats."""
        ordered = []
        for dependency in self.dependencies:
            for cell in dependency.dependency_closure() + [dependency]:
                if cell not in ordered:
                    ordered.append(cell)
        return ordered

    def linked_cells(self):
        """Every other Cell whose sources the library of this Cell links, in link order, without repeats:
        the dependency closure, then the link_with Cells with their own closures."""
        ordered = []
        for cell in self.dependency_closure():
            if cell not in ordered:
                ordered.append(cell)
        for extra in self.link_with:
            for cell in extra.dependency_closure() + [extra]:
                if cell not in ordered and cell is not self:
                    ordered.append(cell)
        return ordered

    def exported_cells(self):
        """The Cells whose public header the library of this Cell exports, in declaration order."""
        if self.export_names is None:
            return [self]
        return [self.registry_cells[n] for n in self.export_names]

    def owns_library(self):
        """True for a Cell with library_from and exports: it builds a library of its own name over the
        binding of the library_from Cell, instead of being tested on that Cell's library."""
        return self.library_from is not None and self.export_names is not None

    def library_plan(self):
        """(Cell whose name the library carries, other Cells linked into it, in link order) for
        `build.py --cell <this>`.

        A Cell without library_from builds its own library over its links. A Cell with library_from
        and no exports is tested on that Cell's library, whatever that library links. A Cell that owns
        its library (owns_library) links itself and its links, then the library_from Cell with its
        links, which define the required operations the freestanding Cells leave open."""
        if self.library_from is None:
            return self, self.linked_cells()
        source = self.registry_cells[self.library_from]
        if not self.owns_library():
            return source.library_plan()
        linked = []
        for cell in self.linked_cells() + source.linked_cells() + [source]:
            if cell is not self and cell not in linked:
                linked.append(cell)
        return self, linked

    def tested_cells(self):
        """The Cells whose tests run on the library this Cell's name carries: this Cell, then every
        Cell tested on it (library_from naming it, without a library of its own), in registry order."""
        return [self] + [other for other in self.registry_cells.values()
                         if other.library_from == self.name and not other.owns_library()]

    def dependency_membranes(self):
        """Membrane directories of the dependency closure, absolute and in order: the only part of a
        dependency a consumer gets as an include root."""
        return [os.path.join(cell.root, "membrane") for cell in self.dependency_closure()]

    def external_include_roots(self):
        """The dependency membranes as repository-relative paths, for the Gate configuration."""
        repo = self.settings.repo_root
        return [os.path.relpath(path, repo).replace("\\", "/") for path in self.dependency_membranes()]

    def definition_flags(self):
        return ["-D%s" % k if v == "1" else "-D%s=%s" % (k, v) for k, v in sorted(self.definitions.items())]

    def _sources(self):
        found = []
        for directory, names, files in os.walk(self.root):
            names.sort()
            for file_name in sorted(files):
                if file_name.endswith(".c"):
                    found.append(os.path.relpath(os.path.join(directory, file_name), self.root).replace("\\", "/"))
        _require(found, "Cell %s: no .c file below %s" % (self.name, self.root))
        return sorted(found)

    def _public_header(self, allow_none=False):
        membrane = os.path.join(self.root, "membrane")
        exclusions = tuple(self.settings.registry["membrane_header_exclusions"])
        found = sorted(n for n in os.listdir(membrane) if n.startswith("xpt_") and n.endswith(".h")
                       and not (exclusions and n.endswith(exclusions))) if os.path.isdir(membrane) else []
        if allow_none and not found:
            return None  # a Cell that binds another Cell's required operations publishes none
        _require(len(found) == 1, "Cell %s: membrane must hold exactly one xpt_*.h, found %s" % (self.name, found))
        return "membrane/" + found[0]

    def stem_files(self):
        """Every .c and .h file of the Cell as (cell, relative path, include guard or None)."""
        found = []
        for directory, names, files in os.walk(self.root):
            names.sort()
            for file_name in sorted(files):
                if file_name.endswith((".c", ".h")):
                    path = os.path.join(directory, file_name)
                    guard = _include_guard(path) if file_name.endswith(".h") else None
                    found.append((self.name, os.path.relpath(path, self.root).replace("\\", "/"), guard))
        return found

    def _contract_headers(self, required=True):
        contract = os.path.join(self.root, "contract")
        found = sorted(n for n in os.listdir(contract) if n.endswith(".h")) if os.path.isdir(contract) else []
        _require(found or not required, "Cell %s: contract holds no header" % self.name)
        return ["contract/" + n for n in found]

    def dependency_record(self):
        """The recorded identity of a dependency Cell's checkout (None for a Cell of this repository):
        the Cell and its published identity, the declared submodule path, the checkout and Cell roots,
        the pin file under 10_pkg/components/ that names the same deps_path with its commit (None
        without exactly one such pin; pin_note says why when the folder is unreadable or several
        pins name the path), the observed HEAD, dirty state and note, and whether HEAD equals the pin. Metadata only: a mismatch or a dirty tree is never refused."""
        if self.dependency is None:
            return None
        observed = observed_checkout(self.dependency_checkout)
        pin_path, pinned, pin_note = _dependency_pin(self.settings, self.dependency)
        return {"cell": self.name, "identity": self.stem, DEPS_PATH_KEY: self.dependency, "source": SOURCE_DECLARED,
                "root": self.dependency_checkout, "cell_root": self.root, "pin": pin_path, "pinned_commit": pinned,
                "pin_note": pin_note,
                "head": observed["head"], "dirty": observed["dirty"], "note": observed["note"],
                "matches_pin": observed["head"] is not None and observed["head"] == pinned}

    def dependency_deliverables(self):
        """What a product ships of a dependency Cell under the dependency's own names (None for a Cell
        of this repository), for the product's packaging code:

            identity        the published identity; deps_path, checkout and cell_root locate the Cell
            public_headers  the public header and the contract headers, relative to cell_root; a
                            package keeps these relative paths (the public header includes its
                            contract by a relative path)
            archive_name    lib<identity>.a; archives maps each cross core to the archive below the
                            run directory of `cross_build.py --archive`
            license_files   the licence text of the checkout root (required) and its notices (when
                            present), absolute

        Read only: nothing is written below the checkout."""
        if self.dependency is None:
            return None
        checkout = self.settings.path_of(self.dependency)
        licence = os.path.join(checkout, LICENSE_FILE)
        _require(os.path.isfile(licence),
                 "Cell %s: the dependency checkout %s holds no %s: a product cannot ship the dependency without its "
                 "licence text. Next step: select a commit of the dependency %s that holds %s at its root."
                 % (self.name, checkout, LICENSE_FILE, self.dependency, LICENSE_FILE))
        notices = os.path.join(checkout, NOTICE_FILE)
        return {"identity": self.stem, DEPS_PATH_KEY: self.dependency, "checkout": checkout, "cell_root": self.root,
                "public_headers": [self.public_header] + self.contract_headers,
                "archive_name": self.archive_name,
                "archives": {core: "%s/%s/%s" % (self.name, core, self.archive_name) for core in self.cross_cores()},
                "license_files": [licence] + ([notices] if os.path.isfile(notices) else [])}


class RustCell:
    """One validated Rust registry entry plus its derived paths."""

    def __init__(self, name, entry, settings=None):
        settings = _settings(settings)
        self.settings = settings
        prefix = settings.symbol_prefix
        _require(NAME_PATTERN.fullmatch(name) is not None, "Cell name %r is not one lowercase word" % name)
        _require(isinstance(entry, dict) and set(entry) == RUST_CELL_KEYS,
                 "Cell %s must hold %s, found %s" % (name, sorted(RUST_CELL_KEYS),
                                                     sorted(entry) if isinstance(entry, dict) else entry))
        self.name = name
        self.path = entry["path"]
        segments = self.path.split("/") if isinstance(self.path, str) else []
        _require(segments and all(NAME_PATTERN.fullmatch(s) is not None for s in segments)
                 and segments[-1] == name and len(segments) <= 2,
                 "Cell %s: path %r must be %s or <category>/%s" % (name, self.path, name, name))
        self.edition = entry["edition"]
        _require(isinstance(self.edition, str) and EDITION_PATTERN.fullmatch(self.edition) is not None,
                 "Cell %s: edition %r is not a valid Rust edition" % (name, self.edition))
        self.depends_on = entry["depends_on"]
        _require(isinstance(self.depends_on, list) and all(isinstance(v, str) for v in self.depends_on)
                 and len(set(self.depends_on)) == len(self.depends_on) and name not in self.depends_on,
                 "Cell %s: depends_on must be a list of other Cell names without duplicates" % name)
        self.dependencies = []
        repo = settings.repo_root
        self.crate_root = os.path.join(repo, "10_pkg", "rust", *segments)
        self.cell_root = os.path.join(self.crate_root, "cell")
        self.tests_root = os.path.join(repo, "10_pkg", "test", "rust", *segments)
        self.provider = name if len(segments) == 2 and segments[0] == PROVIDER_CATEGORY else None
        self.stem = "%s_provider_%s" % (prefix, name) if self.provider else "%s_%s" % (prefix, name)
        self.abi_path = os.path.join(self.tests_root, "%s_abi.py" % self.stem)
        self.test_pattern = "test_%s_*.py" % self.stem
        self.library_name = "%s.dll" % self.stem
        self.library_environment = "%s_LIBRARY" % self.stem.upper()
        self.build_root = os.path.join(repo, "90_temps", "build", "cargo")
        _require(os.path.isfile(os.path.join(self.crate_root, "Cargo.toml")),
                 "Cell %s: %s has no Cargo.toml" % (name, self.crate_root))
        _require(os.path.isfile(os.path.join(self.cell_root, "comsect1.json")),
                 "Cell %s: %s has no comsect1.json" % (name, self.cell_root))
        for zone in ("contract", "core", "capability", "membrane"):
            _require(os.path.isdir(os.path.join(self.cell_root, zone)),
                     "Cell %s: missing zone directory %s/%s" % (name, self.cell_root, zone))
        self.sources = self._sources()

    def _sources(self):
        found = []
        for directory, names, files in os.walk(self.cell_root):
            names.sort()
            for file_name in sorted(files):
                if file_name.endswith(".rs"):
                    found.append(os.path.relpath(os.path.join(directory, file_name), self.cell_root).replace("\\", "/"))
        _require(found, "Cell %s: no .rs file below %s" % (self.name, self.cell_root))
        return sorted(found)

    def dependency_closure(self):
        ordered = []
        for dependency in self.dependencies:
            for cell in dependency.dependency_closure() + [dependency]:
                if cell not in ordered:
                    ordered.append(cell)
        return ordered


def _declares_standard(manifest_path):
    """Whether a Cell's comsect1.json names the fnd-sdk-standard among its hosted vocabularies."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise RegistryError("unreadable Cell manifest %s: %s" % (manifest_path, error))
    vocabularies = manifest.get("composition_context", {}).get("hosted_vocabularies", [])
    return any(isinstance(entry, dict) and entry.get("id") == "fnd-sdk-standard" for entry in vocabularies)


def _dependency_publication(manifest_path, name):
    """(identity, public header) that the manifest of a dependency Cell publishes: it must name the
    language c and exactly one public identity surface. The owner declares both; the consumer's
    registry restates neither."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as error:
        raise RegistryError("unreadable Cell manifest %s: %s" % (manifest_path, error))
    languages = manifest.get("languages") if isinstance(manifest, dict) else None
    _require(isinstance(languages, list) and C_LANGUAGE in languages,
             "Cell %s: the manifest %s of the dependency Cell does not declare the language %s (languages %r): only "
             "a C Cell is compiled into this product" % (name, manifest_path, C_LANGUAGE, languages))
    surfaces = manifest.get("public_identity_surfaces")
    surface = surfaces[0] if isinstance(surfaces, list) and len(surfaces) == 1 else None
    _require(isinstance(surface, dict) and isinstance(surface.get("identity"), str)
             and IDENTITY_PATTERN.fullmatch(surface["identity"]) is not None
             and isinstance(surface.get("path"), str) and PUBLIC_HEADER_PATTERN.fullmatch(surface["path"]) is not None,
             "Cell %s: the manifest %s of the dependency Cell must declare exactly one public identity surface "
             "{identity: lowercase words joined by an underscore, path: membrane/xpt_<stem>.h}, found %r: the "
             "identity names its archive and its public functions, the path is its public header"
             % (name, manifest_path, surfaces))
    return surface["identity"], surface["path"]


# git never takes an optional lock for a query: `git status` then leaves the index of a consumed checkout
# as it is instead of refreshing its cached file states (a write below the checkout).
GIT_READ_ONLY_OPTION = "--no-optional-locks"


def _git(checkout, arguments):
    try:
        done = subprocess.run(["git", GIT_READ_ONLY_OPTION, "-C", checkout] + arguments, capture_output=True,
                              text=True,
                              stdin=subprocess.DEVNULL, timeout=60, creationflags=process.window_flags())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RegistryError("git on %s failed: %s" % (checkout, error))
    _require(done.returncode == 0, "git %s on %s failed: %s" % (" ".join(arguments), checkout, done.stderr.strip()))
    return done.stdout.strip()


def _inside(path, repo_root):
    path, repo = os.path.realpath(path), os.path.realpath(repo_root)
    return os.path.splitdrive(path)[0].lower() == os.path.splitdrive(repo)[0].lower() \
        and os.path.commonpath([path, repo]) == repo


def _same_path(first, second):
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def dependency_checkout_root(path, repo_root, what, found, details):
    """The real path of a consumed checkout `path` (an existing folder) after refusing a linked or
    reparse component on the way. Inside this repository it must lie below 10_pkg/deps/, the home of
    declared dependency submodules; outside it, it is an override the machine supplies. `what` and
    `found` name the checkout and how it was found in the message; `details` go into the error."""
    try:
        checkout = str(_guard.checked_path(path))
    except (OSError, ValueError) as error:
        raise RegistryError(
            "%s %s (%s) is not usable: %s. Next step: use the checkout folder itself, not a link, junction or "
            "reparse point to it." % (what, path, found, error), dict(details, root=path))
    deps = os.path.join(os.path.realpath(repo_root), *DEPS_FOLDER)
    if _inside(checkout, repo_root) and (not _inside(checkout, deps) or _same_path(checkout, deps)):
        raise RegistryError(
            "%s %s (%s) lies inside this repository outside %s/. Next step: add the dependency as a Git submodule "
            "below %s/, or point the variable at a checkout outside %s."
            % (what, checkout, found, "/".join(DEPS_FOLDER), "/".join(DEPS_FOLDER), repo_root),
            dict(details, root=checkout))
    return checkout


def _require_dependency_path(name, deps_path):
    """Fail closed unless the `dependency` value of the Cell `name` is a valid deps_path: a present key is
    never read as an absent one."""
    defect = _deps_path_defect(deps_path)
    _require(defect is None, "Cell %s: %s %r is invalid: %s. Next step: name the submodule folder of the dependency "
             "below %s/." % (name, DEPENDENCY_KEY, deps_path, defect, "/".join(DEPS_FOLDER)))


def require_unlinked_cell(name, deps_path, checkout, cell_root):
    """Fail closed unless the dependency Cell at `cell_root` is read only from inside its checkout: no
    folder from the checkout down to the Cell root and no folder or file below the Cell root (its
    manifest, sources and headers) is a link or reparse point, and the Cell root lies in the checkout."""
    def refuse(path):
        raise RegistryError(
            "Cell %s: %s is a link or reparse point: a dependency Cell is read only from the files of its "
            "declared checkout %s, never through a link out of it. Next step: restore the tracked content of the "
            "submodule, or select a commit of the dependency that holds the folder or file itself."
            % (name, path, deps_path), {DEPS_PATH_KEY: deps_path, "root": checkout, "link": path})

    current = checkout
    for part in os.path.relpath(cell_root, checkout).split(os.sep):
        current = os.path.join(current, part)
        if process.is_link(current):
            refuse(current)
    for directory, names, files in os.walk(cell_root):
        for entry in sorted(names) + sorted(files):
            if process.is_link(os.path.join(directory, entry)):
                refuse(os.path.join(directory, entry))
    _require(not os.path.lexists(cell_root) or _inside(cell_root, checkout),
             "Cell %s: %s lies outside its declared checkout %s" % (name, cell_root, checkout))


def dependency_checkout(deps_path, settings, name):
    """The real path of the declared checkout that holds the dependency Cell `name`: `deps_path` is a
    submodule path that the repository's .gitmodules lists below 10_pkg/deps/, the folder exists and
    holds an entry, and no component of it is a link or reparse point (fail closed, each with its
    reason and next step). The checkout's commit and dirty state are not conditions."""
    _require_dependency_path(name, deps_path)
    gitmodules = os.path.join(settings.repo_root, GITMODULES_FILE)
    root = settings.path_of(deps_path)
    details = {DEPS_PATH_KEY: deps_path, "source": SOURCE_DECLARED, "root": root, "gitmodules": gitmodules}
    if deps_path not in _submodule_paths(settings.repo_root):
        raise RegistryError(
            "Cell %s: %s %s, but %s lists no submodule at that path: the submodule is the declaration and the "
            "registry names its path. Next step: run %s, or correct %s to the declared submodule path."
            % (name, DEPENDENCY_KEY, deps_path, gitmodules, SUBMODULE_ADD_STEP % (UNNAMED_REPOSITORY, deps_path),
               DEPENDENCY_KEY), details)
    try:
        state = _folder_state(root)
    except OSError as error:
        raise RegistryError("Cell %s: the dependency checkout %s (%s %s) is not readable: %s. Next step: repair the "
                            "folder, then run %s." % (name, root, DEPENDENCY_KEY, deps_path, error,
                                                      SUBMODULE_INIT_STEP % deps_path), details)
    if state is not None:
        raise RegistryError("Cell %s: the dependency checkout %s (%s %s) is %s. Next step: run %s."
                            % (name, root, DEPENDENCY_KEY, deps_path, state, SUBMODULE_INIT_STEP % deps_path), details)
    return dependency_checkout_root(root, settings.repo_root, "Cell %s: the dependency checkout" % name,
                                    "declared as %s %s" % (DEPENDENCY_KEY, deps_path), details)


def _dependency_pin(settings, deps_path):
    """(pin file, pinned commit, note) of the one pin under 10_pkg/components/ whose deps_path names the
    submodule: (None, None, None) without one, and (None, None, note) when the folder cannot be listed or
    several pins name the path. Recorded metadata, never a failure: an unreadable file is skipped."""
    folder = os.path.join(settings.repo_root, *COMPONENTS_FOLDER)
    if not os.path.isdir(folder):
        return None, None, None
    try:
        file_names = sorted(os.listdir(folder))
    except OSError as error:
        return None, None, "the pin folder %s is not readable: %s" % (folder, error)
    found = []
    for file_name in file_names:
        if not file_name.endswith(".json"):
            continue
        path = os.path.join(folder, file_name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                pin = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(pin, dict) and pin.get(DEPS_PATH_KEY) == deps_path:
            found.append((path, pin["commit"] if isinstance(pin.get("commit"), str) else None, None))
    if len(found) > 1:
        return None, None, ("%d pins name %s %s (%s): none is taken as its pin"
                            % (len(found), DEPS_PATH_KEY, deps_path, ", ".join(path for path, _, _ in found)))
    return found[0] if found else (None, None, None)


def _git_observation(checkout, arguments):
    """The stripped output of one git query, or None when the checkout cannot answer it."""
    try:
        return _git(checkout, arguments)
    except RegistryError:
        return None


# Observed consumed checkouts of this process: real path -> {"head", "dirty", "note"}. One tool process
# reads a checkout once, not once per compiled unit.
_OBSERVED_CHECKOUTS = {}


def observed_checkout(checkout):
    """{"head", "dirty", "note"} of a consumed checkout, read for the record and never a condition.

    HEAD and the dirty state (a change or an untracked file) are read only when the checkout is the top
    level of its own Git work tree (a clone, or a submodule with its .git file): git run in a plain
    folder inside another work tree, such as an uninitialized dependency folder of the consumer, would
    report that tree instead. Otherwise head and dirty are None and the note says why."""
    if checkout not in _OBSERVED_CHECKOUTS:
        head = dirty = None
        top = _git_observation(checkout, ["rev-parse", "--show-toplevel"])
        if top is None:
            note = "no Git work tree at %s: HEAD and dirty state are not read" % checkout
        elif not _same_path(top, checkout):
            note = ("%s is no Git work tree of its own (git finds the enclosing work tree %s): HEAD and dirty "
                    "state are not read" % (checkout, top))
        else:
            head = _git_observation(checkout, ["rev-parse", "HEAD"])
            status = _git_observation(checkout, ["status", "--porcelain=v1", "--untracked-files=all"]) \
                if head is not None else None
            dirty = None if status is None else status != ""
            note = None if head is not None else "HEAD of %s is not readable" % checkout
        _OBSERVED_CHECKOUTS[checkout] = {"head": head, "dirty": dirty, "note": note}
    return dict(_OBSERVED_CHECKOUTS[checkout])


# The Foundation pin: its declared checkout, a repository-relative folder below 10_pkg/deps/ that the
# repository's .gitmodules lists as a submodule path (the submodule is the declaration; the pin names
# its path); the removed key that held a local path. A checkout is found through the environment
# override or the declared path, the two recorded sources.
DEPS_PATH_KEY = "deps_path"
REMOVED_PIN_KEY = "local_root"
SOURCE_ENVIRONMENT = "environment"
SOURCE_DECLARED = DEPS_PATH_KEY
# One folder name of a deps_path: a letter or digit first, then letters, digits, ".", "_" or "-", and no
# trailing dot (Windows drops a trailing dot or space, so "x." would alias "x").
DEPS_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?")
GITMODULES_FILE = ".gitmodules"
# .gitmodules is a Git configuration file. Only what a submodule path needs is read: the sections
# `[submodule "<name>"]` (section name without case, a backslash escaping the next character of <name>),
# their `path` key (without case), and its value with quotes, escapes and a trailing `#` or `;` comment
# outside quotes.
GITMODULES_SECTION = "submodule"
GITMODULES_PATH_KEY = "path"
GITMODULES_SECTION_PATTERN = re.compile(r'\[[ \t]*([A-Za-z0-9.-]+)(?:[ \t]+"((?:[^"\\]|\\.)*)")?[ \t]*\]')
GITMODULES_KEY_PATTERN = re.compile(r"([A-Za-z][A-Za-z0-9-]*)[ \t]*(=?)")
GITMODULES_ESCAPE = "\\"
GITMODULES_ESCAPES = {"n": "\n", "t": "\t", "b": "\b", GITMODULES_ESCAPE: GITMODULES_ESCAPE, '"': '"'}
GITMODULES_COMMENTS = "#;"
GITMODULES_BLANKS = " \t"
SUBMODULE_INIT_STEP = "git submodule update --init %s"
SUBMODULE_ADD_STEP = "git submodule add %s %s"
# The repository a pin names when it names none (the pin's "repository" key is recorded metadata).
UNNAMED_REPOSITORY = "<repository URL>"

# Resolved Foundation checkouts of this process: (pin path, pin bytes, override) -> standard_checkout.
_STANDARD_CHECKOUTS = {}


def _deps_path_defect(value):
    """None when `value` is a valid deps_path, otherwise the rule it breaks."""
    if not isinstance(value, str) or not value:
        return "it must be a non-empty string"
    if value.startswith("/") or "\\" in value or ":" in value:
        return "it must be repository-relative with / separators (no root, drive, backslash or stream)"
    parts = value.split("/")
    if tuple(parts[:len(DEPS_FOLDER)]) != DEPS_FOLDER or len(parts) <= len(DEPS_FOLDER):
        return "it must name a folder below %s/" % "/".join(DEPS_FOLDER)
    for part in parts[len(DEPS_FOLDER):]:
        if DEPS_SEGMENT_PATTERN.fullmatch(part) is None:
            return ("its segment %r is not a plain folder name (a letter or digit first, then letters, digits, "
                    "'.', '_' or '-', no trailing dot or space)" % part)
    return None


def _submodule_paths(repo_root):
    """The submodule paths that the repository's .gitmodules lists (empty without the file)."""
    path = os.path.join(repo_root, GITMODULES_FILE)
    if not os.path.lexists(path):
        return set()
    try:
        _require(os.path.isfile(path) and not process.is_link(path), "%s is not a regular file" % path)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError, RegistryError) as error:
        raise RegistryError("the submodule declarations %s are unreadable: %s. Next step: repair %s."
                            % (path, error, GITMODULES_FILE))
    try:
        declared = _gitmodules_paths(text)
    except ValueError as error:
        raise RegistryError("the submodule declarations %s are invalid: %s. Next step: repair %s."
                            % (path, error, GITMODULES_FILE))
    owners = {}
    for name, value in declared.items():
        owners.setdefault(value, []).append(name)
    clashes = ["path %s is declared by the submodules %s" % (value, " and ".join('"%s"' % name for name in names))
               for value, names in sorted(owners.items()) if len(names) > 1]
    _require(not clashes, "the submodule declarations %s are invalid: %s: one folder is one submodule. Next step: "
             "repair %s." % (path, "; ".join(clashes), GITMODULES_FILE))
    return set(owners)


def _gitmodules_value(text):
    """The value of one configuration entry (the text after `=`): blanks around it dropped, quotes removed,
    escapes replaced, and a `#` or `;` comment outside quotes cut. ValueError names what cannot be read."""
    value = []
    blanks = []  # blanks outside quotes, kept only when more of the value follows
    quoted = False
    index = 0
    text = text.lstrip(GITMODULES_BLANKS)
    while index < len(text):
        character = text[index]
        if not quoted and character in GITMODULES_COMMENTS:
            break
        if not quoted and character in GITMODULES_BLANKS:
            blanks.append(character)
            index += 1
            continue
        value += blanks
        blanks = []
        if character == GITMODULES_ESCAPE:
            if index + 1 == len(text):
                raise ValueError("a value continued over the line end is not read")
            if text[index + 1] not in GITMODULES_ESCAPES:
                raise ValueError("unknown escape %s%s" % (GITMODULES_ESCAPE, text[index + 1]))
            value.append(GITMODULES_ESCAPES[text[index + 1]])
            index += 1
        elif character == '"':
            quoted = not quoted
        else:
            value.append(character)
        index += 1
    if quoted:
        raise ValueError("unterminated quoted value")
    return "".join(value)


def _gitmodules_paths(text):
    """{submodule name: path} of the text of a .gitmodules file; a later entry of one name replaces an
    earlier one, as Git reads it. ValueError names the line that cannot be read."""
    paths = {}
    section = None  # the name of the submodule section the line belongs to, else None
    for number, line in enumerate(text.split("\n"), 1):
        rest = line.strip(GITMODULES_BLANKS + "\r")
        if rest.startswith("["):
            header = GITMODULES_SECTION_PATTERN.match(rest)
            if header is None:
                raise ValueError("malformed section header at line %d" % number)
            section = None
            if header.group(1).lower() == GITMODULES_SECTION and header.group(2) is not None:
                section = re.sub(r"\\(.)", r"\1", header.group(2))
            rest = rest[header.end():].lstrip(GITMODULES_BLANKS)
        if not rest or rest[0] in GITMODULES_COMMENTS:
            continue
        key = GITMODULES_KEY_PATTERN.match(rest)
        if key is None:
            raise ValueError("malformed entry at line %d" % number)
        if section is None or key.group(1).lower() != GITMODULES_PATH_KEY or not key.group(2):
            continue
        try:
            paths[section] = _gitmodules_value(rest[key.end():])
        except ValueError as error:
            raise ValueError("%s at line %d" % (error, number))
    return paths


def _folder_state(path):
    """None for a folder that holds an entry, otherwise "missing", "a file, not a folder" or "empty"."""
    if not os.path.lexists(path):
        return "missing"
    if not os.path.isdir(path):
        return "a file, not a folder"
    return None if os.listdir(path) else "empty"


def _standard_pin(settings):
    _require(settings.host_build is not None, "the configuration names no fnd-sdk-standard pin")
    pin_path = settings.path_of(settings.host_build["standard_pin"])
    try:
        with open(pin_path, "rb") as handle:
            pin_bytes = handle.read()
        pin = json.loads(pin_bytes.decode("utf-8"))
    except (OSError, ValueError) as error:
        raise RegistryError("the fnd-sdk-standard pin is unreadable: %s" % error)
    _require(isinstance(pin, dict), "the fnd-sdk-standard pin is not an object")
    _require(isinstance(pin.get("root_variable"), str) and pin["root_variable"],
             "the fnd-sdk-standard pin names no root_variable")
    _require(isinstance(pin.get("public_header"), str) and pin["public_header"],
             "the fnd-sdk-standard pin names no public_header")
    _require(REMOVED_PIN_KEY not in pin,
             "the fnd-sdk-standard pin %s holds %s: a pin holds no machine path outside the repository. Next step: "
             "remove %s, add the dependency as a Git submodule below %s/ and name its path in %s (override with %s)."
             % (pin_path, REMOVED_PIN_KEY, REMOVED_PIN_KEY, "/".join(DEPS_FOLDER), DEPS_PATH_KEY,
                pin["root_variable"]))
    defect = _deps_path_defect(pin.get(DEPS_PATH_KEY))
    _require(defect is None,
             "the fnd-sdk-standard pin %s: %s %r is invalid: %s. Next step: name the submodule folder of the "
             "dependency below %s/." % (pin_path, DEPS_PATH_KEY, pin.get(DEPS_PATH_KEY), defect, "/".join(DEPS_FOLDER)))
    return pin_path, pin_bytes, pin


def standard_checkout(settings=None):
    """The Foundation checkout that supplies the fnd-sdk-standard, with its recorded identity.

    The pin's deps_path must be a submodule path that the repository's .gitmodules lists. The root is
    the directory named by the pin's root_variable (an override the machine supplies) or, when that
    variable is unset or empty, the declared submodule folder deps_path. Nothing else is searched. It
    must exist, contain no linked or reparse component, lie outside the repository or below 10_pkg/deps/
    and hold the public header (fail closed: nothing can be compiled otherwise). The pin's commit is
    recorded metadata: the result holds the root, its source (environment or deps_path), the pinned
    commit, the observed HEAD, dirty state and note and whether HEAD equals the pin; a mismatch or a dirty
    tree is never refused. The result is reused for the same pin and override for the rest of the process."""
    settings = _settings(settings)
    pin_path, pin_bytes, pin = _standard_pin(settings)
    override = os.environ.get(pin["root_variable"], "")
    memo = (os.path.normcase(os.path.abspath(pin_path)), pin_bytes, override)
    if memo not in _STANDARD_CHECKOUTS:
        _STANDARD_CHECKOUTS[memo] = _resolve_standard_checkout(settings, pin, override)
    return dict(_STANDARD_CHECKOUTS[memo])


def _resolve_standard_checkout(settings, pin, override):
    deps_path = pin[DEPS_PATH_KEY]
    declared = settings.path_of(deps_path)
    repository = pin["repository"] if isinstance(pin.get("repository"), str) and pin["repository"] \
        else UNNAMED_REPOSITORY
    source, root = (SOURCE_ENVIRONMENT, override) if override else (SOURCE_DECLARED, declared)
    pinned = pin.get("commit") if isinstance(pin.get("commit"), str) else None
    pin_path = settings.path_of(settings.host_build["standard_pin"])
    details = {"root_variable": pin["root_variable"], "environment_value": override or None,
               DEPS_PATH_KEY: deps_path, "source": source, "root": root, "pin": pin_path,
               "pinned_commit": pinned}
    gitmodules = os.path.join(settings.repo_root, GITMODULES_FILE)
    if deps_path not in _submodule_paths(settings.repo_root):
        raise RegistryError(
            "the fnd-sdk-standard pin %s names %s %s, but %s lists no submodule at that path: the submodule is the "
            "declaration and the pin names its path. Next step: run %s, or correct %s to the declared submodule path."
            % (pin_path, DEPS_PATH_KEY, deps_path, gitmodules, SUBMODULE_ADD_STEP % (repository, deps_path),
               DEPS_PATH_KEY),
            dict(details, gitmodules=gitmodules))
    if override and not os.path.isdir(root):
        raise RegistryError(
            "the fnd-sdk-standard checkout is not found: %s is %s, not a directory (pin %s, pinned commit %s, "
            "recorded metadata). Next step: point %s at a checkout of %s, or unset it to use the declared "
            "checkout %s." % (pin["root_variable"], override, pin_path, pinned, pin["root_variable"], repository,
                              declared),
            details)
    if not override:
        try:
            state = _folder_state(root)
        except OSError as error:
            raise RegistryError(
                "the fnd-sdk-standard declared checkout %s (%s %s of %s) is not readable: %s. Next step: repair "
                "the folder, then run %s." % (root, DEPS_PATH_KEY, deps_path, pin_path, error,
                                              SUBMODULE_INIT_STEP % deps_path),
                details)
        if state is not None:
            raise RegistryError(
                "the fnd-sdk-standard checkout is not found: the declared checkout %s (%s %s of %s; %s is unset) is "
                "%s (pinned commit %s, recorded metadata). Next step: run %s, or set %s to a checkout of %s."
                % (root, DEPS_PATH_KEY, deps_path, pin_path, pin["root_variable"], state, pinned,
                   SUBMODULE_INIT_STEP % deps_path, pin["root_variable"], repository),
                details)
    checkout = dependency_checkout_root(root, settings.repo_root, "the fnd-sdk-standard checkout",
                                        "found through %s" % source, details)
    header = os.path.join(checkout, *pin["public_header"].split("/"))
    observed = observed_checkout(checkout)
    if not os.path.isfile(header):
        raise RegistryError(
            "the fnd-sdk-standard public header %s is missing in the checkout %s (found through %s; HEAD %s, dirty %s, "
            "pinned commit %s). Next step: select a checkout of %s that holds %s (%s, or %s)."
            % (header, checkout, source, observed["head"], observed["dirty"], pinned, repository,
               pin["public_header"], SUBMODULE_INIT_STEP % deps_path, pin["root_variable"]),
            dict(details, root=checkout, head=observed["head"], dirty=observed["dirty"], note=observed["note"],
                 missing=header))
    return {"root": checkout, "source": source, "root_variable": pin["root_variable"],
            "include_root": os.path.dirname(header), "pinned_commit": pinned,
            "head": observed["head"], "dirty": observed["dirty"], "note": observed["note"],
            "matches_pin": observed["head"] is not None and observed["head"] == pinned}


def standard_include_root(settings=None):
    """The include directory of the Foundation's fnd-sdk-standard public header (standard_checkout)."""
    return standard_checkout(settings)["include_root"]


def standard_record(settings=None):
    """The recorded identity of the Foundation checkout (standard_checkout without the include
    directory), or None when no unit of this process read the Foundation."""
    settings = _settings(settings)
    if settings.host_build is None or not _STANDARD_CHECKOUTS:
        return None
    record = standard_checkout(settings)
    del record["include_root"]
    return record


GUARD_PATTERN = re.compile(r"^[ \t]*#[ \t]*ifndef[ \t]+(\w+)[ \t]*\n[ \t]*#[ \t]*define[ \t]+\1\b", re.MULTILINE)


def _include_guard(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            match = GUARD_PATTERN.search(handle.read())
    except (OSError, ValueError) as error:
        raise RegistryError("unreadable header %s: %s" % (path, error))
    return match.group(1) if match else None


def require_unique_stems(files):
    """Fail closed when two C files of the Cells share a stem.

    `files` lists (cell, relative path, include guard or None) for every .c and .h
    file. A stem lives in one folder of one Cell (its .c and .h side by side).
    The only exception is a header-only stem whose every copy carries the same
    include guard: a compatible redeclaration of shared vocabulary, which
    compiles to no object. This syntactic collision check does not establish
    semantic ownership. Returns the number of distinct stems."""
    by_stem = {}
    for cell, path, guard in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        by_stem.setdefault(stem, []).append((cell, path, guard))
    clashes = []
    for stem, entries in sorted(by_stem.items()):
        homes = {(cell, os.path.dirname(path)) for cell, path, _guard in entries}
        sources = ["%s/%s" % (cell, path) for cell, path, _guard in entries if path.endswith(".c")]
        if len(sources) > 1:
            clashes.append("%s: %d .c files (%s)" % (stem, len(sources), ", ".join(sources)))
            continue
        if len(homes) == 1:
            continue
        guards = {guard for _cell, _path, guard in entries}
        if sources or None in guards or len(guards) != 1:
            clashes.append("%s: in %s" % (stem, ", ".join("%s/%s" % (c, p) for c, p, _g in entries)))
    _require(not clashes, "C file stems are not unique across the Cells: %s" % "; ".join(clashes))
    return len(by_stem)


def object_name(key, suffix=".o"):
    """The file name of the object compiled from the unit `key` (a Cell-relative source path, optionally
    prefixed with `<cell>:` or a label): `o_<first 16 hex of SHA-256 of the key>` plus the suffix. Fixed
    length whatever the source path's depth, so a run directory below a deep checkout stays far below
    MAX_PATH; the cross-build record maps each unit to its object name."""
    return "o_%s%s" % (hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], suffix)


def external_symbols(nm_text):
    """The external-linkage symbols an object defines, from GNU nm output (`nm --defined-only`):
    every symbol whose type letter is upper case, except U (undefined)."""
    found = set()
    for line in nm_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[-2]) == 1 and parts[-2].isupper() and parts[-2] != "U":
            found.add(parts[-1])
    return sorted(found)


def require_unique_symbols(defined):
    """Fail closed when two compiled units define one external-linkage symbol.

    `defined` maps a unit label (for example `port/core/port/smo_x_port.c`) to the
    external symbols its object defines. Returns the number of distinct symbols."""
    owners = {}
    for unit, symbols in sorted(defined.items()):
        for symbol in symbols:
            owners.setdefault(symbol, []).append(unit)
    clashes = ["%s (%s)" % (symbol, ", ".join(units)) for symbol, units in sorted(owners.items()) if len(units) > 1]
    _require(not clashes, "external symbols are defined more than once across the Cells: %s" % "; ".join(clashes))
    return len(owners)


def _require_acyclic(cell, trail):
    _require(cell.name not in trail, "Cell dependency cycle: %s" % " -> ".join(trail + [cell.name]))
    for dependency in cell.dependencies:
        _require_acyclic(dependency, trail + [cell.name])


def require_provider_dependencies(cells):
    """Fail closed when a Provider Cell depends on anything but a library Cell: a freestanding Cell that
    is no Provider, depends on nothing and binds nothing."""
    for cell in cells.values():
        if cell.provider:
            for dependency in cell.dependencies:
                _require(dependency.provider is None and not dependency.depends_on and dependency.library_from is None
                         and dependency.export_names is None and dependency.freestanding,
                         "Cell %s: a Provider Cell depends only on library Cells, not on %s" % (cell.name, dependency.name))


def _read(settings):
    try:
        with open(settings.registry_path(), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as error:
        raise RegistryError("registry unreadable: %s" % error)


def _load_rust(settings, raw):
    _require(isinstance(raw, dict) and set(raw) == {"cells"} and isinstance(raw["cells"], dict) and raw["cells"],
             "registry must hold a non-empty 'cells' object")
    cells = {name: RustCell(name, entry, settings) for name, entry in raw["cells"].items()}
    paths = [cell.path for cell in cells.values()]
    _require(len(set(paths)) == len(paths), "registry cells must have distinct paths")
    for cell in cells.values():
        cell.registry_cells = cells
        for dependency in cell.depends_on:
            _require(dependency in cells, "Cell %s depends on unknown Cell %r" % (cell.name, dependency))
            cell.dependencies.append(cells[dependency])
    for cell in cells.values():
        _require_acyclic(cell, [])
    return {"cells": cells}


def load_registry(settings=None):
    """The validated registry: {'cores': {...}, 'cells': {name: Cell}} (Rust: {'cells': {name: RustCell}})."""
    settings = _settings(settings)
    raw = _read(settings)
    if settings.language == "rust":
        return _load_rust(settings, raw)
    _keys(raw, {"cores", "cells"}, "registry")
    cores = raw["cores"]
    _require(isinstance(cores, dict) and (cores or "arm" not in settings.registry["target_names"])
             and all(isinstance(v, list) and all(isinstance(f, str) for f in v) for v in cores.values()),
             "registry cores must map core names to flag lists")
    _require(isinstance(raw["cells"], dict) and raw["cells"], "registry cells must be a non-empty object")
    cells = {name: Cell(name, entry, cores, settings) for name, entry in raw["cells"].items()}
    paths = [(cell.dependency, cell.path) for cell in cells.values()]
    _require(len(set(paths)) == len(paths), "registry cells must have distinct paths")
    require_unique_stems([entry for cell in cells.values() for entry in cell.stem_files()])
    for cell in cells.values():
        cell.registry_cells = cells
        for other in cell.link_with_names:
            _require(other in cells and other != cell.name, "Cell %s: link_with names unknown Cell %r" % (cell.name, other))
            cell.link_with.append(cells[other])
        if cell.library_from is not None:
            _require(cell.library_from in cells, "Cell %s: library_from names unknown Cell %r" % (cell.name, cell.library_from))
            _require(cells[cell.library_from].dependency is None,
                     "Cell %s: library_from names the dependency Cell %s, which has no library in this repository"
                     % (cell.name, cell.library_from))
    for cell in cells.values():
        for dependency in cell.depends_on:
            _require(dependency in cells, "Cell %s depends on unknown Cell %r" % (cell.name, dependency))
            _require(cell.dependency is None or cells[dependency].dependency is not None,
                     "Cell %s: a dependency Cell depends only on dependency Cells, not on %s, a Cell of this "
                     "repository" % (cell.name, dependency))
            cell.dependencies.append(cells[dependency])
    for cell in cells.values():
        _require_acyclic(cell, [])
    require_provider_dependencies(cells)
    for cell in cells.values():
        if cell.owns_library():
            source = cells[cell.library_from]
            _require(not source.freestanding and cell not in source.linked_cells(),
                     "Cell %s: library_from %s must be a hosted binding Cell that does not link it"
                     % (cell.name, source.name))
        elif cell.library_from is not None:
            source = cells[cell.library_from]
            _require(cell in source.linked_cells() or cell in source.exported_cells(),
                     "Cell %s: library_from %s does not link it" % (cell.name, source.name))
        # With owned_library_links_binding, an owned library also exports from its hosted binding.
        allowed = [cell] + (cell.library_plan()[1] if cell.owns_library()
                            and settings.registry["owned_library_links_binding"] else cell.linked_cells())
        for exported in cell.exported_cells():
            _require(exported in allowed, "Cell %s: exports %s, which its library does not link" % (cell.name, exported.name))
            _require(exported.public_header is not None, "Cell %s: exports %s, which has no public header"
                     % (cell.name, exported.name))
        if cell.freestanding:
            for dependency in cell.dependency_closure():
                _require(dependency.freestanding, "Cell %s is freestanding but depends on hosted Cell %s"
                         % (cell.name, dependency.name))
    return {"cores": cores, "cells": cells}


def authored_cells(cells):
    """The Cells this repository authors, in name order: every Cell of the registry that is no
    dependency Cell. They are the Cells its tools build a library for, test and submit to the Gate; a
    dependency Cell is built only into them and is verified in its own repository."""
    return [cell for _name, cell in sorted(cells.items()) if cell.dependency is None]


def get_cell(name, settings=None):
    cells = load_registry(settings)["cells"]
    if name not in cells:
        raise RegistryError("unknown Cell %r (registry holds: %s)" % (name, ", ".join(sorted(cells))))
    return cells[name]
