# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""The reference toolchain pin and the one rule that locates it (Python standard library only).

`reference_toolchain.json` beside this module is the single pin record of the
toolchain bundles every repository uses; it is template-owned and reaches each
repository through the maintainer-tools synchronization, never by hand.

The reference tools root is found by one rule, in this order:

1. `COMSECT1_REFERENCE_TOOLS`, when set: it must be an absolute directory. A set
   but unusable value is an error; it never falls through to the layout.
2. Otherwise the canonical checkout layout: the nearest ancestor of the
   repository root that holds `00_reference/tools/`, whatever the depth of
   the checkout.

The selected bundle `<root>/<bundle>/` must hold the pinned provenance file.
There is no other source: no `LLVM_HOME`, no Android NDK, no PATH search.
Digests are recorded in the result (`provenance_sha256_recorded` beside the
digest the caller reads), never a condition here.
"""

import json
import os
import re
from pathlib import Path

PIN_FILE = Path(__file__).resolve().parent / "reference_toolchain.json"
PIN_SCHEMA = "comsect1.reference_toolchain_pin.v1"
ROOT_VARIABLE = "COMSECT1_REFERENCE_TOOLS"
LAYOUT_ANCHOR = ("00_reference", "tools")
TOOLCHAIN_KEYS = ("toolchain", "version", "bundle", "release_tag", "archive_name", "archive_sha256",
                  "provenance", "provenance_sha256")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
NAME_PATTERN = re.compile(r"[A-Za-z0-9_.+-]+")


class ReferenceToolsError(Exception):
    """The pin is unusable or the pinned bundle cannot be located; the message names paths and a next step."""


def _require(condition, message):
    if not condition:
        raise ReferenceToolsError(message)


def load_pin(path):
    """The validated pin record: {"schema", "native_c": {TOOLCHAIN_KEYS}}."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReferenceToolsError("reference toolchain pin %s unreadable: %s" % (path, error)) from error
    _require(isinstance(record, dict) and set(record) == {"schema", "native_c"} and record["schema"] == PIN_SCHEMA,
             "reference toolchain pin %s must be {schema: %s, native_c}" % (path, PIN_SCHEMA))
    entry = record["native_c"]
    _require(isinstance(entry, dict) and set(entry) == set(TOOLCHAIN_KEYS),
             "reference toolchain pin native_c must have exactly the members %s" % ", ".join(TOOLCHAIN_KEYS))
    for key in TOOLCHAIN_KEYS:
        _require(isinstance(entry[key], str) and entry[key], "reference toolchain pin native_c.%s is empty" % key)
    for key in ("bundle", "provenance"):
        _require(NAME_PATTERN.fullmatch(entry[key]) is not None and entry[key] not in (".", ".."),
                 "reference toolchain pin native_c.%s must be one plain name" % key)
    for key in ("archive_sha256", "provenance_sha256"):
        _require(SHA256_PATTERN.fullmatch(entry[key]) is not None,
                 "reference toolchain pin native_c.%s must be 64 lowercase hex digits" % key)
    return record


def resolve_root(repo_root, environ=None):
    """(reference tools root Path, how it was found): the variable, else the nearest layout ancestor."""
    environ = os.environ if environ is None else environ
    value = environ.get(ROOT_VARIABLE)
    if value is not None:
        _require(os.path.isabs(value) and Path(value).is_dir(),
                 "%s=%r is not an absolute existing directory. Next step: correct or unset it "
                 "(unset, the root is the nearest %s ancestor of the checkout)."
                 % (ROOT_VARIABLE, value, "/".join(LAYOUT_ANCHOR)))
        return Path(value), "variable %s" % ROOT_VARIABLE
    start = Path(repo_root).resolve()
    searched = []
    for ancestor in (start, *start.parents):
        candidate = ancestor.joinpath(*LAYOUT_ANCHOR)
        searched.append(str(candidate))
        if candidate.is_dir():
            return candidate, "layout ancestor %s" % ancestor
    raise ReferenceToolsError("no reference tools root: %s is unset and no ancestor of %s holds %s (searched %s). "
                              "Next step: work in the canonical checkout layout or set %s to the absolute "
                              "reference tools directory."
                              % (ROOT_VARIABLE, start, "/".join(LAYOUT_ANCHOR), "; ".join(searched), ROOT_VARIABLE))


def native_c_toolchain(repo_root, environ=None, pin_path=None):
    """The pinned native C toolchain selection: provenance path, recorded digest and how it was found."""
    pin_path = PIN_FILE if pin_path is None else Path(pin_path)
    entry = load_pin(pin_path)["native_c"]
    root, source = resolve_root(repo_root, environ)
    bundle = root / entry["bundle"]
    provenance = bundle / entry["provenance"]
    _require(provenance.is_file(),
             "pinned toolchain %s %s is absent: %s has no %s (root from %s). Next step: build the bundle %s "
             "from the official archive %s (SHA-256 %s) with the reference bundle procedure, or select the "
             "reference tools root that holds it."
             % (entry["toolchain"], entry["version"], bundle, entry["provenance"], source, entry["bundle"],
                entry["archive_name"], entry["archive_sha256"]))
    return {"manifest": provenance, "manifest_sha256": entry["provenance_sha256"], "pin": dict(entry),
            "pin_path": str(pin_path), "reference_tools_root": str(root), "root_source": source}
