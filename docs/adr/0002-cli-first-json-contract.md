# CLI-first with JSON on stdout, agent as orchestrator

Every SciForge skill ships as a standalone command-line executable that reads flags/stdin and writes human-readable text (default) or machine-readable JSON (`--json`) to stdout. Skills do not import each other; they compose over stdout/stdin/temp files, and the host agent (Claude Code, Codex, …) is responsible for chaining them. SciForge has no long-running orchestrator, no shared Python core, and no MCP servers in this first iteration.

## Considered Options

- **A. CLI + JSON (accepted)** — language-agnostic, host-agnostic, matches Claude Code's Bash tool and the existing `litlib` shape.
- **B. Python shared-core package** — rejected: forces Python on every companion skill, breaks the "any language that can print JSON is welcome" property implied by [ADR-0001](0001-hybrid-opinion-contract.md).
- **C. MCP servers for every skill** — rejected for v1: forces long-running processes, tighter host coupling, and a schema-first authoring model that raises the barrier to contribution. A thin MCP layer on top of the CLIs remains a possible future addition; it will not be the primary interface.

## Consequences

- Startup cost (Python ~200 ms per invocation) is accepted as the price of composability. Skills stay stdlib-only where possible so cold start is fast.
- Cross-skill state is exchanged through disk artifacts and stdout, never through in-process shared state.
- The URI resolver (see [ADR-0003](0003-uri-cross-skill-refs-lazy.md)) is itself a CLI, not a library call.
