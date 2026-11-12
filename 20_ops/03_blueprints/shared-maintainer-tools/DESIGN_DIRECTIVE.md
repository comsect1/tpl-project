# Shared maintainer tools design directive

Date: 2026-11-02. Work revision r2: stage 1 (steps 1 to 9: template package,
instructions, DCB adoption) done; record and deviations in section 14. Steps
10 to 14 (HSB, WSB, Hatbit, Sunset, closing T2) are open. Work revision r3
(2026-11-03): build optimization (object cache, parallel compilation), section 15.

Authority: `fnd-platform-architecture` reference model section 8, finding T2
("`native_gate.py`, `native_path_guard.py`, `cross_build.py`,
`cell_registry.py` and `build.py` are copied between DCB, HSB, WSB and
Sunset"); recommendation already recorded there: move the shared maintainer
tools into the project template and keep product configuration local.

Scope: maintainer-only tools under `20_ops/tools/`. The SDK-user tool
`tools/comsect1_sdk.pyz` (Foundation `fnd-platform-architecture`, distribution 8.5,
finding T1) is SDK content and stays out of this design; see section 9.

## 1. Evidence: what is copied today

Inventory of 2026-11-02 (`python -I -B` AST comparison per top-level
function, class and module constant; line counts are physical lines).

| File | DCB | HSB | WSB | Sunset | Finding |
|---|---|---|---|---|---|
| `native_gate.py` | 707 | absent | 883 | 883 | WSB and Sunset byte-identical. DCB is the 2026-10-24 origin; WSB/Sunset forked it on 2026-10-28 with toolchain-manifest admission, path guard, publications and input-owner selection (9 added units, `find_clang` rewritten). DCB alone kept `check_resource`/`clang_candidates` discovery and alone validates `outcome_summary` in `read_report`. 56 units identical, 10 differ, 1 DCB-only. |
| `native_path_guard.py` | absent | absent | 30 | 30 | Identical. Pure common code, no product term. |
| `cross_build.py` | 272 | absent | 272 | 272 | DCB and Sunset byte-identical. WSB differs in one line of `build` (adds `passive_include_roots()`). 20 units identical. |
| `cell_registry.py` | 603 | 131 | 627 | 627 | C registry (DCB/WSB/Sunset): 26 units identical, 8 differ. Differences are product terms (`dcb_`/`wsb_` stems, `DCB_*`/`WSB_*` environment pattern, `TARGET_NAMES` with or without `arm`), policy switches (Provider budget always vs only when cross-built; `library_plan` linking; `xpt_*_types.h` exclusion) and two product extensions (WSB `fixture_units` + hard-coded `passive_include_roots` table; Sunset `sdk_dependencies` + `WSB_SDK_ROOT` roots). HSB is a Rust registry (`path`, `edition`, `depends_on`) sharing only the skeleton (name/path rules, `_require`, `_require_acyclic`, `get_cell`). |
| `build.py` | 818 | 671 | 844 | 783 | C host build (DCB/WSB/Sunset): 40 units identical, 12 differ, 19 single-product. Differences: product prefix in 5 places; `CREATE_NO_WINDOW` present in WSB only; gcc response files in DCB only; `_provider_family` has three implementations (DCB: ctypes negotiate over family offers; WSB: `wsb_hosted.registration_family`; Sunset: `wsb_policy.hosted` from the selected SDK archive); WSB `fixture_units`, `_source_dependencies`, `native_build_evidence`; Sunset `sdk_input_receipt` capture before/after and `libwsb_shell.a` link. HSB `build.py` is a cargo driver (`FreshBuildSession`, 391 lines) and is not a copy. |
| `gate.py` | 135 | 265 | 136 | 225 | DCB and WSB equal except the WSB passive-roots line. Sunset hardened `_is_link`, `quarantine` (inventory and inode check), `_checked_path`, `_check_tree` and derives Provider `publications` from `WSB_SDK_ROOT`. HSB is a different Rust Gate driver. |
| `text-format.ps1` | 106 | 106 | 106 | 111 | Already template-owned; Comsect1 template, DCB, HSB and WSB identical. Hatbit template carries a compacted copy; Sunset carries an improvement (`--cached --others --exclude-standard`, so untracked files are checked). |
| `package.py` | 1769 | 860 | 1905 | absent | Not a copy: DCB/WSB share 66 units but differ in 35 and each has 12-44 own units; HSB is separate. Out of T2 (section 10). |
| `wsb_evidence.py`, `wsb_project.py` | 953, 540 | 786, 510 | owner | absent | WSB *consumer* tools copied between the two WSB adopters (36 + 21 identical units). Out of T2; candidate for WSB SDK content (section 10). |

Duplicated helpers inside every file (each copy re-implements them):
`_load_sibling`, `new_run_id`, `RUN_ID_PATTERN`, `_is_link`, `quarantine`,
`run_process`/`_run` with `CREATE_NO_WINDOW`, `kill_tree`, `sha256_file`,
`write_json`, `REPO_ROOT` discovery.

Loading contract that every consumer relies on and that the design keeps:
product tools and tests load a tool by file path
(`importlib.util.spec_from_file_location(..., "20_ops/tools/<tool>.py")`)
and call its public functions; `build.py`, `cross_build.py`, `gate.py`,
`package.py`, `wsb_evidence.py` load `cell_registry.py` the same way. The
only private name reached from outside is `native_gate._path_guard` (WSB and
Sunset report tests patch it).

## 2. Direction

One Python package `comsect1_maint` under `20_ops/tools/` of this template is
the single owner of the shared maintainer tools. A product keeps:

- one configuration file `20_ops/tools/maint_config.json` (section 4),
- its `cells.json` and `native_gate_config.json` (already product data),
- an optional `20_ops/tools/product_hooks.py` for logic that is not data
  (section 5),
- its own product-only tools (`package.py`, `fuzz.py`, HSB `build.py` and
  `gate.py`, Sunset `sdk_input_receipt.py`, ...), which import the package.

The package and five thin entry files travel as a common layer of template
synchronization (section 6). Product-specific behaviour is selected by
configuration and hooks, never by editing the package in a product; a needed
change goes upstream to the template and comes back by synchronization.

## 3. Module set and location

```
20_ops/tools/
  comsect1_maint/              common layer (template-owned, synced verbatim)
    __init__.py                package marker; exposes `origin()` (section 7)
    sync_manifest.json         sha256 of every common file (section 6)
    path_guard.py              <- native_path_guard.py (30 lines, as is)
    process.py                 extracted helpers: REPO_ROOT discovery, run_id,
                               run_process/kill_tree/CREATE_NO_WINDOW,
                               is_link (Sunset variant), checked_path,
                               check_tree, quarantine (Sunset variant),
                               sha256_file/text, write_json/text, load_sibling
    config.py                  maint_config.json loader, schema check,
                               hook loader, template-origin reader
    registry.py                <- cell_registry.py; language "c" and "rust"
    native_gate.py             <- native_gate.py (merged, section 3.1)
    cross_build.py             <- cross_build.py
    host_build.py              <- build.py (C host build)
    gate.py                    <- gate.py (C Gate driver, Sunset hardening)
    test/                      template-owned unit tests and fixtures
      test_path_guard.py test_process.py test_config.py test_registry.py
      test_native_gate_config.py test_native_gate_report.py
      test_native_gate_toolchain.py test_native_input_owners.py
      test_cross_build.py test_host_build.py
      fixtures/ (registry files for both languages, config examples per
                 product shape, synthetic toolchain tree, Gate reports)
  native_gate.py               entry shim  (common layer)
  cell_registry.py             entry shim  (common layer)
  cross_build.py               entry shim  (common layer)
  build.py                     entry shim  (common layer; HSB keeps its own)
  gate.py                      entry shim  (common layer; HSB keeps its own)
  text-format.ps1              common layer (already)
  maint_config.json            local layer (product)
  cells.json                   local layer (product)
  native_gate_config.json      local layer (product)
  product_hooks.py             local layer (product, optional)
  <other product tools>        local layer (product)
```

Estimated size of the package (from the merged copies): `path_guard` 30,
`process` ~150, `config` ~130, `registry` ~740 (C ~700 + Rust ~40),
`native_gate` ~930, `cross_build` ~250, `host_build` ~900, `gate` ~230,
shims 5 x ~14, tests ~1,200 (moved from the products, see section 8).

Entry shim (identical text in every product, template-owned):

```python
"""Entry for `native_gate`; the implementation is `comsect1_maint.native_gate`."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from comsect1_maint import native_gate as _implementation
globals().update({k: v for k, v in vars(_implementation).items() if not k.startswith("__")})
if __name__ == "__main__":
    sys.exit(_implementation.main(sys.argv[1:]))
```

`globals().update` (not `import *`) keeps underscore names such as
`_path_guard` reachable, so `spec_from_file_location` consumers see the same
names as before. A test that *patches* a name through the shim must patch the
package module instead (section 8); every other consumer is unchanged.

### 3.1 Merge decisions per module

`native_gate`: base is the WSB/Sunset file (newer, hardened). Three DCB-only
behaviours are kept and selected by `native_gate_config.json`:

- `clang_resource` has two shapes. `{"manifest", "manifest_sha256"}` is the
  manifest admission (WSB/Sunset; digests recorded, never refused, section 16). `{"resource_dir", "required_major",
  "clang_candidates"}` is discovery (DCB, `check_resource` kept). Exactly one
  shape is accepted; `load_config` reports which.
- `read_report` is the DCB strict reader (validates `outcome_summary`,
  `gate_exit in (0,1,3,4)`, blocked assessment keeps `BLOCK`). All four
  products pin the same Gate set hashes (`gate` `01d875b5...`), so the strict
  reader applies to every product. The WSB/Sunset `object_pairs_hook` and
  `parse_constant` guards are kept as well.
- `publications` is optional in the configuration (absent means `[]`), so
  DCB's file needs no change.

`registry`: `maint_config.json` `registry` selects language and the policy
switches in section 1. WSB's hard-coded `passive_include_roots` table becomes a
per-Cell optional key `passive_include_roots` in WSB's `cells.json` (data
migration, section 11). Sunset's `sdk_dependencies` becomes the generic
`registry.sdk_dependencies` block.

`host_build`: gcc response files (DCB) and `CREATE_NO_WINDOW` (WSB) become
common behaviour (both are improvements without product meaning).
`_provider_family`, input receipts, build evidence and SDK link libraries go
through hooks or configuration (sections 4 and 5).

`gate`: Sunset's hardened `_is_link`, `_checked_path`, `_check_tree` and
`quarantine` move to `process.py` and become the common behaviour for every
tool that quarantines a run (also `native_gate`, `cross_build`, `host_build`).
Sunset's Provider `publications` derivation becomes the `gate_publications`
hook.

`cross_build`: the WSB line is covered once `passive_include_roots` is
registry data.

## 4. Product configuration contract

One file per product: `20_ops/tools/maint_config.json`, keys in
`snake_case`, relative repository-anchored paths only, validated fail-closed by
`comsect1_maint.config.load()` before any tool runs. Unknown keys are refused.

```json
{
  "schema": "comsect1.maint_config.v1",
  "product": {
    "id": "dcb",
    "symbol_prefix": "dcb",
    "environment_prefix": "DCB",
    "image_record_product": "dcb"
  },
  "language": "c",
  "registry": {
    "path": "20_ops/tools/cells.json",
    "target_names": ["windows", "arm"],
    "provider_budget_required": "always",
    "owned_library_links_binding": false,
    "membrane_header_exclusions": [],
    "sdk_dependencies": null
  },
  "host_build": {
    "compiler_bin_dir": "C:/msys64/ucrt64/bin",
    "standard_pin": "10_pkg/components/fnd_sdk_standard.json",
    "image_target": "windows-x86_64",
    "provider_family": {
      "kind": "negotiate_offers",
      "offers": [["communication", 2, 1], ["port", 3, 1]]
    },
    "sdk_link_libraries": [],
    "provider_export_aliases": []
  },
  "cross_build": {
    "toolchain_bin_dir": "C:/Program Files/Arm/GNU Toolchain mingw-w64-x86_64-arm-none-eabi/bin",
    "common_flags": ["-ffreestanding", "-fno-builtin", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O2"]
  },
  "native_gate": {
    "config": "20_ops/tools/native_gate_config.json"
  },
  "hooks": null
}
```

Field rules:

| Key | Type | Meaning |
|---|---|---|
| `schema` | const | `comsect1.maint_config.v1` |
| `product.id` | `[a-z][a-z0-9]*` | product word (`dcb`, `hsb`, `wsb`, `sunset`) |
| `product.symbol_prefix` | same pattern | stem prefix: `<prefix>_<cell>`, `<prefix>_provider_<name>_registration`, `<prefix>_<cell>_abi_probe`, fixture `<prefix>_<cell>_<fixture>.dll` |
| `product.environment_prefix` | `[A-Z][A-Z0-9]*` | `external_headers.environment` pattern and `<PREFIX>_<CELL>_LIBRARY` test variables |
| `product.image_record_product` | string | `product` member of `comsect1.image_build_record` |
| `language` | `c` or `rust` | registry schema: `c` requires `standard, definitions, include_roots, freestanding, targets`; `rust` requires `edition` |
| `registry.target_names` | list | admitted `targets` keys (`windows`, `arm`) |
| `registry.provider_budget_required` | `always` or `cross_build_only` | DCB `always`; WSB/Sunset `cross_build_only` |
| `registry.owned_library_links_binding` | bool | WSB `true` (an owned library also links its hosted binding) |
| `registry.membrane_header_exclusions` | list of suffixes | WSB `["_types.h", "_target.h", "_required.h"]` |
| `registry.sdk_dependencies` | null or `{"allowed": [..], "root_environment": "WSB_SDK_ROOT", "required_for_path_prefix": "provider/", "vocabulary_header": "wsb_provider.h", "shell_membrane": "shell/membrane/xpt_wsb_shell.h"}` | Sunset |
| `host_build.compiler_bin_dir` | absolute path | gcc location, child `PATH` only |
| `host_build.provider_family` | `{"kind": "negotiate_offers", "offers": [[family, number, major], ...]}` or `{"kind": "hook"}` | DCB uses offers; WSB and Sunset use the `provider_family` hook |
| `host_build.sdk_link_libraries` | list of `{"environment": "WSB_SDK_ROOT", "member": "lib/windows-x86_64/libwsb_shell.a"}` | Sunset |
| `host_build.provider_export_aliases` | list of symbols | Sunset `["wsb_provider_negotiate", "wsb_provider_open"]`, emitted into the `.def` as aliases of `<stem>_negotiate` / `<stem>_open` |
| `cross_build` | object or null | null in WSB/Sunset/HSB (`cores` empty); object in DCB |
| `native_gate.config` | path | existing per-product file; its own schema is unchanged except the two `clang_resource` shapes |
| `hooks` | null or `{"module": "20_ops/tools/product_hooks.py"}` | section 5 |

The four product values that this schema must express are known (section 1);
the template tests carry one fixture per product shape so that a schema
change that would break a product is caught in the template.

## 5. Product-specific logic: hooks, not forks

`product_hooks.py` is a product-local module loaded by file path. Each hook is
optional; the package calls a hook only when its config requires it and fails
closed when a required hook is missing. Hook names are fixed by the package:

| Hook | Signature | Used by | Today's product code |
|---|---|---|---|
| `provider_family` | `(registration, name) -> str` | `host_build` image records when `host_build.provider_family.kind == "hook"` | WSB `wsb_hosted.registration_family`; Sunset `sunset_package.sdk_archive` + `wsb_policy.hosted` |
| `build_inputs_begin` | `(cell, run_dir) -> token` | `host_build` before compiling a Provider library | Sunset `sdk_input_receipt.validate_sdk_corpus` + `capture` |
| `build_inputs_end` | `(token, cell, library_path, image_record) -> None` | after linking; refuses a changed input, writes the receipt | Sunset `capture` again + `write_receipt` |
| `build_evidence` | `(cell, run_dir, record) -> record` | `host_build` record enrichment | WSB `native_build_evidence`, `_source_dependencies`, `_observed_run` |
| `gate_publications` | `(cell, config) -> list` | `gate.derived_config` | Sunset `publications` from `WSB_SDK_ROOT` |

A hook receives package objects (`registry.Cell`, paths) and may import the
product's own modules. It never receives the package's private state. Anything
that two products need becomes data or package code upstream, not two hooks.

## 6. Template synchronization boundary

Today's practice (read from `.instructions/instruction-layers.md`,
`fork-adoption.md`, the products' `template-origin.json` /
`local/template-baseline.md` and the sync commits `834cee2` HSB, `c7e29bb`
WSB, `2e10034` Sunset): synchronization is a manual three-way merge of the
common files, recorded by updating `template-origin.json`'s `baseline_commit`
and a commit "synchronize the common instructions with <template> <commit>";
`kind/` and `local/` are never touched; no sync tool exists.

This design extends the *common* layer with executable content:

- Common: `20_ops/tools/comsect1_maint/**`, the five entry shims,
  `20_ops/tools/text-format.ps1`. Synced verbatim (copy, not merge): a product
  never edits them. A local edit is a defect; the fix goes upstream.
- Local: `20_ops/tools/maint_config.json`, `cells.json`,
  `native_gate_config.json`, `product_hooks.py`, every other `20_ops/tools/*`
  file. Never written by synchronization.

`comsect1_maint/sync_manifest.json` lists every common file with its sha256
(`{"schema": "comsect1.maint_sync_manifest.v1", "files": {"comsect1_maint/registry.py": "<sha256>", "native_gate.py": "<sha256>", ...}}`),
generated by `python -I -B 20_ops/tools/comsect1_maint/sync.py --write-manifest`
in the template. In a product, `python -I -B 20_ops/tools/comsect1_maint/sync.py --check`
verifies the common files against the manifest and reports `MODIFIED`,
`MISSING` or `EXTRA` paths (exit 1). `sync.py --from <template-checkout>`
copies the common layer from a template checkout, refuses links, writes
nothing outside the listed paths, and prints the new baseline commit for
`template-origin.json`. Both are standard-library Python.

Template instruction changes that accompany the package (common files):

- `instruction-layers.md`, "Template synchronization boundary": add the
  executable common layer and its local counterpart as listed above.
- `fork-adoption.md` item 11: synchronization also copies
  `20_ops/tools/comsect1_maint/` and the entry shims; the fork writes
  `maint_config.json` and, if needed, `product_hooks.py`.
- `artifact-placement.md`: one row, "Template-owned maintainer tool package
  and its tests | `20_ops/tools/comsect1_maint/`" (the tests travel with the
  package because they are common-layer content, the one exception to
  "test source in `10_pkg/`").
- `development-feedback-and-release-evidence.md`: the fast-feedback operation
  for the tools is `python -I -B -m unittest discover -s 20_ops/tools/comsect1_maint/test`.

### 6.1 Comsect1 and Hatbit templates

`comsect1-project-template` owns `comsect1_maint`. The Hatbit template
already declares Comsect1 as its upstream (`template-origin.json`
`baseline_commit 99f9335`), so Hatbit receives the package through its own
template synchronization and never edits it; a Hatbit need (firmware layering,
CMake presets per `development-workflow-integration.md`) is expressed by a
product's configuration or hooks, or proposed upstream. `text-format.ps1`:
adopt Sunset's untracked-file scan in the Comsect1 template first, then let
Hatbit sync it, so the three copies converge on one text again.

## 7. Versioning by template revision

The package carries no version of its own. The product's
`.instructions/template-origin.json` (`baseline_commit`) is the single record
of which template revision the common layer came from; `comsect1_maint.origin()`
reads it. Every tool run summary (`native_gate` `summary.json`, `host_build`
`build_record.json`, `cross_build` and `gate` records) gains one member
`"maintainer_tools": {"template": "<name>", "baseline_commit": "<sha>", "manifest_sha256": "<sha>"}`.
A run fails closed with the config exit class when `template-origin.json` is
absent or the manifest check reports a modified common file, unless
`--accept-tool-drift` is given (the same pattern as `native_gate`'s existing
`--accept-tool-hashes`), in which case the summary records
`"tool_drift_accepted": true`. DCB and HSB currently have no
`template-origin.json` (they record the baseline in
`local/template-baseline.md`); migration adds the file.

No `r{n}`, `v1`/`v2` or date appears in a package path, module name or
identifier (version policy); the schema ids follow the template's existing
`comsect1.<name>.v1` form.

## 8. Test plan

Template-owned tests (`20_ops/tools/comsect1_maint/test/`, standard-library
`unittest`, run with `python -I -B -m unittest discover -s 20_ops/tools/comsect1_maint/test`
from the template or any product):

- `path_guard`, `process`: link/junction refusal, `checked_path` on
  drive-relative and `..` input, `quarantine` inventory and inode checks,
  `run_process` timeout kill (fixture scripts under `test/fixtures/`).
- `config`: every example config (`fixtures/config/{dcb,hsb,wsb,sunset}.json`)
  loads; one negative case per rule (unknown key, absolute path, wrong
  language keys, missing hook when required).
- `registry`: C and Rust fixture registries; each policy switch exercised in
  both states; `passive_include_roots`, `sdk_dependencies`, `fixture_units`.
- `native_gate`: `load_config` accepts each `clang_resource` shape and refuses
  a mixed one; `read_report` (moved from WSB/Sunset `test_native_gate_report.py`,
  extended with DCB's `outcome_summary` cases); toolchain admission (moved
  from WSB `test_native_gate_toolchain.py`); input owners (moved from WSB
  `test_native_input_owners.py`).
- `cross_build` selection and `host_build` pure helpers (`header_functions`,
  `_exported_names`, `_strip_comments`, `_response_file`, `.def` aliases).
- `sync`: manifest write/check round trip; a modified file is reported.

Product tests, unchanged in behaviour:

- Each product's existing commands keep running exactly as listed in its
  `local/c-build-and-test.md` (DCB: `10_pkg/test/tool/gate`, `.../registry`,
  `.../build`; WSB: `10_pkg/test/tool/{gate,registry,build,package}`; Sunset:
  `10_pkg/test/tool/gate`; HSB: `10_pkg/test/python/...`). They load the
  shims by path and call public functions; no change.
- The only required edit: tests that patch a module global through the shim
  (`patch.object(driver, 'sha256_file')`, `driver._path_guard`) must patch
  `comsect1_maint.native_gate` instead. Known files: WSB
  `10_pkg/test/tool/gate/test_native_gate_report.py`, Sunset
  `10_pkg/test/tool/gate/test_native_gate_report.py` and
  `10_pkg/test/tool/native_gate/test_native_gate_report.py` (duplicate; delete
  one). After the template owns these cases (above), the product copies may be
  removed in the same product commit.
- Acceptance per product: the set of passed and skipped test ids before and
  after migration is identical (record both lists in
  `20_ops/06_verification/maintainer_tools_migration.md` of the product).

Behaviour equivalence per product: run `gate.py --cell <cell> --target windows`,
`native_gate.py` on one Cell, `build.py --cell <cell>` and (DCB)
`cross_build.py --archive` before and after; diff `summary.json` /
`build_record.json` ignoring `run_id`, timestamps and the new
`maintainer_tools` member. Expected deltas are only those listed in section 11.

## 9. Boundary to the common SDK tool (T1)

`fnd-platform-architecture` `10_pkg/sdk_tool/comsect1_sdk_tool` builds
`tools/comsect1_sdk.pyz`, shipped in every Tier 2 SDK (DCB, HSB, WSB, Gantry)
for SDK *users* (`install-sdk`, `bundle-runtime`). `comsect1_maint`
is for *maintainers* and is never shipped: it is not a recipe input, never
appears in an SDK or RUNTIME deliverable, and contains no install or bundle
logic. Keep the two apart by rule, not by inspection: each product's
`package.py` already scans shipped files; it must additionally refuse any
path under `20_ops/`. The Foundation tool is recorded per product through
`component-pins.md` (metadata, not a lock, section 16); `comsect1_maint` is versioned by template baseline
(section 7). The two have no import relation in either direction.

## 10. Out of scope, recorded

- `package.py` (DCB, HSB, WSB): too divergent for T2. A later increment can
  extract its 66 identical units (`_digests`, `_refuse_links`, `path_problem`,
  `tag_name`/`tag_id`, `render_tag`, `render_redistributable`, `_spdx_id`,
  `scan`, `query_identity_line`, `issue_deliverable`) into
  `comsect1_maint/release.py` once the three products agree on one recipe
  model.
- `wsb_evidence.py`, `wsb_project.py` (DCB, HSB): WSB consumer tools, copied
  by the two adopters of the WSB SDK. They are SDK-user content and belong in
  the WSB SDK (`sdk` content), not in the project template. Record as a WSB
  finding.
- HSB `build.py` and `gate.py` (cargo and Rust Gate drivers) stay local; they
  adopt `comsect1_maint.process` and `registry` (Rust mode) only.

## 11. Per-product deltas

| Product | Removed (copied lines) | Added locally | Behaviour deltas on adoption |
|---|---|---|---|
| DCB | `native_gate.py` 707, `cross_build.py` 272, `cell_registry.py` 603, `build.py` 818, `gate.py` 135 = 2,535 (replaced by 5 shims, ~70) | `maint_config.json` (~45 lines; `provider_family` as `negotiate_offers`, no hook), `template-origin.json` | path guard now refuses linked or reparse paths in Cell and include roots; hardened quarantine; `publications` and toolchain-manifest mode available but not selected; `maintainer_tools` member in records |
| HSB | `cell_registry.py` 131 (Rust mode of `registry`, shim ~14); ~60 duplicated helper lines in `build.py`/`gate.py` (`is_reparse`, `quarantine_run`, `_load_sibling`, `new_run_id`) replaced by `process` imports | `maint_config.json` (`language: rust`, no `host_build`/`cross_build`/`native_gate`), `template-origin.json` | none in cargo or Gate behaviour; hardened quarantine |
| WSB | `native_gate.py` 883, `native_path_guard.py` 30, `cross_build.py` 272, `cell_registry.py` 627, `build.py` 844, `gate.py` 136 = 2,792 | `maint_config.json` (~50; `provider_family` hook, `build_evidence` hook), `product_hooks.py` (~25, delegating to `wsb_hosted` and `native_build_evidence.py`), `cells.json` gains `passive_include_roots` on `shell`, `process`, `report`, `binder`, `host`, `windows`, `abi` | strict `read_report` (`outcome_summary` required; same Gate set, so no new failures expected); gcc response files; hardened quarantine; `fixture_units` kept |
| Sunset | `native_gate.py` 883, `native_path_guard.py` 30, `cross_build.py` 272, `cell_registry.py` 627, `build.py` 783, `gate.py` 225 = 2,820 | `maint_config.json` (~55; `sdk_dependencies`, `sdk_link_libraries`, `provider_export_aliases`, `provider_family` hook), `product_hooks.py` (~45: `provider_family` via `sunset_package.sdk_archive`, `build_inputs_begin/end` via `sdk_input_receipt.py`, `gate_publications`) | strict `read_report`; gcc response files; its hardened quarantine becomes common (no change for Sunset) |

Total copied lines retired: about 8,300 across the four products; package
size about 3,300 plus tests.

## 12. Risks

1. DCB is being committed to by another session (2026-11-02). Land the
   template first; migrate DCB only on a quiet working tree, in one commit.
2. The strict `read_report` is applied to WSB and Sunset. Mitigation: all
   products pin the identical Gate set hashes; the template test carries both
   report shapes; if a product's Gate set ever lacks `outcome_summary`, the
   fix is a Gate set update, not a reader relaxation.
3. Path guard in DCB: a developer checkout reached through a junction (for
   example a short-path link) will be refused. This is the intended fail-closed
   rule (`temporary-output-and-cleanup.md`); record it in DCB's
   `c-build-and-test.md`.
4. Two `clang_resource` modes keep DCB's discovery alive. Recommend DCB adopt
   the manifest mode as a follow-up owner decision; until then both modes are
   tested in the template.
5. Tests that patch through the shim silently stop patching if the one-line
   repoint is missed; the template test `test_shims.py` asserts that each shim
   exposes the same public names as its package module, and the product
   acceptance (section 8) compares test id sets, so a changed outcome shows.
6. WSB `passive_include_roots` moves from code to `cells.json`; a missed Cell
   changes WSB's include set. Mitigation: the migration commit diffs the
   derived `include` lists per Cell before and after (behaviour equivalence
   run).
7. `sys.path.insert` in the shim exposes `20_ops/tools/` to imports; with
   `python -I` and no `__init__.py` beside the package, only `comsect1_maint`
   is importable as a package; the entry shims remain file-loaded modules.
8. Sync without a tool today: `sync.py --check` is the first machine check of
   the common layer in a product; until it exists, drift can only be found by
   diff. Implement `sync.py` in the template before the first product
   migration.
9. Hatbit convergence: if Hatbit is not synced promptly, Sunset would carry
   the package ahead of its own template. Order Hatbit's sync before Sunset's
   migration (section 13).

## 13. Implementation steps

Template (`comsect1-project-template`, branch work in a scratchpad worktree
per the shared-checkout rule; one PR):

1. Create `20_ops/tools/comsect1_maint/` with `path_guard.py` (copy of WSB
   `native_path_guard.py`) and `process.py` (extract the helpers named in
   section 1; use the Sunset `_is_link`, `_checked_path`, `_check_tree`,
   `quarantine`; DCB `run_process`/`kill_tree` from `native_gate.py`).
2. Write `config.py`: load `maint_config.json`, enforce the section 4 schema,
   load `product_hooks.py` by file path, read `template-origin.json`,
   verify `sync_manifest.json`, expose `origin()`.
3. Port `registry.py` from WSB `cell_registry.py` (the superset), replace the
   product terms with `config.product.*`, add the policy switches, make
   `passive_include_roots` a Cell key, make `sdk_dependencies` generic, add the
   Rust mode from HSB `cell_registry.py`.
4. Port `native_gate.py` from WSB (3.1): both `clang_resource` shapes, DCB
   strict `read_report`, optional `publications`, `process` helpers.
5. Port `cross_build.py` (DCB/Sunset text), `host_build.py` (WSB text plus
   DCB response files, hooks of section 5, `provider_export_aliases`,
   `sdk_link_libraries`), `gate.py` (Sunset text, `gate_publications` hook).
6. Write the five entry shims and `sync.py` (`--write-manifest`, `--check`,
   `--from`); generate `sync_manifest.json`.
7. Move the WSB/Sunset/DCB tool tests into `comsect1_maint/test/` as listed
   in section 8, add the config fixtures for the four product shapes and
   `test_shims.py`; run `python -I -B -m unittest discover -s 20_ops/tools/comsect1_maint/test`
   on a clean checkout (no product present): all pass.
8. Update the four common instructions named in section 6 and adopt Sunset's
   `text-format.ps1` improvement. Run `text-format.ps1`. Commit, PR, merge;
   note the merge commit as the new baseline.

Products, in this order; each is one commit "chore(tools): adopt the template
maintainer tools at <baseline>", preceded by the before-run of section 8:

9. DCB: `sync.py --from <template>`; write `maint_config.json`
   (`negotiate_offers` from `_FAMILY_OFFERS`, `target_names` with `arm`,
   `provider_budget_required: always`, `cross_build` block from
   `cross_build.py` constants); add `.instructions/template-origin.json`
   (baseline from `local/template-baseline.md`, updated); delete the five
   product copies (shims replace them); run the DCB tool and Cell test
   commands; after-run and equivalence diff; record in
   `20_ops/06_verification/maintainer_tools_migration.md`; update
   `local/c-build-and-test.md` (path guard note) and `local/template-baseline.md`.
10. HSB: sync; `maint_config.json` with `language: rust`; replace
    `cell_registry.py` by the shim; in `build.py` and `gate.py` replace the
    duplicated helpers with `comsect1_maint.process` imports; add
    `template-origin.json`; run the HSB suites; record.
11. WSB: sync; `maint_config.json` (`provider_family: hook`,
    `cross_build_only`, exclusions, `owned_library_links_binding: true`);
    `product_hooks.py` (`provider_family` -> `wsb_hosted.registration_family`,
    `build_evidence` -> `native_build_evidence.py`); add
    `passive_include_roots` to the seven Cells in `cells.json`; delete the six
    product copies; repoint `test_native_gate_report.py` patches or delete the
    cases now owned by the template; run; equivalence diff of per-Cell include
    lists; record.
12. Hatbit template: synchronize from Comsect1 (common instructions,
    `comsect1_maint`, shims, `text-format.ps1`); update its
    `template-origin.json`.
13. Sunset: sync from Hatbit; `maint_config.json` (`sdk_dependencies`,
    `sdk_link_libraries`, `provider_export_aliases`, `provider_family: hook`);
    `product_hooks.py` (section 11); delete the six product copies and the
    duplicate report test; run; record; update `template-origin.json`.
14. Close T2 in `fnd-platform-architecture` section 8 with the baseline commit and the
    four product commits; open the two section 10 findings (package.py
    release helpers; WSB consumer tools as SDK content).

## 14. Stage 1 record (r2, 2026-11-02)

Steps 1 to 9 are done; steps 10 to 14 are open. Decided by recommendation
(owner directive); the owner authorized commits on `main` of both
repositories, so the template work landed on `main` without a worktree or PR.

### 14.1 What landed

Template `comsect1-project-template` (`main`):

| Commit | Content |
|---|---|
| `604ace4` | `text-format.ps1`: Sunset's untracked-file scan and BOM refusal |
| `188ea6b` | `20_ops/tools/comsect1_maint/` (`path_guard` 30, `process` 245, `config` 306, `sync` 259, `registry` 772, `native_gate` 933, `cross_build` 266, `host_build` 892, `gate` 126, `__init__` 17 lines), `sync_manifest.json`, five entry shims (26 to 28 lines each), the tests (16 files, 83 tests) and fixtures; `.gitignore` keeps `__init__.py` |
| `9761699` | the four common instructions of section 6 |
| `7c2cfc1` | this directive (r1) |
| `dca7cbf` | native input owner protocol selected by `publications` (14.3 item 4) |

DCB `prd-plt-data-comm-bridge-reboot` (`main`): `ea66275c` synchronizes the
common instructions (4149c03 to 7c2cfc1, unchanged in dca7cbf); `333326a8`
adopts the tools at `dca7cbf` (2,535 copied lines retired, replaced by 138
shim lines and `maint_config.json`); the record is
`20_ops/06_verification/maintainer_tools_migration.md`.

### 14.2 Acceptance (DCB)

- Test id sets: 25 suites (every folder of `10_pkg/test` holding tests),
  before at `7331679c`, after at the adoption commit: before 1,161 passed, 6
  skipped, 3 failed (three `tool/wsb` tests that need a WSB checkout and a
  current WSB profile); after the same skipped and failed sets plus one TCP
  loopback test of `c/binder` that fails since the before run, with the
  pre-migration tools restored as well (host state, not the migration; the
  library under test has the same bytes before and after). Otherwise the
  passed sets are identical.
- Native Gate on every Cell and target (28 runs, run ids reused): verdicts,
  findings, codes, outcome summaries and report digests unchanged; only
  `maintainer_tools` is new. The plan digest changes on every run of either
  code (it holds the native-input authority ids, new per receipt).
- `cross_build.py --archive` (all Cells): `summary.json` identical but
  `maintainer_tools`.
- `build.py` on every host Cell (14): build and image records identical but
  `maintainer_tools`; the product libraries keep their bytes. Fixture
  libraries differ on every build, before and after alike (their link lacks the
  reproducible flags; pre-existing, recorded in the DCB record).
- `package.py --dist` (`sdk/windows-x86_64` with its runtime, `sdk/cortexm`):
  588 files each; only the revision-bound SWID tags and SBOMs (and the package
  records) differ, by the source commit.

### 14.3 Deviations from r1

1. Entry shim: loads `comsect1_maint/__init__.py` by file path with its
   submodule search location instead of `sys.path.insert`, so no module of
   `20_ops/tools/` becomes importable by name (risk 7 removed); it refuses a
   `comsect1_maint` already loaded from another folder.
2. `.gitignore` of the template ignores `_*`; it now keeps `__init__.py` and
   `__main__.py` (DCB and WSB already did; HSB does not: add the two lines
   before its sync, or the package is silently untracked).
3. Section 4 example offers were wrong: DCB's `_FAMILY_OFFERS` are
   `communication 1` and `port 2` (`[["communication", 1, 1], ["port", 2, 1]]`).
4. Native input owners: the installed Gate set's `comsect1-native-input`
   (`4ec0f0dc...`, pinned by all four products) refuses `--source-cell`. The
   owner protocol (`--source-cell`, `--publication`) is used only when the lane
   configuration has a `publications` member (even `[]`); without it, external
   inputs go by id only. So `publications` absent is not the same as `[]`
   (section 3.1 said it was). WSB and Sunset carry the member: with this Gate
   set, any of their Cells whose closure reaches another Cell's files fails
   with NATIVE_INPUT_FAILED until the Gate set is updated or the member is
   dropped; check before their migration.
5. Hooks: `run_command(kind, command, env, cwd, what, context)` added; when a
   product defines it, host_build runs every compile and link through it
   (WSB's per-command `native_build_evidence.observe`, which `build_evidence`
   alone cannot express). `build_inputs_begin`/`end` are called when defined
   and required when `registry.sdk_dependencies` is set.
6. Drift: `tool_drift_accepted` and the problem list live inside the
   `maintainer_tools` member. Exit classes: native Gate 4; build.py and
   cross_build.py 1; gate.py passes `--accept-tool-drift` to the native Gate.
7. `sync.py`: `--to <product>` for the first adoption (the product has no
   `sync.py` yet); `--from` refuses a template checkout with changes or whose
   files differ from its manifest, moves stale package files through
   `99_trash`; `__pycache__` is ignored; a Rust product (`language` of its
   `maint_config.json`) needs only the `cell_registry.py` shim.
8. The native Gate summary has `native_toolchain` only in the manifest shape,
   so DCB's summary keeps its members.
9. `read_report`: WSB's separate checks (types, count, PASS needs CONTINUE)
   come first, then DCB's strict set; PASS with a blocked assessment has its
   own message.
10. Registry: the Rust mode is a class `RustCell` (HSB code calling
    `Cell(name, entry)` must use it); the `sdk_dependencies` Cell key is
    admitted only when configured, and Cells below
    `required_for_path_prefix` declare exactly `allowed`; `cores` may be empty
    only without the `arm` target.
11. host_build: the WSB fixture include roots (the exported Cells' dependency
    membranes and passive roots) are common; DCB's fixture objects are
    unchanged by them.
12. DCB also synchronized its common instructions, so its recorded baseline is
    true for every common file; its `wsb_evidence.py` tool fingerprint covers
    `maint_config.json` and the package manifest, since the shims alone no
    longer identify the tools. Section 9's `20_ops/` refusal already holds in
    DCB (`package.py` admits only `10_pkg/` sources).
13. gate.py prints the cleanup preview line (Sunset behaviour, now common).

### 14.4 For the HSB, WSB and Sunset migrations

- Capture the before id sets first; run `sync.py --from` from the template
  checkout with `--to`; write `maint_config.json` from
  `test/fixtures/config/<product>.json` (it validates today) and check every
  value against the product code before use.
- WSB: `cells.json` gains `passive_include_roots` on the seven Cells of its
  table; `product_hooks.py` needs `provider_family`, `run_command` (observe)
  and `build_evidence`; decide item 4 before the first Gate run; its
  `test_native_gate_report.py`, `_toolchain.py` and `_input_owners.py` are now
  template tests (delete them or repoint patches to `comsect1_maint.native_gate`).
- Sunset: hooks `provider_family`, `build_inputs_begin`/`end`,
  `gate_publications`; its cleanup tests patch `gate._is_link`, which is now
  `comsect1_maint.process.is_link`; its two `test_native_gate_report.py`
  copies go.
- HSB: `.gitignore` (item 2); Rust registry via `RustCell`; replace its
  duplicated helpers with `comsect1_maint.process`.
- Every product: tests that patch a module global through a shim must patch
  `<shim>._implementation` (the package module); the template test
  `test_shims.py` asserts every shim exposes its module.

## 15. Build optimization (r3, 2026-11-03)

Owner request: a build and compile optimization policy for the C build tools,
decided by recommendation. The policy is the common instruction
`.instructions/build-optimization.md`; this section is its design and
measurement record. Template commit `073aa7a` (code, tests, instruction);
products adopt it by synchronization.

### 15.1 Where the time went

Profile of `build.py --cell tcp` in DCB (one process, before; `subprocess`
wrapped and timed): 70.5 s in all, 69.7 s in child processes, 0.8 s Python.

| Step | Calls | Seconds | Mean |
|---|---|---|---|
| gcc compile | 70 | 51.8 | 0.74 |
| nm | 69 | 7.9 | 0.11 |
| ABI probes (`-fsyntax-only`) | 7 | 3.9 | 0.56 |
| git (pin check, repeated per unit Cell) | 16 | 2.9 | 0.18 |
| link (library and fixture) | 2 | 2.7 | 1.33 |
| objdump | 2 | 0.5 | 0.23 |

No unit was compiled twice inside that run, but six Cells (`serial`, `tcp`,
`udp`, `driver`, `host`, `windows`) build the same `dcb_windows.dll`, and
`abi`, `binder` and `superloop` link most of the same units again: across the
15-Cell sequence the same sources were compiled up to seven times.
`cross_build.py` compiled each dependency Cell once per consumer and core.
Everything was sequential on a 20-processor host. Image-record and digest work
was below 0.1 s.

### 15.2 Design

- `comsect1_maint/object_cache.py`: two-level content-addressed cache (manifest
  key without the dependency closure, object key with it, from gcc `-MD`), so
  a hit needs no preprocessor run. Toolchain identity = driver path, version,
  machine, SHA-256 of driver, `cc1` and `as`. The directory listing of every
  dependency directory and include root is keyed too (a new header earlier on
  the search path is a miss). Objects are stored once by SHA-256 (`blobs/`) and
  verified on every read. `nm` and `size` listings are cached by tool digest,
  arguments and object digest. Atomic publication by `os.replace` with retry;
  in one process a second worker with the same manifest key waits for the
  first. A command with a relative path is not cached; a failed compile is not
  stored. `ObjectCache(None)` is the in-run store (`--no-object-cache`).
- Pool: `process.ordered_map` (threads driving child processes), default
  `os.cpu_count()`, `--jobs` override; results and link/archive order stay the
  sequential order; after the first failure no item starts.
- `host_build`: units, ABI probes and fixture units on the pool; `--cell` may
  repeat and `--all` builds every windows Cell, a shared library once per run;
  `--prune-object-cache`; the record gains `object_cache` (counts of this
  build). A `run_command` hook keeps observing real compiles (never served from
  the cache); a hook that declares `concurrent = True` receives
  `context["unit_index"]` and compiles run on the pool (WSB).
- `cross_build`: one job list over every (Cell, core, unit), so a dependency
  unit is compiled once per core and run, and served from the cache after.
- `registry.standard_include_root`: the pin check (two git calls) runs once per
  process for the same pin and checkout.
- Fixture links gained `--no-insert-timestamp --disable-auto-image-base`
  (`FIXTURE_LINK_FLAGS`): DCB built six different `dcb_host_fixture.dll`
  digests from one source set before; now one. A non-deterministic output
  would also defeat byte-identity checks of the cache.
- Release candidates: DCB, WSB and Sunset `package.py` pass
  `--no-object-cache` / `object_cache_mode="run"` (the common rule: a release
  candidate never reuses a development cache).

### 15.3 Measurements

Same machine (20 logical processors, loaded by other sessions throughout),
2026-11-03, sequential `build.py --cell <c>` for every Cell with a windows
target (DCB 14, WSB 9; `freertos` is arm only), then `cross_build.py --archive`
for every arm Cell; Sunlit: `providers/build.py --cell` and
`providers/cross_build.py --cell` for its 8 Provider Cells. Wall seconds.
"cold" starts from an empty cache (it fills during the sequence); "warm" is the
same sequence again; "no cache" is `--no-object-cache` (pool and in-run reuse
only).

| Product, step | Before | No cache | Cold | Warm |
|---|---|---|---|---|
| DCB host, 14 Cells | 668.4 | 171.8 | 138.2 | 72.6 |
| DCB cross, 13 Cells x 2 cores | 369.1 | 28.6 | 29.2 | 14.1 |
| DCB `build.py --all` (one run) | - | 43.6 | - | 19.9 |
| WSB host, 9 Cells | 277.0 | - | 63.6 | 78.8 |
| Sunlit host, 8 Cells | 104.7 | - | 37.4 | 27.6 |
| Sunlit cross, 8 Cells x 2 cores | 141.6 | - | 42.5 | 24.6 |

Per-Cell DCB host (before / cold / warm): horizon 4.5/9.4/2.7, engine
13.9/11.1/2.7, serial 62.1/27.3/7.6, tcp 69.4/9.4/6.8, udp 65.7/9.3/7.7, driver
64.0/12.7/7.9, binder 70.0/17.2/6.6, abi 84.2/6.5/6.4, host 100.0/8.6/6.3,
framing 6.5/4.7/2.2, shell 8.0/3.2/2.3, signal 3.0/4.0/2.2, windows
78.0/8.3/6.7, superloop 39.1/6.5/4.4. A warm DCB library build is now bounded
by its ABI probes (7 x 0.77 s on the pool for `tcp`), two links (1.1 s),
process start and the image record; probe results are not cached (their
source is generated in the run directory; a later increment can key them by
content).

WSB: its `run_command` hook (`native_build_evidence.observe`) runs a real,
observed compile plus a dependency scan for every product unit, so warm equals
cold within the load noise of the machine; the gain is the pool (product
units, probes, fixtures) and cached fixture objects and `nm` listings.

### 15.4 Byte identity

Digest of every output file except `tmp/` (run records compared with `run_id`,
`maintainer_tools`, `object_cache` and response-file records removed):

- DCB: cold = warm = no cache, 620 of 620 files (14 host runs and the cross run:
  libraries, `.def`, image records, fixtures, every Arm object and archive,
  `summary.json`). Before vs after: identical except the fixture libraries and
  their image records and the `fixtures` member of the build record (the
  reproducible fixture link, 15.2); every product library and Arm archive keeps
  its bytes.
- WSB: cold = warm; before vs after identical except the fixture libraries
  (same cause).
- Sunlit: cold = warm; host libraries and image records identical to before;
  the Arm archives differ from before only because `providers/cross_build.py`
  archived with `ar rcs`, which stamps member times (`Nov 03 11:08 2026`); it
  now uses `rcsD` (members dated 1970-01-01), so each revision gives one
  archive. Objects are identical.

### 15.5 Flags (measured, item 5 of the request)

Each variant built in-run only (`object_cache_mode="run"`), same machine.

| Variant | Time (s) | Result | Decision |
|---|---|---|---|
| Host `-O2 -ffunction-sections -fdata-sections -fno-ident`, link `--gc-sections` (current), `dcb_windows.dll`, `--jobs 1`, two runs | 56.9, 57.8 | 313,674 bytes, SHA-256 `23b0787f...` | kept |
| Host + `-pipe`, same | 74.2, 54.9 | same bytes | not adopted: no measurable gain under this load |
| DCB Cortex-M `-Os` with sections and `-fno-ident` (current), 13 Cells x 2 cores | 20.2 | text 112,314, every budget met | kept |
| DCB Cortex-M `-O2` instead of `-Os` | 23.3 | text 131,820 (+17.4 %); `serial` m0plus 2,088 > 2,048, `shell` m4 9,740 > 8,192 and m0plus 10,008 > 8,192, `signal` m4 2,680 > 2,560 | refused (budgets) |
| DCB Cortex-M without `-ffunction-sections -fdata-sections` | 21.3 | text 112,284 (-30, alignment) | kept with sections: an image link with `--gc-sections` drops unused functions only with them |
| DCB Cortex-M + `-pipe` | 19.5 | same sizes | not adopted: within noise |
| Sunlit Cortex-M `-ffreestanding -nostdlib -Os` (current), 8 Cells x 2 cores | 37.5 | text 120,566, every budget met | kept |
| Sunlit + `-ffunction-sections -fdata-sections -fno-ident` | 33.5 | text 120,560; per Cell and core -6 to +8 bytes (`osp` m4 +8, `sunshine_link` m4 +7), every budget met | not adopted now: some Cells grow by a few bytes and the gain appears only in a `--gc-sections` image link, which Sunlit does not measure; recommended once a firmware image link of a Sunlit Provider is measured |

No flag that changes semantics was tried or added. Every compile flag stays as
it was; the only link change is the fixture reproducibility (15.2).

### 15.6 Product adoption

| Product | Commit | Content |
|---|---|---|
| DCB | `07112980` | sync to `073aa7a`; `package.py` `--no-object-cache`; `local/c-build-and-test.md` names the cache, `--all`, `--jobs`, `--prune-object-cache` |
| WSB | `9c54b0d7` | sync to `073aa7a`; `product_hooks.py` `run_command.concurrent = True`, observations numbered by `unit_index`; `package.py` `--no-object-cache`; `local/c-build-and-test.md` |
| Hatbit template | `786c98e` | sync to `073aa7a` (also carries `7f58df9` and `c2bc7df`) |
| Sunset | `81cd577` | sync from the Hatbit template; `package.py` `--no-object-cache`; `local/c-build-and-test.md` |
| Sunlit | `0759bc0` | sync from the Hatbit template; `providers/build.py` and `providers/cross_build.py` compile through `comsect1_maint.object_cache` on the pool (`--jobs`, `--no-object-cache`), `ar rcsD`; `providers/registry.py` loads the package by file path |

### 15.7 Verification

- Template and Hatbit template: `comsect1_maint/test` 101 tests pass (17 new in
  `test_object_cache.py`: depfile parsing, absolute-path rule, ordered pool,
  miss then hit with identical bytes, cold = warm down to an `ar rcsD` archive,
  in-run store, header change, shadowing header, compiler identity, argument,
  environment, source and target keys, corrupt blob repair, relative command
  and failed compile not stored, `nm` listing cache, 8 threads plus 4 processes
  writing one entry, pruning by age and size through `99_trash`, linked cache
  root refused).
- DCB: every suite folder of `10_pkg/test` and `comsect1_maint/test`; all pass
  (`tool/package` 79 tests with the `--no-object-cache` release builds) except
  `tool/wsb` (3, need a WSB checkout and current profile, failing before this
  change too, record 14.2) and one run of
  `test_dcb_driver_timing...test_sixty_hertz_paced_send_over_tcp_through_two_drivers`
  under machine load (the suite passed 34 of 34 when run again).
- WSB: `build.py --all --run-tests` (9 Cells) passes; `tool/build`,
  `tool/package`, `tool/native_publication` pass; failures only where the
  environment lacks `WSB_NATIVE_TOOLCHAIN_ROOT` (`tool/gate`) or
  `WSB_FOUNDATION_TEST_ARCHIVE` (`python`), and `tool/registry` expects one
  `fixture_units` entry where WSB's `cells.json` has two (repository state,
  not the tools).
- Sunset: `build.py --all --run-tests` (4 Cells) passes; `tool/gate`,
  `tool/native_gate`, `tool/package` pass; `python` needs
  `SUNSET_VECTORCAST_TEST_ROOT`.
- Sunlit: `providers/build.py --cell <c> --run-tests` and
  `providers/cross_build.py --cell <c>` for all 8 Cells, `virtual_board` (25),
  `comsect1_maint/test` and `recipes.py check` pass.
- `sync.py --check` and `text_format.py` pass in all six repositories.

## 16. Consumed dependencies are recorded, not locked (r4, 2026-11-03)

Owner decision 2026-11-03: a consumer never re-verifies the executables or
libraries it consumes and never locks their version; a floor and interface
negotiation stay; content digests are metadata. The common policy is
`.instructions/consumed-dependencies.md`. Every product pinned the Foundation
(`fnd_sdk_standard.json`) at one commit and the build refused unless the
checkout was clean at exactly that commit; with the shared checkout at a newer
HEAD no product could build.

- `registry.standard_checkout` replaces the pin check: the root is the pin's
  `root_variable` or else its declared in-repository `deps_path` (section 17);
  it must exist and hold the public header. HEAD and dirty state are
  read once per process and recorded with the pinned commit and `matches_pin`;
  a mismatch or a dirty tree is never refused. The Foundation has no version
  notion (no release or tag), so no floor is defined.
- `host_build` writes `fnd_sdk_standard` into `build_record.json`;
  `cross_build` into `summary.json`, and `external_headers.checkout` per
  result. `Cell.external_roots` no longer requires the pinned commit or a
  clean tree.
- `native_gate`: the Gate binary digests are recorded beside
  `expected_sha256` (`tool_sha256`, `tool_sha256_recorded`,
  `tool_sha256_differs`); exit code 6 and `--accept-tool-hashes` are removed
  (also from `gate.py`). The toolchain manifest digest, member sizes and
  digests and the semantic runtime digest are recorded with every difference
  (`native_toolchain.members_differ`); the inventory as the complete directory,
  the run locks and the closure's resource membership stay.
- The object cache needed no change: its key is the bytes of the `-MD` closure
  read from the resolved root.
- Error handling (owner, same day: apart from ABI negotiation everything is a
  metadata-preserving read; strengthen error handling): the Foundation and
  external checkout errors name the variable, its value, the declared
  `deps_path`, the pin path, the pinned commit, the observed HEAD and dirty state and a next step,
  and carry them as `RegistryError.details`. An ABI probe failure is measured
  again (`_abi_mismatch`: gcc names each size, offset and constant in its
  int-to-pointer diagnostic, so nothing is run) and reports expected and found
  values, the mirror and header paths and a next step. A failed `host_build`
  run writes `build_record.json` with `result: failed`, `error`, `details`,
  the Foundation identity and the tools; a failed `cross_build` run writes
  `summary.json` the same way; unreadable metadata is a note. Export mismatch
  and Gate set errors name the paths, the release set and a next step.

## 17. A dependency is reached through its declaration (r5, 2026-11-12)

Owner decision 2026-11-12: no file in a repository contains an absolute path
that lies outside the repository root, nor a relative path that leaves it; a
repository reaches another repository only through a declared dependency
(`.instructions/consumed-dependencies.md` section 6). The Foundation pin held an
optional absolute `local_root`, and `registry.standard_checkout` refused a
checkout inside the repository, so a product had to store a machine path.

- The declaration is the Git submodule (`.gitmodules` and the gitlink) below
  `10_pkg/deps/`. The pin (`fnd_sdk_standard.json`) names its path in
  `deps_path` (`10_pkg/deps/fnd-platform-architecture`); `.gitmodules` must
  list that path, or the run fails with the `git submodule add <repository>
  <deps_path>` step. `deps_path` uses `/` separators, starts with
  `10_pkg/deps/` and names a folder there, and every further segment is a
  plain folder name (a letter or digit first, then letters, digits, `.`, `_`
  or `-`, no trailing dot or space): root, drive, backslash, stream, `.` and
  `..` forms fail closed, each with the rule it breaks. A pin that still
  holds `local_root` is a configuration error: a pin holds no machine path
  outside the repository.
- Resolution order, and nothing else: the pin's `root_variable` when the
  environment sets it to a non-empty value (an override the machine
  supplies); otherwise `<repository root>/<deps_path>`. A missing, empty or
  non-folder declared checkout (an uninitialized submodule) fails closed with
  the resolved path, how it was found, the pinned commit and the step
  `git submodule update --init <deps_path>` or setting the variable; an
  unreadable folder is reported the same way.
- One rule for every consumed checkout (`registry.dependency_checkout_root`,
  Foundation and `external_headers`): no link or reparse component on its
  path; inside the repository it lies below `10_pkg/deps/`, which holds no
  Cell of the consumer. The former refusal of an in-repository checkout is
  replaced by that rule. `external_headers` keeps environment-only
  resolution: the registry entry names the variable and stores no path, so
  it needs no declared path field; its checkout may now be a submodule below
  `10_pkg/deps/`.
- The native Gate admits a system include root inside the repository only
  below `10_pkg/deps/` (`native_gate.system_directories`). The Foundation and
  an adopted header tree stay system roots, the same `-isystem` search the
  host and cross builds use; routing them through `external_include_roots`
  would change their search order against the builds and treat a dependency
  checkout like the membrane of a consumer Cell.
- `observed_checkout` reads HEAD and the dirty state only when
  `git rev-parse --show-toplevel` is the checkout itself (a clone, or a
  submodule with its `.git` file). A plain folder inside the consumer's work
  tree records `head` and `dirty` as None with a `note`, never the consumer's
  identity.
- Records: `fnd_sdk_standard.source` is `environment` or `deps_path`; the
  resolved `root` and the observation `note` are recorded; no record or error
  `details` holds `local_root`. The pinned commit, observed HEAD, dirty state
  and `matches_pin` stay metadata, never a refusal.
- Toolchain location settings (`host_build.compiler_bin_dir`,
  `toolchain_bin_dir`, `hosted_include_roots`, the reference toolchain) are
  outside this step; the policy names them as a known open non-conformance.
