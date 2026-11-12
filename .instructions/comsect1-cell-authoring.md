# Comsect1 Cell Authoring Model

This instruction is complete for ordinary Cell authoring and refactoring. Do
not consult another repository to decide a zone, role, classification, prefix,
or local control-file placement.

## One local control authority

`comsect1.json` is the only Comsect1-authored control file inside a Cell. It
contains only facts that a tool cannot derive from the governed tree, such as
Cell kind, languages, selected conventions, feature roots, public identity
surfaces, and composition intent.

Do not create or update any of the following:

- `contract/projection/<language>/metadata.json`;
- `contract/convention/pvo_convention_activation.json`;
- `comsect1-role:`, `SSOT:owner`, `deps:`, or other Comsect1-only source
  comments; or
- a language-specific Cell control file beside `comsect1.json`.

Structural verification may inspect the actual governed files as its subject.
Derived inventories, projections, observations, findings, and evidence belong
outside the Cell.
Product-owned JSON contracts and runtime configuration remain ordinary product
artifacts; this rule forbids additional Comsect1 control surfaces, not JSON as
a medium.

A `comsect1.json` inside a declared dependency checkout (`10_pkg/deps/<name>/`)
belongs to that dependency: its Cell is not a Cell of this repository and is
not authored or refactored here.

## Three independent decisions

Choose a zone, a semantic role when applicable, and a classification in that
order. They are separate axes. A prefix never creates authority, a dependency,
an access exception, or a call hierarchy.

| Zone | Admitted prefixes | Responsibility |
|---|---|---|
| `core/` | `smo_`, `rmp_`, `efx_` | Feature meaning and its dependent role family |
| `core/` | `cfg_`, `dat_` | Governed configuration and data without a semantic role |
| `contract/` | `pvo_` | Passive bidirectional promises, imported external representations, required operations, and shared vocabulary |
| `membrane/` | `xpt_` | Outward consumer-addressable publication of this Cell |
| `capability/` | `csr_` | Cross-feature runtime state, events, and streams |
| `capability/` | `csf_`, `csp_`, `chf_`, `cbf_` | Role-less service, pipeline, and hardware facilities |

The following stems are prohibited in every zone and are structural
violations wherever they appear. `src_`, `med_`, `rea_`, `contract_`, `api_`,
`stm_`, and `db_` have one current stem of the same meaning (`smo_`, `rmp_`,
`efx_`, `pvo_`, `xpt_`, `csr_`, `dat_`). `svc_`, `mdw_`, `hal_`, and `bsp_`
have none: place the artifact again by responsibility. `voc_`, `apx_`, and
`esc_` have no current counterpart.

All four zone directories exist at the Cell root. A Cell does not gain another
zone from a language, framework, package layout, or prefix.

## Core role families

For one lower-snake-case stem `S`, `smo_S` is the sole semantic owner.
`rmp_S` and `efx_S` are optional sibling dependents in the same feature root;
they are not required stages in a `smo_ -> rmp_ -> efx_` call chain. An observed
`include`, `import`, `call`, data access, or symbol reference may remain within
one role or move only from SEMANTIC_OWNER toward REPRESENTATION_MAPPING toward EFFECT_EXECUTION. It may
skip an absent stage, but it must not move in the reverse direction. Derived
SEMANTIC_OWNER lineage states semantic ownership and is assessed by the
separate lineage laws, not as consumption.
Generic dependency and module-mount records describe topology; they never
stand in for the parser-proven final-target reference or use this rule judges.

- `smo_` owns decisions, policy, invariants, domain meaning, state-transition
  rules, validation criteria, and interpretation of the Cell's promises. It
  keeps that ownership when it calls a library, driver, protocol, or platform.
- `rmp_` maps between two named semantic representations while preserving the
  meaning already owned by its `smo_`. It owns neither product policy nor an
  irreversible effect. Syntax helpers and thin boundary wrappers are not
  representation mapping merely because they translate bytes or types.
- `efx_` executes or progresses an already-decided effect or external state
  step through a near-simple port. It may persist or publish under a
  `smo_`-owned transition, but it must not select sequencing, retry, default,
  validation policy, or the sole meaningful service behavior. Feature-local
  `efx_` may consume lower `chf_`/`cbf_` hardware facilities; their existence
  does not move the feature's adaptation into capability.

A dependent replaces only the prefix of its exact SEMANTIC_OWNER stem. Optional leaves
use `rmp_S_<subtype>` or `efx_S_<subtype>`. Within each dependent role, an
unsplit parent and its split children cannot coexist: use either `efx_S` or
leaves such as `efx_S_read` and `efx_S_write`, never all three. Representation mapping and
effect execution split independently.

Filename and physical sibling placement provide the mechanical family
identity. `comsect1.json` declares feature roots and other closed Cell identity
facts; it does not repeat every artifact, path, symbol, role, observed call,
or derived relation.

## Role-less surfaces

- `pvo_` is passive and bidirectional. It imports the representation of a
  library the Cell adopts as-is (types, constants, vocabulary, and
  operations), and Core, `smo_` included, uses that imported representation
  directly and calls the library's algebra through it. Restating it as a
  Cell-local mirror type, parallel shape, forwarding function, or translation
  copy is not required and is itself a wrapper finding. An adopted
  operation is algebra when its result depends only on the values passed, the
  adopted representation, and memory in the running image, and it changes
  nothing outside that memory. An operation that reads or changes a file,
  device or register, bus, network, clock, persistent storage, or OS service
  is an effect: it stays behind a required operation or in `efx_`, with its
  decision in `smo_`, even when its library is adopted. The hosted standard
  library (C hosted headers, Rust `std`/`core`/`alloc`) is the platform, not
  an adopted library. A dependency the Cell may replace
  (board, chip, medium, transport, OS backend) is instead declared in the
  contract as a required operation in the Cell's own vocabulary and bound when
  the Cell is built. Calls, effects, executable behavior, behavioral macros,
  derive-driven wire translation, mutable state, orchestration, and policy do
  not belong in `contract/`.
- `xpt_` is exclusively outward-facing. A Cell's own Core and capabilities do
  not call its membrane as an internal acquisition path.
- `cfg_` and `dat_` are governed material, not disguised SEMANTIC_OWNER owners.
- `csr_`, `csf_`, `csp_`, `chf_`, and `cbf_` are classifications, not roles.
  They do not form a promotion ladder or imply allowed dependency directions.
- A cross-feature interaction uses a role-less seam selected by meaning:
  shared state through `csr_`, shared shapes through `pvo_`, explicit
  composition through a Core composition table, and an independently justified
  mechanism through a capability. A forwarding wrapper is not a seam.
- Another Cell is consumed only through its `xpt_` membrane surface.

## Required decision procedure

Before creating, moving, or renaming an artifact:

1. Identify the exact Cell and the responsibility it owns.
2. Select the zone from that responsibility, not from the file type.
3. If it is feature meaning, identify the feature root and its one `smo_`
   owner.
4. Add `rmp_` only for a named meaning-preserving representation mapping.
5. Add `efx_` only for an effect whose policy remains in the sibling `smo_`.
6. If the responsibility is passive vocabulary used in either direction,
   choose `pvo_`. Import an adopted library there once and let Core use its
   representation and call its algebra directly; declare a replaceable
   dependency there as a required operation in the Cell's own vocabulary.
   `smo_` never includes or imports the adopted library itself, and an
   adopted operation with an external effect still belongs to porting or
   `efx_`.
7. Choose `xpt_` only to publish the Cell's outward API. Never use the local
   membrane to absorb or acquire an inbound dependency.
8. Choose `csr_`, `csf_`, or `csp_` only when state, service, or pipeline
   responsibility is genuinely shared across features. A feature-local
   external form remains `rmp_`; its decided effect remains `efx_`.
9. Record only Cell identity, selected profiles, feature roots, public identity surfaces, and composition intent in `comsect1.json`.
10. Reject a change that merely relabels, re-exports, forwards, or moves an edge
   to silence a finding. Importing an adopted representation once in the
   contract is not such a move: the crossing stays visible and accounted
   there.

For Rust, a non-standard name under
`composition_context.hosted_vocabularies[].packages` states exact Cell intent;
it does not authorize an arbitrary package. Structural verification can account
that package only where runtime Cargo facts prove the observing artifact has
the same normal, nonoptional, non-path dependency applicable at every observed
occurrence. A direct package dependency qualifies. A package member's explicit
`workspace = true` adoption qualifies only when it resolves one exact
nonoptional, non-path workspace dependency. The workspace-root declaration
alone, and optional, dev, build, path, transitive or ambiguous dependencies, do
not qualify. A target-specific dependency qualifies only when there is one
exact adopted dependency and the complete conjunction of guards on every
occurrence entails its target predicate under the Rust profile's bounded proof.
Unguarded, differently guarded, mixed or incompletely parsed occurrences remain
unproved. This does not make the dependency unconditional or use the host
platform to erase source. Even a qualifying proof grants availability only. An
external path that SEMANTIC_OWNER or membrane writes itself remains forbidden.
An adopted library enters through the contract's own `use` or type alias, which
Core then names and calls directly; translation the Cell writes itself and
effects belong to the truthful REPRESENTATION_MAPPING, EFFECT_EXECUTION, or
genuinely Cell-wide capability owner. In C, a contract header may use
object-like macros from the header it includes; function-like macros and calls
stay out of `contract/`.

Rust internal modules require no authored relation. When immutable Cargo
manifests are available, structural verification seeds crate roots from exact
package target entries and follows only parser-proven `mod`, inline-module, and `#[path]`
mounts. A selected file that is neither a target entry nor reachable through
that AST graph remains unresolved. Cargo facts establish build topology only;
they never grant a zone, role, feature, dependency exception, or composition
identity, and no `comsect1.json` member or sidecar may replace a missing bind.

A meaningful `smo_controller` that decides retry policy may call a replaceable
`efx_controller_transport`; moving the retry policy into the effect execution and
leaving a forwarding SEMANTIC_OWNER is invalid. A `rmp_controller_frame` is justified
only when it maps a named controller representation to a named frame
representation without choosing retry or transport behavior.

The following matrix is the minimum semantic mismatch check. These are design
findings, never mechanical moves or relabels.

| Invalid owner | Observable evidence | Correct owner | Verification disposition | Mechanical correction disposition |
|---|---|---|---|---|
| Active `pvo_` | call, executable definition, behavioral macro, or derive-driven wire mapping | `smo_` policy, `rmp_` representation, or `efx_` decided effect; outward declaration only in `xpt_` | REWORK or REVIEW_REQUIRED with the exact source observation | no edit candidate; retain as AI/human rework |
| Inbound `xpt_` membrane | local Core/capability import, provider decode, inbound adaptation, or direct external call | passive type narrowing in `pvo_`, representation in `rmp_`, effect in `efx_` | REWORK with the exact inbound edge | no automatic move or wrapper |
| Own external import in `smo_` | package/header prefix in the `smo_` artifact's own include, import, qualified path, derive, symbol, or call, not reached through its contract import | contract import of the adopted representation, used directly; `rmp_`/`efx_` for mapping or effects | unresolved REWORK; aliases outside the contract do not discharge it | no declaration synthesis or prefix rewrite |
| Mirror of an imported representation | Cell-local type, constant set, forwarding function, or translation copy restating what the contract already imports, with no meaning of its own | the contract import itself, used and called directly by Core | wrapper-sufficiency review finding; not mechanically detected | no edit candidate; retain as AI/human rework |
| Adopted effect in policy | Core call into an adopted library operation that reads or changes a file, device, bus, network, clock, storage, or OS state | required operation in the contract, or `efx_` over the decision in `smo_` | design review finding; the call itself binds to the contract | no edit candidate; retain as AI/human rework |
| Policy or effect in `rmp_` | branch selects retry/default/transition, or performs irreversible I/O | policy in sibling `smo_`; already-decided effect in sibling `efx_` | semantic role REWORK | no AUTO_FIX or role rename |
| Policy or orchestration in `efx_` | effect code selects sequencing, retry, validation policy, or cross-feature lifecycle | sibling `smo_` policy or genuinely cross-feature `csf_`/`csp_`; keep only leaf effect in `efx_` | semantic role REWORK | no AUTO_FIX; require explicit redesign |
| Feature-local capability bucket | `csf_`/`csp_` merely hides one feature's representation/effect or forwards its call | sibling `rmp_` representation and `efx_` effect; `smo_` remains owner | cross-feature/ownership REWORK | no move plan or forwarding seam |

For example, `pvo_clock` imports the vendor timestamp type the Cell adopts;
`smo_schedule` uses that type directly and calls the library's duration
arithmetic on it, and `efx_schedule_clock` performs the already-decided read
over `chf_clock`, because reading the clock is an effect even though the
library is adopted. A Cell-owned `ClockInstant` that only
mirrors the vendor type is not required. It is invalid for `smo_schedule` to
include or import the vendor package itself, for `pvo_clock` to call the
clock, for `xpt_schedule` to absorb it, or for a feature-local clock adapter
to be relabeled `csf_clock`. When the clock source is replaceable, the Cell
instead declares the read as a required operation in its own contract
vocabulary, and the environment binds it at build time. A complex device protocol follows the same
split: `rmp_` owns frames and representation, `efx_` advances decided I/O
steps, and `smo_` owns transition policy. Only a facility actually shared by
multiple features earns a Cell-wide `csr_`, `csf_`, or `csp_` owner.

## Design-before-edit refactoring closure

Before a semantic Cell refactor, derive the following work products outside
the Cell from its public contract, relevant specification, governed tree, and
baseline structural evidence:

1. public meaning, state flow, behavior invariants, and external effects;
2. the semantic owner map across Core roles and truthful role-less seams;
3. the expected governed dependency graph and exact SEMANTIC_OWNER-stem lineage;
4. external isolation boundaries and public identity surfaces; and
5. behavior, role-pack, complete Cell, and direct-consumer acceptance checks.

Complete this design before moving implementation. Structural analysis
confirms the resulting observed structure; a lower finding count does not
substitute for the design. Treat any changed finding class, new unresolved observation, missing
SEMANTIC_OWNER lineage, SEMANTIC_OWNER-owned effect, REPRESENTATION_MAPPING-owned policy, reverse
EFFECT_EXECUTION consumption, active contract, internal membrane acquisition, or
forwarding seam as an incomplete refactor.

After editing, run behavior verification, the structural checks that exposed
the issue, complete Cell structural verification, and affected direct-consumer
tests. Keep the derived maps and transient evidence in the task-owned run
outside every Cell; do not turn them into another control file or persist
derived relations in `comsect1.json`.
