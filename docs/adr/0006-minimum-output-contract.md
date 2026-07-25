# Minimum output contract every SciForge CLI must satisfy

Every SciForge CLI must (1) accept a `--json` flag that switches its stdout to machine-readable JSON, (2) use the reserved exit codes below, (3) if it owns a URI namespace, expose `show <id> --json` returning at least `{"id": str, "type": str, "uri": str}`, and (4) format all timestamps as ISO-8601 UTC and all IDs as strings. Everything else — payload shape, error message wording, pagination, warning placement — is a per-skill decision that must be documented in that skill's own SKILL.md but is not enforced by SciForge.

## Reserved exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `2` | Invalid user input (bad flags, malformed JSON, conflicting options) |
| `3` | Resource not found (unknown citekey, missing file, unknown URI) |
| `4` | Destructive operation refused without `--force` (see [ADR-0004](0004-layered-trust-model.md)) |
| `1`, `≥64` | Reserved for system / runtime errors; skill authors should prefer `2/3/4` when applicable |

## Considered Options

- **A. Lax: only require `--json`** — rejected: the URI resolver from [ADR-0003](0003-uri-cross-skill-refs-lazy.md) would need per-skill adapters, undoing most of the value.
- **B. Strict: enforce `{data, meta, warnings, errors}` envelope everywhere** — rejected: raises the bar for companion authors past what the "print correct JSON and you're in" promise from [ADR-0001](0001-hybrid-opinion-contract.md) allows.
- **C. Minimum contract (accepted)** — lock down exactly what cross-skill glue and the trust model need, leave the rest to skill authors.

## Consequences

- `sf-lit show <key> --json` must be extended to always emit `type: "paper"` and `uri: "sciforge://literature/<citekey>"`. This is the first B-phase task.
- Skill authors are free to disagree about where warnings go or how errors are worded; agents that want a uniform UX will normalize at the agent layer, not by asking the contract to grow.
- Tightening this contract later is a legitimate future ADR — but only when the pain of divergence is measured across at least three skills, not before.
