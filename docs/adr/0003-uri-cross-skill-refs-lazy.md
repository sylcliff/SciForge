# Cross-skill references are lazy URIs, not a central registry

Skills refer to each other's entities using URIs of the form `sciforge://<skill>/<id>[#<fragment>]`. There is no central SciForge registry, no foreign-key enforcement, and no write-time validation. A reference is only checked when someone reads it, by asking the owning domain skill via `sf-<skill> show <id> --json`. A missing target is a warning, not an error.

## Considered Options

- **A. Each domain silo, references live in prose** — rejected: writing and analysis both need reliable references into the literature catalog; prose-only refs make `sf-writing cite-check` impossible.
- **B. Central `~/.sciforge/registry.db` with foreign keys** — rejected: creates a single point of coupling every skill must learn, and breaks the "companion skill just prints JSON" property from [ADR-0001](0001-hybrid-opinion-contract.md) / [ADR-0002](0002-cli-first-json-contract.md).
- **C. URI convention + lazy resolution (accepted)** — every domain skill owns exactly one URI namespace, and the only cross-skill primitive is `show <id> --json`.

## Consequences

- Every domain skill **must** implement `show <id> --json` returning at least `{id, type, uri}` (see [ADR-0006](0006-minimum-output-contract.md)).
- Dangling references are a valid intermediate state (you can cite a paper you haven't added yet). A future `sf doctor` command may bulk-validate them but never rewrites them.
- `figures` deliberately gets no URI namespace: figures are outputs of experiments and are referred to as `sciforge://analysis/<exp-id>/<fig-id>`. This is why `figures` is not a domain skill.
