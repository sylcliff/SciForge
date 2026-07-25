# sf-me config

`sf-me` reuses the SciForge shared config file. Resolution order (first match wins):

1. `$SCIFORGE_CONFIG` — explicit path to a TOML file
2. `./.sciforge.toml` — walked up to the git root
3. `$XDG_CONFIG_HOME/sciforge/config.toml` or `~/.config/sciforge/config.toml`
4. Built-in defaults

## `[me]` section

| Key | Type | Default | Meaning |
|---|---|---|---|
| `dir` | string | `~/.sciforge/me` | Directory that will contain `me.md`. Created on `init` if it does not exist. `~` is expanded. |

Example:

```toml
[me]
dir = "/home/alice/dotfiles/sciforge/me"
```

## Difference from `sf-lit`

- `sf-lit` defaults to `[library] path = "./library"` — **project-level**.
- `sf-me` defaults to `[me] dir = "~/.sciforge/me"` — **user-level**.

The rationale is documented in `SKILL.md`: your research self does not
change per repository; your library sometimes does.

## Env var for tests

`SCIFORGE_CONFIG` is the standard SciForge test hook: point it at a
per-test TOML that pins `[me] dir` under `tmp_path`, and the CLI will
read and write there in complete isolation from `~/.sciforge/me/`.

See `tests/conftest.py` for the exact pattern (copied from `sf-lit`).

## Not used by `sf-me`

Other sections in the config file (e.g. `[library]`, `[converter]`,
`[sources]`) are ignored by `sf-me`. They belong to other skills.
`sf-me` only reads the `[me]` section.
