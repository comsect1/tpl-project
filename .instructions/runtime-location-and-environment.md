# Runtime Location and Environment Override Policy

This policy governs how Comsect1 products, tools, platforms, and foundations
resolve their runtime home directories, role subdirectories, dot-prefix (`.`)
conventions, and environment variable overrides across Windows, macOS, and
Linux. Product-specific bindings (a product's family name, its role
directories, its home variable) belong to that product's own local
instructions.

## 1. Family Root Grouping (`<family>`)

- Every runtime product family owns a single lowercase `kebab-case` family
  directory (`<family>`).
- Sub-daemons or internal role directories (`<role>`, for
  example `providers` or `logs`) **must never scatter** as top-level siblings
  in the OS base directory. They must reside strictly inside their owning
  `<family>/` root:
  - `%LOCALAPPDATA%\<family>\{<role>,<role>,...}`
- Never prepend a vendor folder (`<vendor>/`) above `<family>` in OS runtime
  paths unless required by an external third-party host contract.

## 2. Platform Base Paths (Local-Only, No Roaming `%APPDATA%`)

Hardware-bound vaults, keys, sockets, seat leases, provider installations, and
caches must reside in machine-local storage, never in Active Directory Roaming
Profiles (`%APPDATA%`):

| Platform | Default Base Path | Fallback |
|---|---|---|
| **Windows** | `%LOCALAPPDATA%\<family>` | `%USERPROFILE%\AppData\Local\<family>` -> Win32 `CSIDL_LOCAL_APPDATA\<family>` |
| **macOS** | `$HOME/Library/Application Support/<family>` | `$XDG_DATA_HOME/<family>` |
| **Linux / Unix** | `$XDG_DATA_HOME/<family>` (or `$XDG_CONFIG_HOME/<family>` for pure config tools) | `$HOME/.local/share/<family>` (or `$HOME/.config/<family>`) |

## 3. Dot-Prefix (`.`) Rule ("No Double-Hidden; Dot Only on User-Exposed Roots")

A leading dot (`.`) hides metadata when placed directly inside a user-exposed
directory shared with ordinary user documents. Inside an OS-dedicated hidden
application data root, adding a leading dot creates redundant "double-hidden"
paths and must be avoided.

1. **NO leading dot inside OS AppData / XDG / Application Support**:
   - Use `%LOCALAPPDATA%\<family>` and `$XDG_DATA_HOME/<family>`, **never**
     `%LOCALAPPDATA%\.<family>` or `$XDG_DATA_HOME/.<family>`.
2. **NO leading dot on role subdirectories inside `<family>/`**:
   - Inside `<family>/`, role directories are already isolated in the product's
     private namespace. Use `<home>/<role>` — **never**
     `<home>/<role>/.<name>` or `<home>/.<role>`.
3. **USE a leading dot (`.<family>`) ONLY on user-exposed roots**:
   - **Removable USB drive root**: `<drive root>/.<family>` (hidden from the user's top-level
     USB file listing; a product's earlier drive-root name, `.<earlier name>`,
     may be accepted as a read fallback).
   - **User `$HOME` / `%USERPROFILE%` root** (when `$HOME` is used directly as a
     portable workspace or fallback root): `~/.<family>`.
   - **Project / workspace root**: `.configs/`, `.instructions/`,
     `.claude-plugin/`.

## 4. Environment Variable Override Hierarchy and Fail-Closed Validation

All Comsect1 runtimes must resolve their `<family>` home directory in the
following deterministic priority order:

1. **Explicit CLI flag**: `--home <abs_path>` (or `--config-dir <abs_path>`)
2. **Family home environment variable (`<FAMILY>_HOME`)**:
   - One variable per family, named by the product (`<FAMILY>_HOME`).
   - Maps directly to the single `<family>` root directory (`$<FAMILY>_HOME`).
3. **OS Platform Default**: as defined in Section 2.

### Fail-Closed Validation at Trust Boundaries

- Every path supplied via `<FAMILY>_HOME`, `LOCALAPPDATA`, `USERPROFILE`,
  `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, or `HOME` **must be an absolute path**.
- If an environment variable is set to a relative path (e.g., `<FAMILY>_HOME=foo/bar`),
  the resolver must **fail closed** with an explicit invalid-argument/configuration
  refusal rather than silently resolving against the current working directory.
