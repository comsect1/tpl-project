# Artifact Placement

| Material | Location |
|---|---|
| Authored implementation, test code, fixtures, build inputs | `10_pkg/` |
| Declared dependency checkouts (Git submodules; not authored content, their Cells are not this repository's) | `10_pkg/deps/<name>/` |
| Inputs and constraints | `20_ops/01_inputs/` |
| Requirements and acceptance criteria | `20_ops/02_requirements/` |
| Architecture, design, and decisions | `20_ops/03_blueprints/` |
| Bounded work packages | `20_ops/04_work_orders/` |
| Development plans and receipts | `20_ops/05_development/` |
| Verification plans, cases, and reviewed results | `20_ops/06_verification/` |
| Validation plans, scenarios, and reviewed results | `20_ops/07_validation/` |
| Baselines, links, coverage, and gaps | `20_ops/08_traceability/` |
| Findings, dispositions, and readiness preparation | `20_ops/09_assurance/` |
| Packaging and release preparation | `20_ops/10_release/` |
| Executable tool scripts, build helpers, and runners | `20_ops/tools/` |
| Template-owned maintainer tool package and its tests | `20_ops/tools/comsect1_maint/` |
| Auxiliary dotfiles, third-party configs, and tool presets | `.configs/` (e.g., `.configs/cmake/`, `.configs/wsb/`) |
| Raw output, caches, traces, screenshots, staging | `90_temps/` |
| Issued immutable final packages | matching numbered `30_cert/` directory |

Raw output is not retained evidence merely because it exists. A reviewed
record in `20_ops/` binds stable IDs, relative locators, manifests, and digests.
Only an authorized issue operation creates a final package in `30_cert/`.
