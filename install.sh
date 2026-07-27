#!/usr/bin/env bash
# SciForge installer (Bash) — installs skills into a target project
# for Claude Code, and writes an AGENTS.md for Codex CLI.
#
#   ./install.sh [-t <path>] [--global] [-f]
#
#   (no args)       Install into the current directory (project-level).
#   -t <path>       Install into <path> instead of the current directory.
#   --global        Install user-level instead of project-level.
#   -f, --force     Overwrite existing skills / AGENTS.md without prompting.
#
# Project-level layout (default):
#   <target>/.claude/skills/<name>/     Claude Code auto-discovers these
#   <target>/AGENTS.md                  Codex CLI reads this
#
# User-level layout (--global):
#   ~/.claude/skills/<name>/
#   ~/.codex/AGENTS.md

set -euo pipefail

# --- Helpers -----------------------------------------------------------------
die() { echo "error: $*" >&2; exit 1; }
info() { printf "  %s\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
skip() { printf "  \033[90mskipped %s\033[0m\n" "$*"; }
warn() { printf "  \033[33m%s\033[0m\n" "$*"; }

# --- Parse args --------------------------------------------------------------
TARGET=""
GLOBAL=0
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--target) TARGET="$2"; shift 2 ;;
        --global)    GLOBAL=1;  shift ;;
        -f|--force)  FORCE=1;   shift ;;
        --help|-h)   sed -n '/^# SciForge installer/,/^$/p' "$0"; exit 0 ;;
        *)           die "unknown flag: $1 (try --help)" ;;
    esac
done

# --- Locate the SciForge repo ------------------------------------------------
REPO_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
SKILLS_SRC="$REPO_ROOT/skills"
TEMPLATE="$REPO_ROOT/templates/AGENTS.md"

[[ -d "$SKILLS_SRC" ]]  || die "skills/ not found under $REPO_ROOT — run this from the SciForge repo."
[[ -f "$TEMPLATE" ]]    || die "templates/AGENTS.md not found — SciForge install is incomplete."

# --- Resolve install destinations --------------------------------------------
if [[ $GLOBAL -eq 1 ]]; then
    SKILLS_DEST="$HOME/.claude/skills"
    AGENTS_DEST="$HOME/.codex/AGENTS.md"
    SCOPE_LABEL="user-level (global)"
else
    if [[ -n "$TARGET" ]]; then
        PROJECT_ROOT="$(cd "$TARGET" && pwd -P)" || die "target directory does not exist: $TARGET"
    else
        PROJECT_ROOT="$(pwd -P)"
    fi
    SKILLS_DEST="$PROJECT_ROOT/.claude/skills"
    AGENTS_DEST="$PROJECT_ROOT/AGENTS.md"
    SCOPE_LABEL="project-level ($PROJECT_ROOT)"
fi

echo ""
echo "SciForge installer"
echo "  source: $SKILLS_SRC"
echo "  scope:  $SCOPE_LABEL"
echo "  skills: $SKILLS_DEST"
echo "  agents: $AGENTS_DEST"
echo ""

# --- Discover skills ---------------------------------------------------------
SKILLS=()
for d in "$SKILLS_SRC"/*/; do
    [[ -f "$d/SKILL.md" ]] && SKILLS+=("$d")
done

if [[ ${#SKILLS[@]} -eq 0 ]]; then
    die "No skills found (no <name>/SKILL.md under $SKILLS_SRC)."
fi

# Junk we never want to copy into an install.
EXCLUDE=(
    __pycache__ .pytest_cache .git .venv venv .claude
    library experiments .mypy_cache .ruff_cache
)

mkdir -p "$SKILLS_DEST"

# --- Copy each skill ---------------------------------------------------------
for skill in "${SKILLS[@]}"; do
    name="$(basename "$skill")"
    dst="$SKILLS_DEST/$name"

    if [[ -d "$dst" && $FORCE -eq 0 ]]; then
        read -r -p "  $name already exists. Overwrite? [y/N] " ans
        [[ "$ans" =~ ^[Yy] ]] || { skip "$name"; continue; }
    fi
    [[ -d "$dst" ]] && rm -rf "$dst"

    # Copy each top-level item, skipping excluded dirs at copy time
    # (so we never copy 50MB+ of library/ or cached data).
    mkdir -p "$dst"
    for item in "$skill"/* "$skill"/.*; do
        base="$(basename "$item")"
        [[ "$base" == "." || "$base" == ".." ]] && continue
        [[ -e "$item" ]] || continue  # skip unmatched globs
        excluded=0
        for pat in "${EXCLUDE[@]}"; do
            [[ "$base" == "$pat" ]] && { excluded=1; break; }
        done
        [[ $excluded -eq 1 ]] && continue
        cp -a "$item" "$dst/$base"
    done
    # Prune any nested __pycache__ etc. inside the copied tree.
    for pat in "${EXCLUDE[@]}"; do
        find "$dst" -type d -name "$pat" -prune -exec rm -rf {} + 2>/dev/null || true
    done
    find "$dst" \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true

    ok "$name"
done

# --- Build AGENTS.md from the template ---------------------------------------
SKILLS_ROOT_ABS="$(cd "$SKILLS_DEST" && pwd -P)"  # absolute path, no trailing /

# Map skill name → CLI entry point, for skills that follow the sf-* convention.
# Skills absent here (e.g. shell-script-driven remote-*) fall back to SKILL.md.
declare -A ENTRY_NAMES=(
    [literature]=sf-lit
    [search]=sf-search
    [download]=sf-download
    [init]=sf-init
    [me]=sf-me
)

BLOCKS=""
for skill in "${SKILLS[@]}"; do
    name="$(basename "$skill")"
    skill_md="$skill/SKILL.md"

    # Extract description from YAML frontmatter.
    desc=""
    in_fm=0
    while IFS= read -r line; do
        [[ "$line" == "---" ]] && { if [[ $in_fm -eq 0 ]]; then in_fm=1; continue; else break; fi; }
        if [[ $in_fm -eq 1 && "$line" =~ ^[[:space:]]*description:[[:space:]]*(.*)$ ]]; then
            desc="${BASH_REMATCH[1]}"
            break
        fi
    done < "$skill_md"

    # Resolve the entry point: prefer an sf-* CLI; else reference SKILL.md.
    entry="${ENTRY_NAMES[$name]:-}"
    if [[ -z "$entry" ]]; then
        cand=$(ls "$skill/scripts"/sf-* 2>/dev/null | head -1 || true)
        [[ -n "$cand" ]] && entry="$(basename "$cand")"
    fi
    if [[ -n "$entry" ]]; then
        entry_path="$SKILLS_ROOT_ABS/$name/scripts/$entry"
    else
        entry_path="$SKILLS_ROOT_ABS/$name/SKILL.md  (read for usage)"
    fi

    BLOCKS+="### $name"$'\n'
    BLOCKS+="\`$entry_path\`"$'\n'
    BLOCKS+="$desc"$'\n'
    BLOCKS+=$'\n'
done

# Read template, substitute placeholders, write.
agents_content=$(<"$TEMPLATE")
agents_content="${agents_content//'{{SKILLS_ROOT}}'/$SKILLS_ROOT_ABS}"
agents_content="${agents_content//'{{SKILLS_TABLE}}'/"$BLOCKS"}"

if [[ -f "$AGENTS_DEST" && $FORCE -eq 0 ]]; then
    read -r -p "  AGENTS.md already exists at $AGENTS_DEST. Overwrite? [y/N] " ans
    if [[ "$ans" =~ ^[Yy] ]]; then
        mkdir -p "$(dirname "$AGENTS_DEST")"
        printf '%s' "$agents_content" > "$AGENTS_DEST"
        ok "wrote AGENTS.md"
    else
        skip "AGENTS.md"
    fi
else
    mkdir -p "$(dirname "$AGENTS_DEST")"
    printf '%s' "$agents_content" > "$AGENTS_DEST"
    ok "wrote AGENTS.md"
fi

# --- Done --------------------------------------------------------------------
echo ""
echo "Done. Installed ${#SKILLS[@]} skills ($SCOPE_LABEL)."
echo ""
echo "Next steps:"
echo "  1. Claude Code: skills are auto-discovered under .claude/skills/."
echo "     (Restart Claude Code in the target project if it was open.)"
echo "  2. Codex CLI: reads AGENTS.md automatically."
echo "  3. Configure SciForge (one time):"
echo "       $SKILLS_ROOT_ABS/init/scripts/sf-init"
echo ""