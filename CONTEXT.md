# SciForge

A curated layer of skills, prompts, workflows, and tools that turns a general-purpose coding agent (Claude Code / Codex / Cursor / …) into a domain-tuned scientific research assistant. This file defines the shared language; architectural rationale lives in [`docs/adr/`](docs/adr/), operational rules in [`SKILL_AUTHORING.md`](SKILL_AUTHORING.md).

## Language

### Kinds of skill

**Domain skill**:
A skill that owns and persists its own state (an index, a catalog, a project directory) and exposes a stable identifier space to the rest of SciForge. Each domain skill declares exactly one URI namespace.
_Avoid_: Package, module, pack.

**Micro skill**:
A stateless, function-shaped skill: input from flags/stdin, output to stdout, no side-effects on any SciForge index. Composes freely with any domain skill or other micro skill.
_Avoid_: Utility, helper, plugin, action.

**Companion skill**:
A skill (usually a micro skill) authored outside the SciForge core that satisfies a SciForge contract — most often the ingest interface consumed by a domain skill.
_Avoid_: Extension, integration, adapter.

### Identity and references

**Citekey**:
The stable, human-readable ID for a paper in the literature domain (e.g. `vaswani2017attention`). Assigned by `sf-lit add`, never reassigned.
_Avoid_: Paper ID, slug.

**SciForge URI**:
A cross-skill reference of shape `sciforge://<skill>/<id>[#<fragment>]`, resolved lazily by asking the owning domain skill (`sf-<skill> show <id> --json`). Not a URL, not a path.
_Avoid_: Handle, ref, pointer.

**Contract**:
The externally visible, versioned interface of a skill — CLI surface, JSON schema, exit codes, URI namespace. Everything else is implementation and may change without notice.
_Avoid_: API, interface, protocol.

### Paper lifecycle

**Paper**:
One entry in the literature domain. Has a citekey, a `paper.pdf`, a `metadata.json`, and (once converted) a canonical `paper.md`.
_Avoid_: Article, document, publication, reference.

**Canonical markdown**:
The `paper.md` a domain skill treats as truth for full-text search. There is exactly one per paper regardless of how many converter outputs exist on disk.
_Avoid_: Rendered text, extracted text, main MD.

**Converter**:
An external PDF→MD tool (MinerU or Docling) that SciForge shells out to. Each paper is bound to exactly one converter at a time; the binding is recorded on disk.
_Avoid_: Parser, extractor, renderer.

**md_status**:
The lifecycle state of a paper's canonical markdown: `absent` · `ready` · `failed` · `stale`. Recomputed on read against disk; the DB does not lie.
_Avoid_: State, phase, availability.

### Interaction

**Workflow**:
A multi-skill plan authored and driven by the host agent, not by any SciForge process. SciForge has no orchestrator, scheduler, or long-running daemon.
_Avoid_: Pipeline, DAG, job, chain.

**Destructive operation**:
Any command that would overwrite/delete on-disk artifacts, mutate an index destructively, break a provenance link, call outward from the machine, bypass a safety fuse, or change SciForge config. Enumerated in [`SKILL_AUTHORING.md`](SKILL_AUTHORING.md); always requires explicit confirmation (either `--force` at the CLI or user confirmation via the agent).
_Avoid_: Dangerous action, side-effectful call.
