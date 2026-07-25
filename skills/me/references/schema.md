# me.md schema

`me.md` is a single file with two parts:

1. **TOML front-matter** between `+++` fences — structured data.
2. **Markdown body** after the closing `+++` — free notes. Not
   parsed by `sf-me`, not part of `--json` output.

## Sections

Exactly five, in this order:

| Section | Answers the topic-fit question | Typical entries |
|---|---|---|
| `skill` | Can I do it? | methods, languages, wet-lab techniques |
| `equipment` | Can I physically pull it off? | instruments, apparatus, wet-lab access |
| `compute` | Can I physically pull it off? | GPUs, clusters, cloud quotas |
| `preference` | Do I want to do it? | risk appetite, taste, hard nos |
| `history` | What have I done? | past projects, publications, roles |

Every section is a TOML array of tables:

```toml
[[skill]]
name  = "..."
short = "..."
```

An absent section is equivalent to an empty section. `sf-me init`
creates all five so users see them from day one.

## Required fields per entry

| Field | Type | Notes |
|---|---|---|
| `name` | string | Lowercase slug, unique **within its section**. **Never renamed** once written (same policy as `sf-lit` citekeys). If you outgrow a name, delete and rewrite by hand. |
| `short` | string | One-sentence description. Consumed by human `show`, JSON `data.<section>[i].short`, and downstream skills. |

## Optional fields (advisory, not validated)

`sf-me` passes optional keys through verbatim to `--json` and ignores
them in the human view. Downstream consumers may look for them.
Suggested vocabularies:

### `skill`

| Field | Suggested values |
|---|---|
| `level` | `novice` / `working` / `proficient` / `expert` |
| `evidence` | free string — projects, papers, roles |
| `tags` | array of strings |
| `updated` | ISO-8601 date |

### `equipment`

| Field | Suggested values |
|---|---|
| `access` | `owned` / `shared` / `external` |
| `location` | free string |
| `updated` | ISO-8601 date |

### `compute`

| Field | Suggested values |
|---|---|
| `kind` | `local-gpu` / `cluster` / `cloud` |
| `scale` | free string, e.g. `8x A100 80GB` |
| `access` | `owned` / `shared` / `quota` |
| `updated` | ISO-8601 date |

### `preference`

| Field | Suggested values |
|---|---|
| `stance` | `prefer` / `avoid` / `neutral` |
| `tags` | array of strings |
| `updated` | ISO-8601 date |

### `history`

| Field | Suggested values |
|---|---|
| `period` | free string, e.g. `2023-2025` |
| `role` | free string |
| `outputs` | array of strings — papers, patents, code repos |
| `updated` | ISO-8601 date |

Missing optional fields mean "unknown," not "false." A companion
skill that wants a specific field should degrade gracefully when
it is absent.

## Uniqueness

`name` must be unique **within a section**. Two entries with the
same slug in different sections are fine (`compute.local-gpu` and
`skill.local-gpu` can coexist, though that would be a strange
choice).

Duplicate `name` within the same section is a schema error. `sf-me
show --json` exits 2 with a message like:

```
duplicate name in section 'skill': pytorch-distributed
```

## Body

Everything after the closing `+++` is Markdown, ignored by `sf-me`.
Suggested uses:

- `# Notes` heading with cross-entry reflection
- Long-form descriptions that do not fit `short`
- Career-stage thoughts, methodological taste, red lines
- References to other SciForge entities via `sciforge://` URIs

The body is available to any tool that reads the file directly
(the user's editor, a future `sf-me show --with-body`, an
LLM-driven reader). It is deliberately kept out of `show --json`
because most consumers only want structured data.
