# Recommended MCP servers

`sf-init mcp` reads the hard-coded list below and compares it against
Claude Code's registration file (`~/.claude/settings.json`,
`mcpServers` key). Missing recommended servers surface as ⚠ rows with
a copy-paste install command.

The list is intentionally short: only servers that fill a gap in what
SciForge's public-API-only skills can do on their own.

## The shortlist (v1)

### `scansci-pdf`

- **Needed by**: `sf-download`
- **Why**: sf-download uses only public APIs (Crossref, Unpaywall,
  OpenAlex, S2, arXiv). When a paper's DOI has no OA version, or the
  publisher blocks the OA URL with 403, sf-download reports
  `pdf_link_broken` or `paywalled` and stops. `scansci-pdf` covers the
  next layer: Sci-Hub, LibGen, institutional WebVPN, Tor. Not something
  SciForge should call in-process (legal / policy separation), so it
  lives as an MCP server the user opts into per project.
- **Install**:
  ```
  pipx install scansci-pdf
  claude mcp add scansci-pdf -- scansci-pdf serve
  ```

### `brave-search`

- **Needed by**: `sf-search`
- **Why**: Academic sources miss context that lives in blog posts,
  press releases, forum threads, policy briefs. Brave Search's LLM
  Context API returns pre-extracted, ranked snippets you can feed
  directly to synthesis without a separate fetch step.
- **Install**:
  ```
  # Free key at https://brave.com/search/api/
  claude mcp add brave-search \
    -e BRAVE_API_KEY=<your-key> \
    -- npx -y @modelcontextprotocol/server-brave-search
  ```

## MCP env is independent

Every MCP server has its **own** runtime environment:

- `scansci-pdf` — installed via `pipx` (its own isolated Python env)
  or `pip install --user`.
- `brave-search` — Node.js server launched via `npx`; its deps live
  in the npm cache, not Python.
- Future MCPs may be Docker images, Go binaries, whatever.

`sf-init env` does **not** provision any of these. The two systems
share nothing except the recommendation table above; missing MCP is a
different failure class from a missing SciForge Python package. Doctor
renders them as separate sections.

## When to add / remove entries

Add an MCP to this list when:

1. A SciForge skill has a documented failure mode ("`sf-download`
   reports `paywalled` for every ACS paper", "`sf-search` can't answer
   'what's the latest news on X'") that a specific MCP fixes.
2. The MCP is stable, actively maintained, and has a canonical install
   command (either `pipx`, `npx`, or Docker).
3. The install command is short enough to paste as one line.

Remove an entry when it becomes unnecessary (e.g. a SciForge skill
subsumes its capability natively) or unmaintained (last commit >6
months + no security patches).

## Not on the list

- **`codex`** — general-purpose LLM assistant; not tied to SciForge.
- **`vibe-trading`** — orthogonal domain.
- **`understand-anything`** — general code-comprehension.

These are fine to have registered — `sf-init mcp` will list them as
`registered (extra)` rows, informational only.
