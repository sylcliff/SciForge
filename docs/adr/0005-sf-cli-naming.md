# `sf-` prefix for every SciForge CLI

Every SciForge-authored CLI is named `sf-<something>` (e.g. `sf-lit`, `sf-arxiv`, `sf-polish`, `sf-plot-timeseries`). The `sf-` prefix is a reserved namespace: companion skills may opt in to it if they satisfy SciForge's contracts. There is deliberately no single `sf` dispatcher command — each CLI is an independent executable in `$PATH`.

## Considered Options

- **A. Ad-hoc names (`litlib`, `polish`, `fit`, …)** — rejected: no visual identity as a family, high collision risk against the existing CLI ecosystem, poor tab-completion story.
- **B. Uniform `sf-` prefix (accepted)** — 3-byte tab-completion, unambiguous provenance, zero runtime coupling.
- **C. Single `sf` command with subcommands (`sf lit add …`)** — rejected: would require a central dispatcher or a magic loader over `$PATH`, both of which pull against the "any language, any author, just print JSON" property from [ADR-0002](0002-cli-first-json-contract.md).

## Consequences

- The existing `litlib` CLI in the literature skill is renamed to `sf-lit`. A `litlib` shim is kept in place until v1.0 and prints a deprecation notice.
- Domain skills use `sf-<domain>` (`sf-lit`, `sf-analysis`, `sf-writing`). Micro skills use either `sf-<domain>-<action>` when they logically belong to a domain (`sf-plot-timeseries`) or `sf-<action>` when they don't (`sf-polish`, `sf-arxiv`).
- Companion skills authored outside the core are invited but not required to adopt `sf-`. If they do, they must satisfy the contracts in [`SKILL_AUTHORING.md`](../../SKILL_AUTHORING.md).
