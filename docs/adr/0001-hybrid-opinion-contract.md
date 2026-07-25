# Hybrid: opinionated inward, contract-based outward

SciForge is an open-source project for a small population of research users (starting with the author). To ship anything at all it must hardcode strong opinions about tools and workflow (MinerU, SQLite, `paper.md` as canonical, Python stdlib only, two-phase ingest). To be extensible by others without becoming a plugin framework it exposes a contract at every seam where a third party might want to plug in — most importantly the ingest interface every fetch skill talks to.

## Considered Options

- **A. Product for any researcher** — every choice must be pluggable, every backend swappable. Rejected: infinite configuration surface, MVP never ships.
- **B. Personal tool, open source as byproduct** — every choice is hardcoded to one author's habits. Rejected: closes the door to contribution and to the "companion skill" idea that already exists in the literature skill.
- **C. Hybrid: opinionated core + contracts at the edge** — **accepted**.

## Consequences

- Every domain skill has an *inside* (strong opinion, few flags) and an *outside* (a documented contract in `references/*.md`).
- README language must not promise product-grade pluggability. "Here is how we do it, here is the seam if you want to do it differently" is the tone.
- New backends (a second converter, a different DB) are legitimate only if the current one is genuinely inadequate for the author, not as a general capability.
