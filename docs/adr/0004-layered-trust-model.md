# Layered trust model for agent orchestration

Confirmation is applied where risk lives, not uniformly. Single-skill invocations run without prompting — the CLI's own input validation and idempotency are trusted. Multi-skill workflows are shown to the user as an up-front plan and then run through, with per-step progress but no per-step confirmation. Destructive operations (enumerated in [`SKILL_AUTHORING.md`](../../SKILL_AUTHORING.md)) always require explicit confirmation, at the CLI layer (`--force` or an interactive prompt) as well as at the agent layer.

## Considered Options

- **A. Confirm every step** — rejected: kills the workflow use case, reduces SciForge to a fancy shell autocomplete.
- **B. Full autonomy, report at end** — rejected: opaque to the user, defeats the "the way scientists actually work" promise, hides SciForge's own opinions from the learner.
- **C. Layered: single command silent · workflow shows plan · destructive always confirms** — **accepted**.

## Consequences

- The CLI layer is the last line of defence. Skills must refuse destructive operations without `--force` or an interactive `y/N`; the agent is not permitted to bypass that guard.
- "Destructive" is a defined term with an enumerated list — see the checklist in `SKILL_AUTHORING.md`. New categories are added there, not decided ad-hoc by skill authors.
- Exit code `4` is reserved for "would be destructive but `--force` was not given" (see [ADR-0006](0006-minimum-output-contract.md)), so the agent can distinguish a refusal-to-proceed from a real failure and ask the user cleanly.
