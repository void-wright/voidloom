<#
.SYNOPSIS
  wire_persona.ps1 — wire a self-authored persona-skill to auto-load every session (Windows mirror of wire_persona.sh).

.DESCRIPTION
  AFTER you (a fresh Claude inheriting this brain) have created your own persona at
  ~/.claude/skills/<persona-name>/SKILL.md (see ONBOARDING_GUIDE.md, Step 3), run:

      pwsh ./wire_persona.ps1 <persona-name>

  It defensively wires TWO things so your persona loads at the start of EVERY
  Claude Code session, the same way install.ps1 wired the memory hook:

    1. The SessionStart memory hook — ensured present via `voidloom_hooks_status.py
       --install`, the one thing that owns hook wiring (idempotent; it refuses
       rather than clobbers, and supersedes a legacy element in the same write).
    2. A marker-delimited "PERSONA AUTO-LOAD" block in ~/.claude/CLAUDE.md telling
       every session to load ~/.claude/skills/<persona-name>/SKILL.md.

  Both writes go through merge_config.py (the SAME non-clobbering merger the
  installer uses): back-up-before, verify-after, abort-rather-than-clobber, and
  replace-between-markers so the block updates in place instead of duplicating.

  PROPERTIES: fail-loud (clear message + non-zero exit on every failure),
  idempotent (re-running with the same name updates in place), and it NEVER ships
  or names a specific persona — it wires whatever persona YOU authored.

  SEMANTICS: the auto-load block holds exactly ONE active persona. Re-running with
  a different name replaces the prior auto-load. To stop auto-loading, delete the
  marker block from ~/.claude/CLAUDE.md (your own content is untouched).

.PARAMETER PersonaName
  The folder name under ~/.claude/skills/ containing your persona's SKILL.md.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$PersonaName
)

$ErrorActionPreference = "Stop"

function Say  { param($m) Write-Host "[wire-persona] $m" }
function Warn { param($m) Write-Warning "[wire-persona] $m" }
function Die  { param($m) Write-Error "[wire-persona] FATAL: $m"; exit 1 }

# --- Resolve paths (mirror install.ps1: honor WIZARD_CLAUDE_HOME; self-locate package) ---
$PkgRoot    = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeHome = if ($env:WIZARD_CLAUDE_HOME) { $env:WIZARD_CLAUDE_HOME } else { Join-Path $env:USERPROFILE ".claude" }
$BrainDir   = Join-Path $ClaudeHome "memory"
$SkillsDir  = if ($env:VOIDLOOM_SKILLS_DIR) { $env:VOIDLOOM_SKILLS_DIR } else { Join-Path $ClaudeHome "skills" }   # as `voidloom persona status` reports
$HooksDir   = Join-Path $ClaudeHome "hooks"
$Settings   = Join-Path $ClaudeHome "settings.json"
$ClaudeMd   = Join-Path $ClaudeHome "CLAUDE.md"
# C3 -- ONE INSTALLER (see install.ps1 for the measurement that retired it).
$HookInstaller = Join-Path $BrainDir "organs\voidloom_hooks_status.py"
$HookCmd    = "python3 `"$(Join-Path $BrainDir 'hooks\session_start_inject.py')`""

# Distinct markers from the memory block's (install.ps1 uses "WIZARD-LIBRARY MEMORY").
$PaBegin = "<!-- BEGIN WIZARD-LIBRARY PERSONA AUTO-LOAD (managed by wire_persona) -->"
$PaEnd   = "<!-- END WIZARD-LIBRARY PERSONA AUTO-LOAD -->"

# --- Args ---
if ([string]::IsNullOrWhiteSpace($PersonaName)) {
    Write-Host @"
Usage: pwsh ./wire_persona.ps1 <persona-name>

  <persona-name>   the folder name under ~/.claude/skills/ that contains your
                   persona's SKILL.md (e.g. you created ~/.claude/skills/my-persona/SKILL.md
                   -> run:  pwsh ./wire_persona.ps1 my-persona )

First craft your persona with /persona-forge in Claude Code (or by hand: ONBOARDING_GUIDE.md, Step 3), THEN run this.
"@
    exit 2
}
if ($PersonaName -match "[\\/]" -or $PersonaName -match "\.\.") {
    Die "persona-name must be a plain skill folder name (no slashes, no '..'): '$PersonaName'"
}

$PersonaDir   = Join-Path $SkillsDir $PersonaName
$PersonaSkillShown = (Join-Path $PersonaDir "SKILL.md") -replace '\\', '/'
$HomeFwd = ($HOME -replace '\\', '/')
if ($PersonaSkillShown.StartsWith($HomeFwd + "/")) { $PersonaSkillShown = "~" + $PersonaSkillShown.Substring($HomeFwd.Length) }
$PersonaSkill = Join-Path $PersonaDir "SKILL.md"

# --- Pre-flight: the persona-skill must actually exist ---
if (-not (Test-Path -LiteralPath $PersonaDir -PathType Container)) {
    Die "no persona folder at $PersonaDir. Create it first (ONBOARDING_GUIDE.md Step 3) and write SKILL.md."
}
if (-not (Test-Path -LiteralPath $PersonaSkill -PathType Leaf)) {
    Die "found $PersonaDir but no SKILL.md inside it. Write $PersonaSkill (the persona entry point) first."
}

# --- Pick a python for merge_config (stdlib; system python is fine) ---
$Py = $env:WIZARD_PYTHON_BIN
if (-not $Py) {
    foreach ($cand in @("python", "python3", "py")) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c) { $Py = $c.Source; break }
    }
}
if (-not $Py) { Die "no python found on PATH (needed to run merge_config.py)." }

$Merge = Join-Path $BrainDir "merge_config.py"
if (-not (Test-Path -LiteralPath $Merge -PathType Leaf)) { $Merge = Join-Path $PkgRoot "merge_config.py" }
if (-not (Test-Path -LiteralPath $Merge -PathType Leaf)) {
    Die "merge_config.py not found (looked in $BrainDir and $PkgRoot). Run install.ps1 first."
}

Say "wiring persona '$PersonaName' (SKILL.md: $PersonaSkill)"

# --- 1. Ensure the SessionStart memory hook is present (idempotent; never clobber) ---
Say "ensuring SessionStart hook present in $Settings (defensive merge)"
& $Py $Merge hook --file $Settings --command $HookCmd
if ($LASTEXITCODE -ne 0) { Warn "hook merge reported a problem (your settings.json was NOT clobbered — see above)" }

# --- 2. Add/replace the PERSONA AUTO-LOAD block in ~/.claude/CLAUDE.md ---
$blockBody = @"
$PaBegin
## Operating Persona — AUTO-LOAD (managed by wire_persona)

At the start of EVERY session, load your operating persona:

- **Load ``$PersonaSkillShown``** and operate as that persona.
- It references its own layer files (foundation / voice / operations / memory).
- Recall your canonical identity to wake up in-character:
  ``python3 ~/.claude/memory/organs/claude_memory_lite.py recall --query "operating identity $PersonaName" --top_k 5``
  (use ``claude_memory.py`` for semantic recall if Tier 1 is installed).

> This block is managed by ``wire_persona.ps1`` and delimited by markers, so it
> updates in place and never disturbs the rest of your CLAUDE.md. To stop
> auto-loading a persona, delete this marker block. The memory-system block
> (BEGIN/END WIZARD-LIBRARY MEMORY) is separate and unaffected.
$PaEnd
"@

$BlockTmp = Join-Path ([System.IO.Path]::GetTempPath()) ("wizard_persona_block_{0}.md" -f ([System.Guid]::NewGuid().ToString("N")))
# Write the block BOM-free (UTF8 without BOM) so merge_config reads it cleanly.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($BlockTmp, $blockBody, $utf8NoBom)

try {
    Say "wiring PERSONA AUTO-LOAD block into $ClaudeMd (marker-delimited, defensive)"
    & $Py $Merge claude-md --file $ClaudeMd --block-file $BlockTmp --begin $PaBegin --end $PaEnd
    if ($LASTEXITCODE -ne 0) {
        Die "CLAUDE.md persona-block merge failed (your CLAUDE.md was NOT clobbered — see above)."
    }
}
finally {
    Remove-Item -LiteralPath $BlockTmp -ErrorAction SilentlyContinue
}

# --- Verify-after ---
$ok = $true
try {
    $s = Get-Content -LiteralPath $Settings -Raw | ConvertFrom-Json
    $cmds = @()
    foreach ($g in @($s.hooks.SessionStart)) {
        foreach ($h in @($g.hooks)) { $cmds += $h.command }
    }
    if ($cmds -notcontains $HookCmd) { Warn "verify: SessionStart memory hook not detected in settings.json."; $ok = $false }
} catch {
    Warn "verify: could not read settings.json ($_)."; $ok = $false
}
if (-not (Select-String -LiteralPath $ClaudeMd -Pattern ([regex]::Escape($PersonaSkillShown)) -Quiet)) {
    Warn "verify: persona auto-load line not detected in $ClaudeMd."; $ok = $false
}

if ($ok) {
    Say "DONE — persona '$PersonaName' will auto-load every session."
    Say "  - SessionStart hook: present (memory brain primes each session)"
    Say "  - CLAUDE.md: PERSONA AUTO-LOAD block points at $PersonaSkillShown"
    Say "Open a NEW Claude Code session to confirm it wakes up as '$PersonaName', brain-aware."
    exit 0
} else {
    Die "one or more wirings did not verify (see warnings above). Nothing was clobbered; fix and re-run."
}
