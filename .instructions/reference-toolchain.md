# Reference Toolchain

This is the common rule for where every repository finds the C toolchain
inputs it does not build itself. Decided by recommendation on 2026-11-12,
owner sign-off pending (Foundation decision log D19).

## Scope

Every repository that builds C is bound by this file, whatever tool builds
it. Sections 1 and 3 name the record and the records that the maintainer
tools (`20_ops/tools/comsect1_maint/`) carry; the rule itself does not depend
on them. A repository whose C build does not run the maintainer tools:

- takes the toolchain identity (bundle, version) only from its own
  synchronized copy of `20_ops/tools/comsect1_maint/reference_toolchain.json`,
  which synchronization delivers whether or not the repository runs the tools;
- locates the bundle only by the rule of section 2, and keeps no second pin,
  no toolchain version and no bundle location of its own;
- compiles every Cell as ISO/IEC 9899:2018 (C17/C18) only, written and
  verified as section 4 states, and respects section 5;
- records the pin, the reference tools root and how it was found in every run
  record its build writes, and never refuses on a digest (section 3);
- names, in its kind or local instructions, the build entry point that
  applies this rule, without restating the pin.

A repository that builds no C is not bound by sections 2 to 5.

## 1. One pin

`20_ops/tools/comsect1_maint/reference_toolchain.json` is the only pin record:
toolchain, version, bundle directory name, release tag, archive name and
SHA-256, provenance file name and SHA-256. It is template-owned and reaches a
repository only through the maintainer-tools synchronization (the
template's `sync.py --from <template> --to <repository>`,
[instruction layers](./instruction-layers.md)); a repository never edits or
copies it by hand. A version
change is one template commit followed by synchronization.

## 2. One location rule

The reference tools root is, in this order:

1. `COMSECT1_REFERENCE_TOOLS`, when set: an absolute existing directory. A set
   but unusable value is an error; it never falls through.
2. Otherwise the nearest ancestor of the repository root that holds
   `00_reference/tools/`, at any checkout depth.

The bundle is `<root>/<bundle>/` with its `provenance.json`
(`native_c_toolchain_inputs`: official release, archive SHA-256 and the size
and SHA-256 of every member). Nothing else is searched: no `LLVM_HOME`, no
Android NDK, no PATH, no per-product variable. A missing bundle fails closed
with the pinned archive name and digest and a next step.

## 3. Digests are records

The native Gate records the pin, the root and how it was found, and the
manifest, member and semantic runtime digests with every difference
(`native_toolchain` in `summary.json`). A difference is printed and recorded,
never refused ([consumed dependencies](./consumed-dependencies.md) section 3).
Content is verified once, when a bundle is built from the official archive.

## 4. One C standard

Every Cell, host and embedded, is ISO/IEC 9899:2018 (C17/C18), so that one
source compiles down to the embedded compilers (owner ruling 2026-11-13). The
rule may move to C23 later; until then no Cell uses a later edition.

The reference name is the formal one, ISO/IEC 9899:2018. "C17" and "C18" are
informal aliases of that same standard, not two editions: its
`__STDC_VERSION__` is `201710L`. Every place that selects the standard writes
the formal name beside the option, so an alias is never read as another
edition:

| Tool | Spelling of ISO/IEC 9899:2018 |
| --- | --- |
| Cell registry (`cells.json`) | `"standard": "c17"` |
| Native Gate lane (`native_gate_config.json`) | `"standard": "c17"`; the pinned LLVM analyses with `-std=c17` |
| GCC, Clang, `arm-none-eabi-gcc` | `-std=c17`, never `-std=gnu17` (GNU extensions off) |
| MSVC `cl` | `/std:c17` |
| CMake | `set(CMAKE_C_STANDARD 17)`, `set(CMAKE_C_STANDARD_REQUIRED ON)`, `set(CMAKE_C_EXTENSIONS OFF)`, with the comment `# ISO/IEC 9899:2018 (C17/C18), __STDC_VERSION__ == 201710L` |
| IAR | Standard C language mode, IAR extensions off |

The maintainer tools hold the standard once, in
`20_ops/tools/comsect1_maint/c_standard.py`; the registry, the host build, the
cross build and the native Gate lane read it there. The registry accepts only
that token and refuses any other by the formal name. The native Gate analyses
with the build standard and nothing else: no `frontend_standards` list and no
downgrade; a Gate frontend that cannot analyse it fails the lane closed with
the Gate's own message.

A lane's standard is verified by value, not by the name of an option: the host
build, the cross build and the native Gate lane preprocess the one-line probe
`__STDC_VERSION__` with the lane's own flags, once per distinct compiler and
flag set, fail closed unless it expands to `201710L`, and record the observed
value (`c_standard` in the build record and in `summary.json`). A build that
does not run the maintainer tools verifies the same value itself, for example
with `_Static_assert(__STDC_VERSION__ == 201710L, "ISO/IEC 9899:2018")` in one
compiled unit per lane.

Sources use only what that standard defines: `_Static_assert(expression,
"message")` as the one static-assertion spelling (a keyword, no header),
`bool` through `<stdbool.h>`, `(void)` for an empty parameter list, and none
of `nullptr`, `constexpr`, `typeof`, `[[attributes]]` or the empty
initializer `= {}`.

### Adopting the rule after a synchronization

Synchronization replaces the maintainer tools and updates this file; it never
writes a repository's own `cells.json` or `native_gate_config.json`. A
repository whose registry or Gate configuration still names another token is
therefore refused by the registry and by the Gate lane until it adopts the
rule. In the commit that takes the synchronization:

1. Make the sources compile as ISO/IEC 9899:2018 first (the list above); the
   build verifies the value and does not translate sources.
2. Set `"standard": "c17"` on every Cell of `cells.json`.
3. Set `build.standard` to `"c17"` in `native_gate_config.json`.
4. Give every build description outside the maintainer tools (a CMake file, an
   IDE project) the spelling of the table with the formal name beside it.
5. Run the build and the Gate; each record then holds the observed
   `__STDC_VERSION__`.

## 5. Bundle compatibility

The semantic runtime (`libclang.dll`) of the installed Gate set and the
pinned resource headers must have the same LLVM major: newer resource headers
use compiler-predefined macros an older libclang does not define (LLVM 23
`stdint.h` uses `__UINT64_C`, which libclang 19 lacks), and the analysis loses
its evidence. The pin moves to a new major only together with a Gate set whose
libclang has that major.
