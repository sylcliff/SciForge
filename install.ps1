#!/usr/bin/env pwsh
# SciForge installer (PowerShell) — installs skills into a target project
# for Claude Code, and writes an AGENTS.md for Codex CLI.
#
# One-liner (no clone needed):
#   irm https://raw.githubusercontent.com/sylcliff/SciForge/main/install.ps1 | iex
#
# From a local clone:
#   ./install.ps1 [-Target <path>] [-Global] [-Force]
#
#   (no args)        Install into the current directory (project-level).
#   -Target <path>   Install into <path> instead of the current directory.
#   -Global          Install user-level instead of project-level.
#   -Force           Overwrite existing skills / AGENTS.md without prompting.
#
# To pass flags through the one-liner:
#   & ([scriptblock]::Create((irm ...))) -Global
#
# Project-level layout (default):
#   <target>/.claude/skills/<name>/     Claude Code auto-discovers these
#   <target>/AGENTS.md                  Codex CLI reads this
#
# User-level layout (-Global):
#   ~/.claude/skills/<name>/
#   ~/.codex/AGENTS.md
#
# Skills are COPIED (self-contained snapshot). Agents call the CLIs by
# absolute path; nothing is placed on PATH.

[CmdletBinding()]
param(
    [string]$Target,
    [switch]$Global,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$Remote = $false

# --- Locate the SciForge repo (or download if running remotely) ----------------
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'skills'))) {
    $RepoRoot   = $PSScriptRoot
    $SkillsSrc  = Join-Path $RepoRoot 'skills'
    $Template   = Join-Path $RepoRoot 'templates\AGENTS.md'
    if (-not (Test-Path $Template)) {
        Write-Error "templates/AGENTS.md not found — SciForge install is incomplete."
        exit 1
    }
} else {
    $Remote = $true
    $TmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
    Write-Host "Downloading SciForge (one-time)…" -ForegroundColor Cyan
    $ZipUrl = "https://codeload.github.com/sylcliff/SciForge/zip/refs/heads/main"
    $ZipPath = Join-Path $TmpDir 'sciforge.zip'
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -ErrorAction Stop
        Expand-Archive -Path $ZipPath -DestinationPath $TmpDir -Force
    } catch {
        Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
        Write-Error "Download failed: $_"
        exit 1
    }
    $RepoRoot   = Join-Path $TmpDir 'SciForge-main'
    $SkillsSrc  = Join-Path $RepoRoot 'skills'
    $Template   = Join-Path $RepoRoot 'templates\AGENTS.md'
    if (-not (Test-Path $SkillsSrc)) {
        Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
        Write-Error "skills/ not found in downloaded archive"
        exit 1
    }
    if (-not (Test-Path $Template)) {
        Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
        Write-Error "templates/AGENTS.md not found in downloaded archive"
        exit 1
    }
}

# --- Resolve install destinations --------------------------------------------
if ($Global) {
    $SkillsDest   = Join-Path $HOME '.claude\skills'
    $AgentsDest   = Join-Path $HOME '.codex\AGENTS.md'
    $ScopeLabel   = 'user-level (global)'
} else {
    if ($Target) {
        $ProjectRoot = (Resolve-Path -LiteralPath $Target).Path
    } else {
        $ProjectRoot = (Get-Location).Path
    }
    if (-not (Test-Path $ProjectRoot)) {
        Write-Error "Target directory does not exist: $ProjectRoot"
        exit 1
    }
    $SkillsDest = Join-Path $ProjectRoot '.claude\skills'
    $AgentsDest = Join-Path $ProjectRoot 'AGENTS.md'
    $ScopeLabel = "project-level ($ProjectRoot)"
}

Write-Host ""
Write-Host "SciForge installer" -ForegroundColor Cyan
Write-Host "  source: $SkillsSrc"
Write-Host "  scope:  $ScopeLabel"
Write-Host "  skills: $SkillsDest"
Write-Host "  agents: $AgentsDest"
Write-Host ""

# --- Discover skills ----------------------------------------------------------
$Skills = Get-ChildItem -Path $SkillsSrc -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'SKILL.md') }

if ($Skills.Count -eq 0) {
    Write-Error "No skills found (no <name>/SKILL.md under $SkillsSrc)."
    exit 1
}

# Top-level directories we never want to copy (e.g. 50MB+ library/).
$ExcludeTop = @('__pycache__', '.pytest_cache', '.git', '.venv', 'venv', '.claude',
                'library', 'experiments', '.mypy_cache', '.ruff_cache')

New-Item -ItemType Directory -Force -Path $SkillsDest | Out-Null

# --- Copy each skill ----------------------------------------------------------
foreach ($skill in $Skills) {
    $name = $skill.Name
    $dst  = Join-Path $SkillsDest $name

    if ((Test-Path $dst) -and (-not $Force)) {
        $ans = Read-Host "  $name already exists. Overwrite? [y/N]"
        if ($ans -notmatch '^[Yy]') {
            Write-Host "  skipped $name" -ForegroundColor DarkGray
            continue
        }
    }
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }

    # Copy each top-level item, skipping excluded directories at copy time
    # (so we never copy 50MB+ of library/ or cached data).
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $excludeSet = [System.Collections.Generic.HashSet[string]]::new(
        [string[]]$ExcludeTop, [System.StringComparer]::OrdinalIgnoreCase)
    foreach ($item in Get-ChildItem -Path $skill.FullName -Force) {
        if ($excludeSet.Contains($item.Name)) { continue }
        Copy-Item -Recurse -Force -Path $item.FullName -Destination (Join-Path $dst $item.Name)
    }
    # Prune any nested __pycache__ etc. inside the copied tree.
    Get-ChildItem -Path $dst -Recurse -Force -Include '*.pyc','*.pyo' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    foreach ($pat in $ExcludeTop) {
        Get-ChildItem -Path $dst -Recurse -Force -Directory -Filter $pat -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host "  installed $name" -ForegroundColor Green
}

# --- Build AGENTS.md from the template ---------------------------------------
# Absolute root that the installed skills live under.
$SkillsRootAbs = (Resolve-Path -LiteralPath $SkillsDest).Path -replace '\\', '/'

# Map skill directory name → CLI entry point name, for skills that follow
# the `sf-*` convention. Skills absent here (e.g. shell-script-driven
# remote-*) fall back to referencing their SKILL.md.
$entryNames = @{
    'literature'   = 'sf-lit'
    'search'       = 'sf-search'
    'download'     = 'sf-download'
    'init'         = 'sf-init'
    'me'           = 'sf-me'
}

$blocks = foreach ($skill in $Skills) {
    $name    = $skill.Name
    $skillMd = Join-Path $skill.FullName 'SKILL.md'
    $lines   = Get-Content -LiteralPath $skillMd

    # Pull `description:` out of the YAML frontmatter.
    $desc = ''
    $inFm = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $ln = $lines[$i]
        if ($ln -eq '---') { if ($inFm) { break } else { $inFm = $true; continue } }
        if ($inFm -and $ln -match '^\s*description:\s*(.*)$') {
            $desc = $Matches[1].Trim()
            # Absorb continuation lines (indented, no `key:`).
            while ($i + 1 -lt $lines.Count -and
                   $lines[$i+1] -match '^\s+\S' -and
                   $lines[$i+1] -notmatch '^\s*[a-zA-Z_-]+:\s') {
                $desc += ' ' + $lines[$i+1].Trim()
                $i++
            }
            break
        }
    }

    # Resolve the entry point: prefer an sf-* CLI script; else, for
    # shell-driven skills, tell the agent to read the SKILL.md.
    $entry = $null
    if ($entryNames.ContainsKey($name)) {
        $entry = $entryNames[$name]
    } else {
        $cand = Get-ChildItem -Path (Join-Path $skill.FullName 'scripts') `
                -Filter 'sf-*' -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
        if ($cand) { $entry = $cand.Name }
    }

    if ($entry) {
        $entryPath = "$SkillsRootAbs/$name/scripts/$entry"
    } else {
        # No sf-* CLI — reference the SKILL.md (agent reads it for usage).
        $entryPath = "$SkillsRootAbs/$name/SKILL.md  (read for usage)"
    }

    "### $name`n``$entryPath```n$desc`n"
}

$skillsTable = ($blocks -join "`n")

$agentsContent = (Get-Content -LiteralPath $Template -Raw).
    Replace('{{SKILLS_ROOT}}', $SkillsRootAbs).
    Replace('{{SKILLS_TABLE}}', $skillsTable)

if ((Test-Path $AgentsDest) -and (-not $Force)) {
    $ans = Read-Host "  AGENTS.md already exists at $AgentsDest. Overwrite? [y/N]"
    if ($ans -match '^[Yy]') {
        New-Item -ItemType Directory -Force -Path (Split-Path $AgentsDest) | Out-Null
        Set-Content -LiteralPath $AgentsDest -Value $agentsContent -Encoding UTF8
        Write-Host "  wrote AGENTS.md" -ForegroundColor Green
    } else {
        Write-Host "  skipped AGENTS.md" -ForegroundColor DarkGray
    }
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path $AgentsDest) | Out-Null
    Set-Content -LiteralPath $AgentsDest -Value $agentsContent -Encoding UTF8
    Write-Host "  wrote AGENTS.md" -ForegroundColor Green
}

# --- Done --------------------------------------------------------------------
Write-Host ""
Write-Host "Done. Installed $($Skills.Count) skills ($ScopeLabel)." -ForegroundColor Cyan
if ($Remote) {
    Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Claude Code: skills are auto-discovered under .claude/skills/."
Write-Host "     (Restart Claude Code in the target project if it was open.)"
Write-Host "  2. Codex CLI: reads AGENTS.md automatically."
Write-Host "  3. Configure SciForge (one time):"
Write-Host "       $SkillsRootAbs/init/scripts/sf-init"
Write-Host ""