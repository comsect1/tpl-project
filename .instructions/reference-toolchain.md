# Reference Toolchain

This is the common rule for where every repository finds the C toolchain
inputs it does not build itself. Decided by recommendation on 2026-11-12,
owner sign-off pending (Foundation decision log D19).

## 1. One pin

`20_ops/tools/comsect1_maint/reference_toolchain.json` is the only pin record:
toolchain, version, bundle directory name, release tag, archive name and
SHA-256, provenance file name and SHA-256. It is template-owned and reaches a
repository only through the maintainer-tools synchronization
(`sync.py --from`); a repository never edits or copies it by hand. A version
change is one template commit followed by synchronization.

## 2. One location rule

The reference tools root is, in this order:

1. `COMSECT1_REFERENCE_TOOLS`, when set: an absolute existing directory. A set
   but unusable value is an error; it never falls through.
2. Otherwise the nearest ancestor of the repository root that holds
   `00_reference/tools/` (on the workbench `F:/repos/00_reference/tools/`, at
   any checkout depth).

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

Every Cell is C23. The registry accepts only `"standard": "c23"`, and the
native Gate analyses with the build standard and nothing else: no
`frontend_standards` list and no downgrade. A Gate frontend that cannot
analyse C23 fails the lane closed with the Gate's own message.

## 5. Bundle compatibility

The semantic runtime (`libclang.dll`) of the installed Gate set and the
pinned resource headers must have the same LLVM major: newer resource headers
use compiler-predefined macros an older libclang does not define (LLVM 23
`stdint.h` uses `__UINT64_C`, which libclang 19 lacks), and the analysis loses
its evidence. The pin moves to a new major only together with a Gate set whose
libclang has that major.
