# Template Adoption

Complete this checklist in the template-derived repository's first change:

1. Replace template identity in `README.md` and `AGENTS.md`.
2. Record the template upstream URL and exact baseline commit.
3. Select and record the downstream project's own license in accordance with
   `./.instructions/licensing-policy.md` before adding project material. The template
   scaffold is licensed under Apache-2.0 with Instantiation Exception, granting
   downstream work full independence.
4. Replace `unclassified` in `.instructions/kind/README.md` with the repository's
   durable kind classification and record why that classification applies.
5. Declare project ownership, purpose, package boundaries, and durable
   project-specific guidance in `.instructions/local/`; index every local file
   from `.instructions/local/README.md`.
6. Add language and tool output bindings to the `kind` or `local` layer that
   owns them. Bind the common development-feedback policy to the language's
   quickest feedback operation, affected-unit selection, reusable development
   cache path, and release-candidate command set. Do not add those bindings to
   template-managed common files.
7. Keep the complete folder scaffold; remove a `.gitkeep` only when real
   governed content replaces it.
8. Confirm `90_temps/` and `99_trash/` remain ignored except for placeholders.
9. Confirm no project directory is being represented as a Cell without a local
   `comsect1.json`.
10. Retain the root `.gitattributes` and `.editorconfig`, configure every added
   formatter or generator to emit LF, and run
   `python -I -B 20_ops/tools/text_format.py --apply` once before structural verification.
11. Confirm future template synchronization is configured to update common
    files while preserving `.instructions/kind/` and `.instructions/local/`.
    Synchronization also copies `20_ops/tools/comsect1_maint/`, the entry
    shims and the common instruction files
    (`comsect1_maint/sync.py --from <template checkout>`); `AGENTS.md` is
    merged by hand. Follow the procedure of
    [instruction layers](./instruction-layers.md) (template synchronization
    boundary) and record the template and its baseline commit in
    `.instructions/template-origin.json` (`comsect1.template-origin.v1`)
    exactly as it states. A repository that uses the maintainer
    tools writes `20_ops/tools/maint_config.json` and, only for product logic
    that is not data, `20_ops/tools/product_hooks.py`.
