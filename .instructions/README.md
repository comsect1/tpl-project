# Project Instruction Index

These instructions explain the concrete scaffold already present in this
repository. They do not substitute for the folder tree and do not constitute
Comsect1 Specification law.

- `artifact-placement.md`: authored, process, evidence, and issued-package paths
- `lifecycle-and-traceability.md`: continuous development and V&V flow
- [development-feedback-and-release-evidence.md](./development-feedback-and-release-evidence.md): fast feedback, release-only evidence, and disposable cache boundary
- [build-optimization.md](./build-optimization.md): measured build speed, the shared object cache, parallel compilation, determinism and flag rules of the C build tools
- [consumed-dependencies.md](./consumed-dependencies.md): no consumer locks; pins and digests are recorded metadata; floors and interface negotiation only
- [reference-toolchain.md](./reference-toolchain.md): the one synchronized toolchain pin, the one reference tools location rule, and C23 as the only C standard
- [temporary-output-and-cleanup.md](./temporary-output-and-cleanup.md): disposable output safety boundary
- [fork-adoption.md](./fork-adoption.md): mandatory identity and tailoring checklist of a template-derived repository
- [licensing-policy.md](./licensing-policy.md): template, downstream, and third-party license boundaries
- [refactoring-and-updates.md](./refactoring-and-updates.md): current-state replacement, path normalization, and refactoring
- [version-and-compatibility.md](./version-and-compatibility.md): release authority, non-semantic work revisions, and compatibility boundaries
- [instruction-layers.md](./instruction-layers.md): common, kind, local, and task-scoped ownership and precedence
- [kind/](./kind/README.md): repository-owned kind classification and durable kind guidance
- [local/](./local/README.md): repository-owned project-specific instruction index
- [text-format-policy.md](./text-format-policy.md): canonical text bytes, verification, and explicit automatic correction
- [readme-template.md](./readme-template.md): the fixed structure of the external product `README.md`
- [runtime-location-and-environment.md](./runtime-location-and-environment.md): family runtime home grouping (`<family>`), platform base paths, dot-prefix (`.`) rule, and `<FAMILY>_HOME` override hierarchy
- [comsect1-cell-authoring.md](./comsect1-cell-authoring.md): self-contained Cell control, zone, role, classification, and prefix decisions
- [templates/](./templates/): reusable work, readiness, issue, and cleanup records
