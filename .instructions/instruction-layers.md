# Instruction Layers and Precedence

Every durable instruction must have one declared ownership layer. Classification
prevents template updates from erasing project knowledge and prevents temporary
work decisions from silently becoming permanent policy.

## Layers

1. **Common** — Root `.instructions/*.md` files supplied by the project
   template. They define invariants and operational rules shared by every
   downstream project. The template owns and may update these files.
2. **Kind** — `.instructions/kind/`. The repository owns its repository-kind
   classification and guidance shared by repositories of that kind, such as an
   application, tool, platform, library, driver, runtime, trial, or another
   explicitly named kind. The template supplies only the classification slot.
3. **Local** — `.instructions/local/`. The repository owns durable instructions unique
   to this project, including its product boundaries, subsystem responsibilities,
   user-interface rules, domain constraints, and project-specific verification.
4. **Task-scoped** — Authorized records in `20_ops/`. They govern one bounded
   change, experiment, investigation, or release operation and expire with that
   scope unless deliberately promoted and reclassified.

The examples above are descriptive, not a closed global taxonomy. A template-derived repository must
state its chosen kind and rationale instead of inferring it from a repository
name, umbrella category, or source layout.

## Precedence and conflict

- Governing specifications, explicit external contracts, and license boundaries
  retain their own authority and cannot be weakened by repository instructions.
- Common invariants apply to every template-derived repository. Kind instructions may narrow them for a
  declared repository class; local instructions may narrow them for one project.
- A more specific layer may add constraints or choose among options explicitly
  left open by a broader layer. It may not contradict or weaken the broader
  invariant.
- Task-scoped records authorize bounded work; they do not override durable
  common, kind, or local rules merely because they are newer.
- When two instructions conflict or their ownership is unclear, stop, record the
  conflict, and change the owning layer through an authorized decision. Do not
  resolve the conflict by accidental file order or duplication.

## Placement and promotion

- Keep `AGENTS.md` concise: state core invariants and route readers to the owned
  instruction indexes. Put detailed project-specific guidance in `local/`.
- Put a durable rule in the narrowest layer that fully owns it. Do not copy the
  same rule into several layers.
- Promote task-scoped learning only after review establishes that it is durable,
  identifies its owner, and updates the correct kind or local index.
- Preserve rationale in the owning instruction or its linked decision record;
  do not preserve obsolete generations as permanent guidance.

## Template synchronization boundary

Template synchronization may add or update common files and, by hand, the
common sections of `AGENTS.md` (below). It must treat `.instructions/kind/` and
`.instructions/local/` as downstream-owned: never overwrite, delete, reset, or
silently reclassify their contents. Report a conflict for human resolution.

The common layer also holds executable content: the maintainer tool package
`20_ops/tools/comsect1_maint/**` (its tests included), the entry shims
`20_ops/tools/native_gate.py`, `cell_registry.py`, `cross_build.py`, `build.py`
and `gate.py` (a Rust repository keeps its own `build.py` and `gate.py`; only
`cell_registry.py` is common there), and `20_ops/tools/text_format.py`. They
are copied verbatim, never merged: a downstream repository never edits them, a
local edit is a defect, and a needed change goes upstream and comes back by
synchronization. `python -I -B 20_ops/tools/comsect1_maint/sync.py --from
<template checkout>` copies them and prints the baseline commit to record in
`.instructions/template-origin.json`; `sync.py --check` reports a `MODIFIED`,
`MISSING` or `EXTRA` common file. The local counterpart is
`20_ops/tools/maint_config.json`, `cells.json`, `native_gate_config.json`, the
optional `product_hooks.py` and every other file of `20_ops/tools/`;
synchronization never writes them.

### Common instruction files

The same synchronization carries the common instruction files: every `*.md`
directly in `.instructions/`. The template's manifest
(`comsect1_maint/sync_manifest.json`, member `instructions`) records their
SHA-256, and `sync.py --check` reports each `MODIFIED`, `MISSING` or `EXTRA`
one; the build and Gate drift check covers only the tool files.
Synchronization never reads or writes `.instructions/kind/`,
`.instructions/local/` or `.instructions/templates/`, and only reads the
`baseline_commit` of `.instructions/template-origin.json`. A common
instruction file is merged three ways, against the template's file at the
repository's recorded `baseline_commit`:

| Repository's file | Result |
| --- | --- |
| absent | copied (`ADDED`) |
| equal to the template's current file | left alone |
| equal to the template's file at the baseline | replaced by the current file (`UPDATED`) |
| changed here; the template's file unchanged since the baseline | kept unchanged (`KEPT`) |
| changed here and in the template since the baseline, or the baseline is unknown | kept unchanged and reported (`CONFLICT`, exit 1) |

A file is never overwritten when the repository changed it. In an ordinary
repository a `KEPT` or `CONFLICT` file is a local edit of a common file, a
defect that `--check` keeps reporting as `MODIFIED` until it is reverted or
its change goes upstream. A file that the template no longer supplies is left
in place and reported `EXTRA`; remove it by hand through `99_trash/`. The
baseline must be a commit that the template checkout holds (a shallow clone
that lacks it turns every differing file into a `CONFLICT`).

### Derived template

A derived template is a template that synchronizes from this one and is
itself the `--from` source of its own members. It owns its adaptations of
common instruction files and its additional common instruction files:

1. Synchronize from the upstream template. An adapted file is `KEPT` when the
   upstream did not change it, or a `CONFLICT` when it did; merge the upstream
   change (`git -C <upstream> diff <old baseline> <new commit> -- <file>`) into
   the adaptation by hand. Its additional files are not in the upstream
   manifest and are left untouched.
2. Run `sync.py --write-manifest` in the derived template. Its manifest then
   records its own common set, adaptations and additional files included, and
   `--check` passes.
3. Commit. Its members synchronize from that commit and receive the derived
   template's files, adaptations and additional files included, by the same
   three-way rule against their own recorded baseline, which is a commit of
   the derived template.

### `AGENTS.md`

`AGENTS.md` merges the repository's title and identity with the common
sections of the template and is never copied by a tool. After `sync.py
--from`:

1. List the template change: `git -C <template> diff <old baseline_commit>
   <new commit> -- AGENTS.md`, where the new commit is the `baseline_commit`
   that `sync.py --from` printed.
2. Apply every changed common section (every section after the title and the
   repository's identity statement) to the repository's `AGENTS.md` by hand,
   with the template's wording. Keep the repository's title and identity
   statement; a derived template also keeps its adaptations of common
   sections and merges the change into them. Project-specific guidance
   belongs in `.instructions/local/`, not in `AGENTS.md`.
3. Resolve every instruction `CONFLICT` the same way, run `sync.py --check`
   (in a derived template after `--write-manifest`) and
   `python -I -B 20_ops/tools/text_format.py`.
4. Only then set `baseline_commit` in `.instructions/template-origin.json` to
   the full 40-digit commit that `sync.py --from` printed: the `HEAD` of the
   template checkout it synchronized from, which is the direct template (a
   member of a derived template records the derived template's commit, never
   the upstream's). Never record an abbreviated hash, the repository's own
   commit, or a template commit whose `AGENTS.md` change has not been applied.
   Commit the synchronized files, the merged `AGENTS.md` and the origin record
   together.
