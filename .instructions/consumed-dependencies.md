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
URL that the hosting service publishes for the dependency, for example
`git@<host>:<owner>/<repo>.git`, so that every workstation resolves the same
tracked declaration. A workstation that reaches the remote through a
per-account host alias applies the alias only in its own local Git
configuration, with `url.<base>.insteadOf` (`git config --global
url.<alias base>.insteadOf git@<host>:<owner>/`); a host alias, an account
name or any other per-workstation rewrite never appears in `.gitmodules` or in
any other tracked file. The maintainer tools read only the `path` of a `.gitmodules` entry and do
not judge its `url`; this rule is enforced by review.

### 6.1 Building a Cell of a dependency

A consumer that compiles a Cell of a dependency into its own libraries and
archives registers it as a dependency Cell: an entry of `cells.json` with the
key `dependency`, the submodule path of the dependency.

```json
"<name>": {
  "path": "<name>",
  "dependency": "10_pkg/deps/<dependency>",
  "standard": "c17",
  "definitions": {},
  "include_roots": ["membrane", "."],
  "depends_on": [],
  "freestanding": true,
  "targets": {
    "windows": {"gate": {"target": "x86_64-pc-windows-msvc", "plain_char": "signed"}},
    "arm": {"gate": {"target": "thumbv7em-none-eabihf", "plain_char": "unsigned"},
            "cross_cores": ["cortex-m4"]}
  }
}
```

The Cell root is `<dependency>/10_pkg/c/<path>`. Another Cell names the entry
in `depends_on` like any Cell and includes the public header by its file name.

| Subject | Rule |
| --- | --- |
| Declaration | `dependency` is a submodule path that `.gitmodules` lists below `10_pkg/deps/`. The checkout is resolved at that path only; no variable overrides it. |
| Refused, each with its reason and next step | a `dependency` that is no valid path below `10_pkg/deps/`; a path `.gitmodules` does not list; a missing, empty or non-folder checkout (an uninitialized submodule: `git submodule update --init <path>`); a link or reparse component; no `comsect1.json` at the Cell root; a manifest that does not name the language `c` or does not declare exactly one public identity surface; a published header that is missing. |
| Not refused | the commit of the checkout and a dirty tree (section 1). The build record (`dependency_cells`) and the cross-build result (`dependency`) keep the checkout root, the pin under `10_pkg/components/` whose `deps_path` names the submodule with its commit, the observed HEAD, the dirty state and `matches_pin`. |
| Identity | read from the Cell's own `comsect1.json`: the one `public_identity_surfaces` entry gives the identity (`identity`) and the public header (`path`, `membrane/xpt_*.h`). The consumer restates neither and never edits the checkout. The registry and the tool configuration of the dependency's repository are not read. |
| Names | the archive is `lib<identity>.a`, without the consumer's product prefix. The public functions are `<identity>_*`, or `<function_prefix>*` when the entry declares `function_prefix`. |
| Entry keys | the keys every Cell holds, which are the consumer's flags, and optionally `function_prefix` and `budget`. `library_from`, `link_with`, `link_libraries`, `exports`, `hosted_include_roots`, `test_fixture`, `fixture_units`, `link_fixture`, `external_headers`, `passive_include_roots` and `sdk_dependencies` are refused. A dependency Cell is no Provider and depends only on dependency Cells. |
| C standard | one standard for every Cell. A Cell manifest carries no edition, so the entry carries the token of `c_standard` and the edition is verified by the value of `__STDC_VERSION__` under the consumer's flags; the dependency's sources then compile under those flags and the consumer's warning bar, or the build fails. |
| Host build | the sources are compiled from the checkout with the consumer's flags and linked into the library of every Cell that depends on the entry; its membrane is their include root. The entry has no library of its own: `build.py --cell <entry>` is refused and `--all` skips it. A library that lists the entry in `exports` exports the functions of its public header through the `.def` file of the run directory. |
| Cross build | the entry is cross-built for its `cross_cores` like every freestanding arm Cell; `--archive` writes `<run>/<name>/<core>/lib<identity>.a`. The archive of a Cell that depends on it also holds its objects, as for every dependency. |
| Tests | none in the consumer: no test folder and no ABI mirror exist or are required for the entry. Its tests run in its own repository (section 4). |
| Gate | `gate.py --cell <entry>` is refused: the verdict on the Cell is its owner's. Its membrane is an external include root of the Cells that depend on it. |
| Packaging | `Cell.dependency_deliverables()` gives the product's packaging code the identity, the public header and the contract headers relative to the Cell root, the archive name and its path per core below a cross-build run, and the `LICENSE.md` (required) and `NOTICE.md` (when present) of the checkout root, so a product ships them under the dependency's own names. |
| Writes | none below the checkout: every object, archive, `.def` file and record lies below the consumer's `90_temps/`. |

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
