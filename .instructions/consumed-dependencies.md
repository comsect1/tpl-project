# Consumed Dependencies

This is the common policy for how a repository consumes an SDK, a RUNTIME, a
library, a tool or a checkout that another repository issues (the Foundation
checkout of `host_build.standard_pin`, an adopted `external_headers` tree, an
installed SDK, the Gate set and its toolchain). Owner decision 2026-11-03.

## 1. No consumer locks

A consumer never re-verifies the executables or libraries it consumes and
never locks their version (owner, 2026-11-03: apart from ABI negotiation,
everything is a metadata-preserving read). The build, test and run tools of a
consumer, and a runtime engine admitting a Provider library, do not refuse on:

- an exact commit pin, or a pinned checkout that is not clean at that commit;
- an exact version or an upper version bound, or an exact SWID `tagId`;
- a content digest that differs from a recorded one (a tool pin such as
  `expected_sha256`, an admission record or a Provider manifest digest).

## 2. Allowed

- A minimum version (floor), `"minimum_version": "<semver>"`, where the
  dependency has a version notion and a real minimum exists.
- Interface negotiation, the only condition that gates: the C ABI identity
  and layout, Provider negotiate, the MCP protocol revision.
- Data-format integrity needed to parse bytes safely: a frame checksum, a
  binary record or profile trailer that frames the bytes being read.
- Structural resolution: the dependency is found at its declared location
  (section 6) and holds the file the consumer includes or loads. Nothing can
  be compiled or run otherwise.

## 3. Pins and digests are metadata

A pin file (`10_pkg/components/*.json`) names the repository a dependency
comes from, the commit it was last pinned at, the variable that overrides its
location and the path of its submodule (`deps_path`, below `10_pkg/deps/`); it
holds no machine path outside the repository. A content digest covers the
inside of a deliverable only, excluding its root folder name. The pinned
commit and a digest are read and recorded for debugging, never a condition;
`deps_path` and the override variable are resolution inputs.

The C build tools resolve the Foundation root from the pin's `root_variable`
when the environment sets it to a non-empty value, otherwise from its
`deps_path` below the repository root, and from nothing else. `deps_path` must
be a submodule path that `.gitmodules` lists. A checkout inside the repository
lies below `10_pkg/deps/`, and a checkout path holds no link or reparse
component. HEAD and the dirty state are read only from a checkout that is its
own Git work tree (a clone, or a submodule with its `.git` file); otherwise
the record carries a note instead. The tools record `fnd_sdk_standard` (root,
source `environment` or `deps_path`, `pinned_commit`, observed `head`,
`dirty`, `note`, `matches_pin`) in `build_record.json` and the cross-build
`summary.json`; an `external_headers` checkout, found only through its
environment variable, is recorded the same way per result. The native Gate
records the actual Gate binary digests beside the recorded `expected_sha256`
(`tool_sha256_differs`) and the toolchain manifest, member and semantic
runtime digests with every difference (`native_toolchain`).

The object cache keys a compile by the bytes of the headers actually read
([build optimization](./build-optimization.md)), so build correctness never
rests on a pin.

## 4. Where verification stays

Verification of content belongs to the issuing side and the install step, not
to a consumer's build or run:

- install time: `install-sdk --expect-tag-sha256`;
- issue time: a product's own `package.py --issue` reproducibility and
  release checks over its own deliverable.

A repository's own synchronized common layer (`sync.py --check`, the
maintainer tools drift check) is the repository's own content, not a consumed
dependency.

## 5. Error handling

Every failure that remains (a dependency not found or not loadable, an ABI
mismatch, a missing export, a Provider open failure, an unresolvable
Foundation root) names the resolved path and how it was found, the version,
tag id and observed SHA-256 read from metadata, the expected and found ABI
values, and one concrete next step. The same information is written into the
build or run record of the failed run (`build_record.json` and the
cross-build `summary.json` carry `result: failed`, `error` and `details`).
Metadata that cannot be read is a note in the record, never a failure.

## 6. Declaring a dependency on another repository

Owner decision 2026-11-12: a repository names another repository's location
in exactly one declared place, and in no other file:

- a Git submodule under `10_pkg/deps/<name>` for source or headers the
  consumer compiles or imports: `.gitmodules` and the gitlink are the
  declaration. A pin file under `10_pkg/components/` may describe it; its
  `deps_path` names the same submodule path and nothing else; or
- an installed deliverable, found by the install location rule of its
  product or by an environment variable the consumer names. The consumer
  stores no install path.

Source, build files, scripts, tests and current documents reach the
dependency by a path derived from the repository's own root or by a variable
the declaration names. No file in a repository contains an absolute path that
lies outside the repository root, nor a relative path that leaves it; paths
inside the repository are unrestricted. A variable that overrides the
declared location stays an override: the default comes from the declaration.
An override may point outside the repository or at a checkout below
`10_pkg/deps/`. A checkout below `10_pkg/deps/` is not authored content, and
a Cell inside it is not a Cell of the consumer. Dated run records and issued
evidence state what was run and are not rewritten when a location changes.

The `url` that `.gitmodules` records for a submodule is the canonical remote
URL of the dependency, in the form `git@github.com:<owner>/<repo>.git` for a
repository hosted on GitHub, so that every workstation resolves the same
tracked declaration. A workstation that reaches the remote through a
per-account SSH host alias applies the alias locally, in its own Git
configuration, with `git config --global url.<alias base>.insteadOf
git@github.com:<owner>/`; a host alias, an account name or any other
per-workstation rewrite never appears in `.gitmodules` or in any other tracked
file. The maintainer tools read only the `path` of a `.gitmodules` entry and do
not judge its `url`; this rule is enforced by review.

Known open non-conformance: the maintainer tool configuration still requires
absolute toolchain and system include locations (`compiler_bin_dir`,
`toolchain_bin_dir`, `hosted_include_roots`). These locations are to move to
environment or layout resolution; until then, they are the only stored paths
outside the repository that this policy tolerates. Each key is retired on its
own when the reference toolchain pin ([reference toolchain](./reference-toolchain.md))
provides the input that key locates: from the template commit that adds that
input to the pin, the maintainer tools resolve it through the pin and the
reference tools location rule only, the key is removed from every
configuration, and the tools refuse a configuration that still holds it, as
they refuse `local_root` in a pin.
