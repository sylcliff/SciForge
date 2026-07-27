# Agent presentation contract — main search results

Standard for presenting `sf-search` main-mode results (topic / boolean /
fields / `--from-strategy` / `--from-file`) to a human in conversation.
Applies to every invocation regardless of query. Any agent surfacing
results MUST follow this contract exactly — deviations are drift, not
creativity.

**Why:** Without a stable rendering contract, every result summary is
ad-hoc — the user has to re-negotiate what counts as "recommendation" vs
"noise" every time. Fixing the contract makes results reproducible and
the recommendation reasons auditable.

For citation-graph subcommands (`refs` / `cited-by`), use the simplified
variant in [presentation-citation.md](presentation-citation.md) instead.

## Section 0 — echo command + 3-line summary

```
🔍 sf-search '<original CLI verbatim>'
📊 共 N 篇 · 命中源 pubmed:X crossref:Y arxiv:Z openalex:W (failed sources: s2:FAIL(429) etc.)
   去重 R→N · K 篇 arxiv-upgrade · ~M OA · ~S 疑似噪声
```

- Every number MUST come from the actual NDJSON output. Never fabricate.
- If a source is missing/failed, spell it out inline (`s2:FAIL(429)`).

## Section 1 — 强推荐 5 条 (density C, 3-4 lines each)

Fixed 5 role slots, always in this order:

1. **顶级综述** — venue in `{Chem Rev, Nat Rev, Phys Rep, WIREs, Ann Rev}` OR title contains review/survey/tutorial/perspective
2. **奠基/里程碑** — citation_count ≥ 500 AND not a review
3. **核心方法** — defines a core algorithm/technique for the field
4. **新兴前沿** — published in the last 2 years; low citations OK
5. **应用/实证** — specific molecule/material/benchmark/application

Per-paper format:

```
#N  [year · venue · X cited]  ★(if arxiv_upgraded) · sources_hit · 📥/🔒/❔
    <title truncated> — <first author +N>
    Abstract: <60-80 chars from meta.abstract>
    → [<依据类型>] <20-40 汉字 explanation>
```

- If a slot has NO matching paper: display `(此查询未见相关论文)` — do NOT force-fill.
- Same paper never occupies two slots.
- Downloadability icon:
  - `📥` — `meta.is_oa == true` OR venue in {arXiv, bioRxiv, medRxiv, chemrxiv, SSRN} OR publisher in {MDPI, Frontiers, PLOS, eLife, F1000}
  - `🔒` — `meta.is_oa == false` (OpenAlex confirmed closed)
  - `❔` — `meta.is_oa == null` and heuristics don't match

## Section 2 — 分组导读 (4 groups, dynamic naming)

- Group count is FIXED at 4. Group NAMES are dynamic per query.
- Print one explicit line naming the four group titles:
  `📁 分组:<A> / <B> / <C> / <D>`
- Groups 1 and 4 are conventionally reserved for "综述/背景" and "疑似噪声",
  but you are free to rename them if the query is exotic (e.g., humanities).
- Each group picks its top 5 by RRF order (fewer if group has <5 records).
- Per-paper format (density B, 2-3 lines):

```
#N  [year · venue · cited]  ★ · sources_hit · 📥/🔒/❔
    <title> — <authors>
    → [<依据类型>] <explanation>
```

- 疑似噪声 group MUST give a one-line reason per paper explaining WHY
  it's classified as noise (e.g., "QUANTUM ESPRESSO 是 DFT 软件,不涉及 QC").

## Section 3 — 完整清单 (density A, 1 line each, cap 100)

- Default: ALWAYS render (per user preference).
- Cap: 100 papers. If N > 100, print first 100 then this exact tail:
  ```
  ... 剩余 X 条(共 N 条)已省略。若需完整清单:
      sf-search ... --out results.ndjson
  ```
- Order = CLI's RRF order (never re-rank in this section).
- Per-paper format:

```
#N  ★  [year]  cited  venue-truncated-20    first-author+N       title-truncated-55        identifier
```

## 依据类型 — closed set of 6

Every recommendation line MUST use exactly one of these tags:

| Tag | Rule |
|---|---|
| `[综述]` | Paper is review/survey/tutorial/perspective |
| `[里程碑]` | citation_count ≥ 500 OR venue is Nature/Science main journal |
| `[方法核心]` | Defines a core algorithm/technique for the field |
| `[新方向]` | Introduces a new technical path or application area |
| `[应用]` | Concrete application/benchmark/empirical study |
| `[疑似噪声]` | Misfire — only used inside the noise group |

## Forbidden phrasing

Never write these on a recommendation line:
- "必读", "经典", "很重要", "太重要了", "推荐", "极佳", "神作"

Every recommendation must give a *refutable* concrete basis: a citation
count, a venue property, a field-specific connection, or a temporal
signal. The user must be able to disagree with a reason, not with a
vibe.

## Skipping the full list

Default is "always render full list" — do not skip it just because
`--out FILE` was set. If the user asks for it suppressed, honor that
for the current invocation only.
