# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""The product configuration `20_ops/tools/maint_config.json`, its hooks and the template origin.

Python standard library only. `load()` validates the configuration fail-closed
before any tool runs: keys are `snake_case`, unknown keys are refused, paths are
repository-relative (only the compiler directories are absolute).
`maintainer_tools()` reads `.instructions/template-origin.json`, checks the
common layer against `comsect1_maint/sync_manifest.json` and returns the
`maintainer_tools` member every tool record carries.
"""

import importlib.util
import json
import os
import re

from . import process
from . import sync

SCHEMA = "comsect1.maint_config.v1"
CONFIG_RELATIVE = "20_ops/tools/maint_config.json"

WORD = re.compile(r"[a-z][a-z0-9]*")
UPPER_WORD = re.compile(r"[A-Z][A-Z0-9]*")
SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ENVIRONMENT = re.compile(r"[A-Z][A-Z0-9_]*")

TOP_KEYS = {"schema", "product", "language", "registry", "host_build", "cross_build", "native_gate", "hooks"}
PRODUCT_KEYS = {"id", "symbol_prefix", "environment_prefix", "image_record_product"}
C_REGISTRY_KEYS = {"path", "target_names", "provider_budget_required", "owned_library_links_binding",
                   "membrane_header_exclusions", "sdk_dependencies"}
RUST_REGISTRY_KEYS = {"path"}
SDK_DEPENDENCY_KEYS = {"allowed", "root_environment", "required_for_path_prefix", "vocabulary_header",
                       "shell_membrane"}
HOST_BUILD_KEYS = {"compiler_bin_dir", "standard_pin", "image_target", "provider_family", "sdk_link_libraries",
                   "provider_export_aliases"}
CROSS_BUILD_KEYS = {"toolchain_bin_dir", "common_flags"}
NATIVE_GATE_KEYS = {"config"}
HOOK_NAMES = ("provider_family", "build_inputs_begin", "build_inputs_end", "build_evidence", "run_command",
              "gate_publications")
TARGET_NAMES = ("windows", "arm")


class ConfigError(Exception):
    """The configuration, a hook module or the template origin is unusable; the message is the reason."""


class DriftError(ConfigError):
    """The common layer differs from its manifest, or the template origin is absent."""


def _require(condition, message):
    if not condition:
        raise ConfigError(message)


def _keys(section, expected, what):
    _require(isinstance(section, dict), "%s must be an object" % what)
    _require(set(section) == set(expected), "%s must hold exactly %s, found %s"
             % (what, sorted(expected), sorted(section)))


def _relative(value, what):
    _require(isinstance(value, str) and value and not os.path.isabs(value) and ":" not in value
             and "\\" not in value and all(part not in ("", ".", "..") for part in value.split("/")),
             "%s must be a repository-relative path with / separators" % what)
    return value


def _absolute(value, what):
    _require(isinstance(value, str) and value and os.path.isabs(value)
             and ".." not in value.replace("\\", "/").split("/"), "%s must be an absolute path" % what)
    return value


def _strings(value, what, pattern=None, allow_empty=True):
    _require(isinstance(value, list) and (allow_empty or value)
             and all(isinstance(item, str) and item and (pattern is None or pattern.fullmatch(item)) for item in value)
             and len(set(value)) == len(value), "%s must be a list of distinct strings" % what)
    return list(value)


class Settings:
    """A validated product configuration; `raw` is the JSON value, `repo_root` the product root."""

    def __init__(self, raw, repo_root, path):
        self.raw = raw
        self.repo_root = os.path.abspath(repo_root)
        self.path = path
        product = raw["product"]
        self.product_id = product["id"]
        self.symbol_prefix = product["symbol_prefix"]
        self.environment_prefix = product["environment_prefix"]
        self.image_record_product = product["image_record_product"]
        self.language = raw["language"]
        self.registry = raw["registry"]
        self.host_build = raw["host_build"]
        self.cross_build = raw["cross_build"]
        self.native_gate = raw["native_gate"]
        self.hooks = raw["hooks"]
        self._hook_module = None

    def path_of(self, relative):
        return os.path.join(self.repo_root, *relative.split("/"))

    def registry_path(self):
        return self.path_of(self.registry["path"])

    def hook(self, name, required=True):
        """The product hook `name` from `product_hooks.py`, or None when not required and not defined.
        A required hook that the configuration names no module for, or the module lacks, fails closed."""
        _require(name in HOOK_NAMES, "unknown hook %r" % name)
        if self.hooks is None:
            _require(not required, "the configuration names no hook module, but hook %s is required" % name)
            return None
        if self._hook_module is None:
            self._hook_module = load_hooks(self.path_of(self.hooks["module"]))
        function = getattr(self._hook_module, name, None)
        _require(function is None or callable(function), "hook %s is not callable" % name)
        _require(function is not None or not required, "product_hooks.py defines no required hook %s" % name)
        return function


def load_hooks(path):
    """Load the product hook module by file path (never through sys.path)."""
    _require(os.path.isfile(path) and not process.is_link(path), "hook module %s is not a regular file" % path)
    spec = importlib.util.spec_from_file_location("comsect1_maint_product_hooks", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # a broken hook module fails closed with its reason
        raise ConfigError("hook module %s failed to load: %s" % (path, error)) from error
    return module


def validate(raw):
    """Check one configuration value against the schema; return it unchanged."""
    _keys(raw, TOP_KEYS, "maint_config")
    _require(raw["schema"] == SCHEMA, "maint_config.schema must be %s" % SCHEMA)
    product = raw["product"]
    _keys(product, PRODUCT_KEYS, "product")
    for key in ("id", "symbol_prefix"):
        _require(isinstance(product[key], str) and WORD.fullmatch(product[key]), "product.%s must match [a-z][a-z0-9]*" % key)
    _require(isinstance(product["environment_prefix"], str) and UPPER_WORD.fullmatch(product["environment_prefix"]),
             "product.environment_prefix must match [A-Z][A-Z0-9]*")
    _require(isinstance(product["image_record_product"], str) and WORD.fullmatch(product["image_record_product"]),
             "product.image_record_product must be one lowercase word")
    language = raw["language"]
    _require(language in ("c", "rust"), "language must be c or rust")
    registry = raw["registry"]
    if language == "rust":
        _keys(registry, RUST_REGISTRY_KEYS, "registry (rust)")
        _relative(registry["path"], "registry.path")
        for key in ("host_build", "cross_build", "native_gate"):
            _require(raw[key] is None, "%s must be null for language rust" % key)
    else:
        _keys(registry, C_REGISTRY_KEYS, "registry (c)")
        _relative(registry["path"], "registry.path")
        names = _strings(registry["target_names"], "registry.target_names", allow_empty=False)
        _require(all(name in TARGET_NAMES for name in names), "registry.target_names admits only windows and arm")
        _require(registry["provider_budget_required"] in ("always", "cross_build_only"),
                 "registry.provider_budget_required must be always or cross_build_only")
        _require(isinstance(registry["owned_library_links_binding"], bool),
                 "registry.owned_library_links_binding must be true or false")
        _strings(registry["membrane_header_exclusions"], "registry.membrane_header_exclusions",
                 re.compile(r"_[a-z0-9_]*\.h"))
        sdk = registry["sdk_dependencies"]
        if sdk is not None:
            _keys(sdk, SDK_DEPENDENCY_KEYS, "registry.sdk_dependencies")
            _strings(sdk["allowed"], "registry.sdk_dependencies.allowed", SYMBOL, allow_empty=False)
            _require(isinstance(sdk["root_environment"], str) and ENVIRONMENT.fullmatch(sdk["root_environment"]),
                     "registry.sdk_dependencies.root_environment must be a variable name")
            _require(isinstance(sdk["required_for_path_prefix"], str) and sdk["required_for_path_prefix"].endswith("/")
                     and WORD.fullmatch(sdk["required_for_path_prefix"][:-1]),
                     "registry.sdk_dependencies.required_for_path_prefix must be <word>/")
            _require(isinstance(sdk["vocabulary_header"], str) and re.fullmatch(r"[a-z0-9_]+\.h", sdk["vocabulary_header"]),
                     "registry.sdk_dependencies.vocabulary_header must be a header file name")
            _relative(sdk["shell_membrane"], "registry.sdk_dependencies.shell_membrane")
        host = raw["host_build"]
        _keys(host, HOST_BUILD_KEYS, "host_build")
        _absolute(host["compiler_bin_dir"], "host_build.compiler_bin_dir")
        _relative(host["standard_pin"], "host_build.standard_pin")
        _require(isinstance(host["image_target"], str) and re.fullmatch(r"[a-z0-9]+(-[a-z0-9_]+)*", host["image_target"]),
                 "host_build.image_target must be a target registry id")
        family = host["provider_family"]
        _require(isinstance(family, dict) and family.get("kind") in ("negotiate_offers", "hook"),
                 "host_build.provider_family.kind must be negotiate_offers or hook")
        if family["kind"] == "hook":
            _keys(family, {"kind"}, "host_build.provider_family")
            _require(raw["hooks"] is not None, "host_build.provider_family hook needs hooks.module")
        else:
            _keys(family, {"kind", "offers"}, "host_build.provider_family")
            offers = family["offers"]
            _require(isinstance(offers, list) and offers and all(
                isinstance(row, list) and len(row) == 3 and isinstance(row[0], str) and WORD.fullmatch(row[0])
                and all(type(value) is int and value >= 0 for value in row[1:]) for row in offers)
                and len({row[0] for row in offers}) == len(offers),
                "host_build.provider_family.offers must be distinct [family, number, major] rows")
        libraries = host["sdk_link_libraries"]
        _require(isinstance(libraries, list), "host_build.sdk_link_libraries must be a list")
        for library in libraries:
            _keys(library, {"environment", "member"}, "host_build.sdk_link_libraries entry")
            _require(isinstance(library["environment"], str) and ENVIRONMENT.fullmatch(library["environment"]),
                     "host_build.sdk_link_libraries environment must be a variable name")
            _relative(library["member"], "host_build.sdk_link_libraries member")
        _strings(host["provider_export_aliases"], "host_build.provider_export_aliases", SYMBOL)
        cross = raw["cross_build"]
        if cross is not None:
            _keys(cross, CROSS_BUILD_KEYS, "cross_build")
            _absolute(cross["toolchain_bin_dir"], "cross_build.toolchain_bin_dir")
            _require(isinstance(cross["common_flags"], list) and cross["common_flags"]
                     and all(isinstance(flag, str) and flag.startswith("-") for flag in cross["common_flags"]),
                     "cross_build.common_flags must be a non-empty list of flags")
        gate = raw["native_gate"]
        _keys(gate, NATIVE_GATE_KEYS, "native_gate")
        _relative(gate["config"], "native_gate.config")
    hooks = raw["hooks"]
    if hooks is not None:
        _keys(hooks, {"module"}, "hooks")
        _relative(hooks["module"], "hooks.module")
        _require(hooks["module"].endswith(".py"), "hooks.module must name a .py file")
    return raw


def load(path=None):
    """The validated Settings of `path` (default: this repository's `20_ops/tools/maint_config.json`).
    The product root is two levels above the file's directory."""
    if path is None:
        path = os.path.join(process.REPO_ROOT, *CONFIG_RELATIVE.split("/"))
    path = os.path.abspath(path)
    _require(not process.is_link(path), "configuration %s is a link" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        raise ConfigError("configuration %s unreadable: %s" % (path, error))
    validate(raw)
    return Settings(raw, os.path.dirname(os.path.dirname(os.path.dirname(path))), path)


_current = None


def current():
    """The Settings of this repository, loaded once; fails closed when the configuration is absent."""
    global _current
    if _current is None:
        _current = load()
    return _current


def present():
    """Whether this repository has a configuration (the template itself has none)."""
    return os.path.isfile(os.path.join(process.REPO_ROOT, *CONFIG_RELATIVE.split("/")))


def origin(repo_root=None):
    """The template origin record of the product (`.instructions/template-origin.json`)."""
    repo_root = repo_root or process.REPO_ROOT
    path = os.path.join(repo_root, *sync.ORIGIN_RELATIVE.split("/"))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError) as error:
        raise DriftError("template origin %s unreadable: %s" % (path, error))
    if not sync.origin_valid(record):
        raise DriftError("template origin %s lacks schema %s, template or baseline_commit" % (path, sync.ORIGIN_SCHEMA))
    return record


def maintainer_tools(repo_root=None, accept_drift=False, language=None):
    """The `maintainer_tools` record member, after the drift check of the common layer.

    Fails closed (DriftError) when the template origin is absent or a common file differs from the
    manifest; with `accept_drift` the member records `tool_drift_accepted: true` and the problems."""
    repo_root = repo_root or process.REPO_ROOT
    tools_root = os.path.join(repo_root, "20_ops", "tools")
    problems = []
    try:
        record = origin(repo_root)
        member = {"template": record["template"], "baseline_commit": record["baseline_commit"]}
    except DriftError as error:
        problems.append(str(error))
        member = {"template": None, "baseline_commit": None}
    manifest_path = os.path.join(tools_root, *sync.MANIFEST_RELATIVE.split("/"))
    try:
        member["manifest_sha256"] = process.sha256_file(manifest_path)
        if language is None and present():
            language = current().language
        problems += ["%s %s" % (state, name) for state, name in sync.check(tools_root, language)]
    except (OSError, ValueError, sync.SyncError) as error:
        member["manifest_sha256"] = None
        problems.append("manifest unusable: %s" % error)
    if problems:
        if not accept_drift:
            raise DriftError("maintainer tools drift (%s); fix it upstream and synchronize, or pass "
                             "--accept-tool-drift" % "; ".join(problems))
        member["tool_drift_accepted"] = True
        member["tool_drift"] = problems
    return member
