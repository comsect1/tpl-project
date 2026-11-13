# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""Shared maintainer tools of the Comsect1 project template (common layer, synchronized verbatim).

Modules: `path_guard`, `process`, `config`, `sync`, `c_standard`, `registry`,
`native_gate`, `cross_build`, `host_build`, `gate`. A product selects behaviour through
`20_ops/tools/maint_config.json` and optional `product_hooks.py`, never by
editing this package. The package carries no version of its own: the product's
`.instructions/template-origin.json` records the template revision it came
from (`origin()`). Maintainer-only: never part of an SDK or RUNTIME deliverable.
"""


def origin(repo_root=None):
    """The product's template origin record (`.instructions/template-origin.json`)."""
    from . import config
    return config.origin(repo_root)
