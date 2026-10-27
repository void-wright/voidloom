<#
.SYNOPSIS
  Voidloom — Teammate Memory Brain Installer (Windows mirror of install.sh).

.DESCRIPTION
  PARITY NOTE (handoff round 1, L4): install.sh now chooses the compute with install/accelerator_plan.py
  (--accelerator, the torch channel per backend, the reranker RAM gate, --force-cpu recorded as
  WIZARD_ACCELERATOR=cpu, the P6-P8 device proofs). This Windows mirror does NOT do that yet: CUDA-on-Windows
  needs PyTorch's cuXXX index and was out of that round, and Windows is not a labelled row in the README's
  backend table. The brain's runtime still applies the reranker gate itself.

  Gives a teammate's Claude Code on Windows a persistent MEMORY BRAIN in one
  command. This is the Windows mirror of install.sh; the Mac path is the primary
  target for this handoff, but this script keeps the package complete and correct.

  TWO-TIER, lite-first design:
    * Tier 0 (ALWAYS succeeds, zero non-stdlib deps): the stdlib-only lite CLI
      (claude_memory_lite.py) + the seed-packed DB + the Node SessionStart hook
      + a teaching CLAUDE.md. Fully functional KEYWORD-recall brain on any python3.
      This step CANNOT fail the install.
    * Tier 1 (OPTIONAL upgrade, graceful fallback): a venv with the heavy stack
      (sentence-transformers + sqlite_vec + the CPU embedding tier's model) + the full
      claude_memory.py. Adds SEMANTIC recall. On ANY failure, falls back to Tier 0
      and tells the teammate how to upgrade later (exit 0).

  Steps mirror install.sh: A lay files; B Tier-0 seed load (always, verified);
  C Tier-1 attempt (optional); D wire hook into settings.json (DEFENSIVE merge via
  merge_config.py — NEVER clobber); E create/append CLAUDE.md (DEFENSIVE); F
  hardening (ACL lockdown via icacls + no-secret check + audit log); G token check
  (crons OPTIONAL behind -WithCrons, default OFF).

  CRITICAL: config merges are delegated to the cross-platform merge_config.py
  Python helper (NOT PowerShell's fragile ConvertFrom-Json), per the May 2026
  clobber incident.

.PARAMETER Tier0Only   Skip the Tier 1 venv attempt entirely.
.PARAMETER WithCrons   Also wire optional maintenance crons (default OFF; needs your own token).
.PARAMETER DryRun      Print what would happen; change nothing.
.PARAMETER Approvals   auto|manual. Once a month the self-eval proposes changes: auto = the brain approves its own
                       proposals (signed policy:auto, named at each session start for 7 days); manual = they wait for you
                       in `voidloom approvals --tui`. Asked at a console (blank = auto); a recorded choice is
                       re-used; with no console a NEW install takes auto and an existing brain with no recorded
                       choice is left as it is. Belief links and governance holds always wait for a person.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1 -Tier0Only
#>
[CmdletBinding()]
param(
    [switch]$Tier0Only,
    [switch]$WithCrons,
    [switch]$DryRun,
    [switch]$WireHooks,      # STEP P's question answered yes where no console can ask it
    [switch]$NoWireHooks,    # ... answered no
    [ValidateSet("auto", "manual")]
    [string]$Approvals = ""  # the owner's approvals choice (install.sh --approvals; 2026-10-26)
)

$ErrorActionPreference = "Continue"   # Tier 1 may fail without aborting; we guard Tier 0 explicitly.

# ---------------------------------------------------------------------------
# 0. SELF-LOCATE + PATHS
# ---------------------------------------------------------------------------
# This script lives at <package>/install/install.ps1 (promoted there with the
# other installers); the package root is its parent's parent. Until 2026-09-18
# it took its own directory and could not find organs\ -- and nobody knew,
# because the file had never been executed.
$PkgRoot    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ClaudeHome = if ($env:WIZARD_CLAUDE_HOME) { $env:WIZARD_CLAUDE_HOME } else { Join-Path $env:USERPROFILE ".claude" }
$BrainDir   = Join-Path $ClaudeHome "memory"
$SkillsDir  = Join-Path $ClaudeHome "skills"
$HooksDir   = Join-Path $ClaudeHome "hooks"
$DbPath     = Join-Path $BrainDir "claude_memory.db"
# Read BEFORE anything is written: an existing brain makes this an upgrade (the approvals question uses it).
$PriorInstall = Test-Path $DbPath
$Settings   = Join-Path $ClaudeHome "settings.json"
$ClaudeMd   = Join-Path $ClaudeHome "CLAUDE.md"
$EnvFile    = if ($env:WIZARD_ENV_FILE) { $env:WIZARD_ENV_FILE } else { Join-Path $env:USERPROFILE ".wizard-library\wizard-library-env" }
$LogsDir    = Join-Path $BrainDir "logs"

# CRUCIBLE-3 D9-F2 -- THE RERANKER THIS HOST WILL ACTUALLY LOAD.
# Mirrors organs/hybrid_retrieval.py:_resolve_reranker_backend() and the POSIX
# installer's resolve_reranker_backend(): an unrecognised value is not an error
# and does not disable reranking, it degrades to the default. Three resolvers,
# one rule -- and the whole finding was that the INSTALLER did not consult it.
$QwenRerankerRepo = "Qwen/Qwen3-Reranker-0.6B"   # runtime DEFAULT (causal-LM)
$BgeRerankerRepo  = "BAAI/bge-reranker-v2-m3"    # Apache-2.0 fallback (CrossEncoder)
$RerankerBackend  = if ("$($env:VOIDLOOM_RERANKER)".Trim().ToLower() -in @("qwen","bge")) {
                        "$($env:VOIDLOOM_RERANKER)".Trim().ToLower()
                    } else { "qwen" }
$RerankerRepo     = if ($RerankerBackend -eq "bge") { $BgeRerankerRepo } else { $QwenRerankerRepo }
$AuditLog   = Join-Path $LogsDir "audit.jsonl"
# C3 -- ONE INSTALLER. Was `node .../session-start.mjs`: a hook HOOK_MARKER_RE
# could not see, resolving the June flat layout, printing "brain not reachable
# yet" over a reachable brain and never emitting the arrival line the AGENTS.md
# notice checks for. The hooks organ owns wiring now; this string is only used
# for the verify-after read-back.
$HookInstaller = Join-Path $BrainDir "organs\voidloom_hooks_status.py"
# CRUCIBLE-3 U4/U6: this hardcoded "python3", which is NOT on a python.org Windows
# install -- while Find-Python (below) exists precisely to resolve the interpreter
# and :531-534 already use $PySys correctly. Same file, two answers. $HookCmd is
# assigned AFTER $PySys is resolved; see the assignment below Find-Python.

$MdBegin = "<!-- BEGIN WIZARD-LIBRARY MEMORY (managed; safe to leave in place) -->"
$MdEnd   = "<!-- END WIZARD-LIBRARY MEMORY -->"

function Say  ($m) { Write-Host "[install] $m" }
function Step ($m) { Write-Host ""; Write-Host "[install] ===== $m =====" }
function Warn ($m) { Write-Warning "[install] $m" }
function Die  ($m) { Write-Error "[install] FATAL: $m"; exit 1 }

# ---------------------------------------------------------------------------
# Detect a usable python (prefer py launcher / python on PATH; venv layout = Scripts\python.exe)
# ---------------------------------------------------------------------------
function Find-Python {
    if ($env:WIZARD_PYTHON_BIN -and (Get-Command $env:WIZARD_PYTHON_BIN -ErrorAction SilentlyContinue)) {
        return $env:WIZARD_PYTHON_BIN
    }
    foreach ($cand in @("python", "python3", "py")) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c) { if ($cand -eq "py") { return "py -3" } else { return $c.Source } }
    }
    return "python"
}
$PySys  = Find-Python
# Resolved here, not at the top of the file, because it needs Find-Python's answer.
$HookCmd = "$PySys `"$(Join-Path $BrainDir 'hooks\session_start_inject.py')`""
$VenvDir = Join-Path $BrainDir "venv"
$VenvPy  = Join-Path $VenvDir "Scripts\python.exe"   # Windows venv layout

function Invoke-Py {
    param([string]$Exe, [string[]]$ArgList)
    if ($Exe -eq "py -3") { & py -3 @ArgList } else { & $Exe @ArgList }
    return $LASTEXITCODE
}
# The program's OUTPUT only (Invoke-Py also writes the exit code into the pipeline, which a JSON parse or a printed
# line would carry). $LASTEXITCODE still holds the status after the call.
function Invoke-PyOut {
    param([string]$Exe, [string[]]$ArgList)
    if ($Exe -eq "py -3") { & py -3 @ArgList } else { & $Exe @ArgList }
}

# THE EMBEDDING TIER (tier1-upgrade loop, iteration 4). This installer builds the CPU tier, chosen ONCE here by its key.
# Its table, width and model repo are READ from the engine's own registry, organs\claude_memory.py EMBED_TIERS, by
# AST -- exactly as install.sh's C.declare reads it -- and typed nowhere else in this file. Unreadable registry: Tier 1
# is skipped with that reason (Tier 0 is unaffected); nothing is guessed.
$EmbedTier = "nomic"
function Get-EngineTier {
    param([string]$Py, [string]$Engine, [string]$Key)
    $src = @'
import ast, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
consts, tiers = {}, None
for n in tree.body:
    if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
        if isinstance(n.value, ast.Constant):
            consts[n.targets[0].id] = n.value.value
        elif n.targets[0].id == "EMBED_TIERS":
            tiers = n.value
val = lambda e: e.value if isinstance(e, ast.Constant) else consts[e.id]
reg = {val(k): [val(e) for e in v.elts[:3]] for k, v in zip(tiers.keys, tiers.values)}
t, d, r = reg[sys.argv[2]]
print("%s|%s|%s" % (t, d, r))
'@
    $tmp = Join-Path $env:TEMP ("wl_engine_tier_" + $PID + ".py")
    [System.IO.File]::WriteAllText($tmp, $src, (New-Object System.Text.UTF8Encoding($false)))
    try { $out = (Invoke-Py $Py @($tmp, $Engine, $Key)) | Select-Object -First 1 } catch { $out = "" }
    Remove-Item $tmp -ErrorAction SilentlyContinue
    $f = ("" + $out).Trim().Split("|")
    if ($f.Count -ne 3 -or -not ($f[1] -match '^\d+$')) { return $null }
    return @{ Table = $f[0]; Dim = $f[1]; Repo = $f[2] }
}
$EngineTier = Get-EngineTier -Py $PySys -Engine (Join-Path $PkgRoot "organs\claude_memory.py") -Key $EmbedTier
$EmbedTable = if ($EngineTier) { $EngineTier.Table } else { "" }
$EmbedDim   = if ($EngineTier) { $EngineTier.Dim }   else { "" }
$EmbedRepo  = if ($EngineTier) { $EngineTier.Repo }  else { "" }

# ---------------------------------------------------------------------------
# BANNER
# ---------------------------------------------------------------------------
Say "=================================================================="
Say " Voidloom — Teammate Memory Brain Installer (Windows)"
Say "=================================================================="
Say "Install into: $ClaudeHome"
Say "Brain DB:     $DbPath"
Say "Python:       $PySys"
Say ("Crons:        " + $(if ($WithCrons) { "ENABLED (-WithCrons)" } else { "disabled (default; use -WithCrons to enable)" }))
if ($DryRun) { Say "** DRY RUN — no changes will be made **" }

foreach ($req in @("organs\claude_memory_lite.py","seed\seed_pack.json","install\self_check.py","organs\schema_migrator.py","install\CLAUDE.md.template")) {
    if (-not (Test-Path (Join-Path $PkgRoot $req))) { Die "package incomplete — missing $req in $PkgRoot" }
}

# ---------------------------------------------------------------------------
# STEP A — LAY FILES
# ---------------------------------------------------------------------------
Step "STEP A — Laying files into $ClaudeHome"
foreach ($d in @($BrainDir, $SkillsDir, $HooksDir, $LogsDir)) {
    if ($DryRun) { Say "  DRY: mkdir $d" } else { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
function Copy-IfPresent ($name, $dest) {
    $src = Join-Path $PkgRoot $name
    if (Test-Path $src) {
        if ($DryRun) { Say "  DRY: copy $name -> $dest" } else { Copy-Item -Force $src $dest }
    }
}
# Whole memory/ payload if shipped
if (Test-Path (Join-Path $PkgRoot "memory")) {
    if ($DryRun) { Say "  DRY: copy memory\* -> $BrainDir" }
    else { Copy-Item -Recurse -Force (Join-Path $PkgRoot "memory\*") $BrainDir }
}
# Keystone Tier-0 files always
Copy-IfPresent "organs\claude_memory_lite.py" (Join-Path $BrainDir "organs\claude_memory_lite.py")
Copy-IfPresent "seed\seed_pack.json"    (Join-Path $BrainDir "seed\seed_pack.json")
Copy-IfPresent "install\self_check.py"  (Join-Path $BrainDir "install\self_check.py")   # Tier-0 "is my brain alive?" verifier
# Support + Tier-1 recall-path payload if shipped at package root.
# (Mirrors install.sh: the full CLI + the modules it imports for store/recall/context.)
foreach ($f in @("organs","migrations","hooks","install","docs","tools","seed")) {
    Copy-IfPresent $f (Join-Path $BrainDir $f)
}
# C3/M8: the loop above already copies migrations\ wholesale. This block copied
# every .sql a SECOND time, FLAT into the brain root, which is why this script's
# inline creator read schema.sql from there while install.sh read it from
# migrations/. Two installers disagreeing about layout is this class; one copy,
# one location.
# Node hook
# C3: the Node hook is retired; nothing lays it down and nothing wires it.
# Generic identity/recall-map OUTLINES (scaffolds, optional — no specific identity)
if (Test-Path (Join-Path $PkgRoot "skills")) {
    if ($DryRun) { Say "  DRY: copy skills\* -> $SkillsDir (scaffold, not prescription)" }
    else { Copy-Item -Recurse -Force (Join-Path $PkgRoot "skills\*") $SkillsDir }
} else { Say "  (no outline scaffolds in package — the brain works fully without them)" }

# A.1c — the voidloom CLI (the LLM's unified memory toolkit) + Windows PATH launcher.
#        Mirrors install.sh A.1c: copy voidloom\ beside the brain, then put a
#        `voidloom` command on PATH (a generated brain-baked .cmd in ~\.local\bin)
#        so the SessionStart hook's `which voidloom` resolves on Windows. Skipped
#        cleanly if voidloom did not ship — the brain works fully without it (the
#        hook falls back to `python3 claude_memory.py`).
$VoidloomSrc = Join-Path $PkgRoot "voidloom"
if (Test-Path $VoidloomSrc) {
    $VoidloomDest = Join-Path $BrainDir "voidloom"
    if ($DryRun) { Say "  DRY: copy voidloom\ -> $VoidloomDest ; write ~\.local\bin\voidloom.cmd + ensure on PATH" }
    else {
        Copy-Item -Recurse -Force $VoidloomSrc $VoidloomDest
        $LocalBin = Join-Path $env:USERPROFILE ".local\bin"
        New-Item -ItemType Directory -Force -Path $LocalBin | Out-Null
        $brainAbs = (Resolve-Path $BrainDir).Path
        # CRUCIBLE-3 U4 (accessibility seat, non-ASCII home path): this wrote the
        # launcher with -Encoding ASCII while the launcher EMBEDS $brainAbs. An
        # account name like "Ayse" with a non-ASCII character became "Ay?e", the
        # PYTHONPATH was invalid, and EVERY voidloom verb failed with "No module
        # named voidloom" -- AFTER the installer printed INSTALL COMPLETE. The
        # amplifier that seat named and no sighted reviewer would: at NVDA's
        # default punctuation level the substituted '?' IS NOT SPOKEN, so the
        # corruption that breaks every command is inaudible to the person it
        # breaks it for.
        $cmdLines = @(
            '@echo off',
            'REM voidloom launcher (generated by install.ps1) -- runs the brain-local package.',
            ('set "PYTHONPATH=' + $brainAbs + ';%PYTHONPATH%"'),
            'where python >nul 2>nul && (python -m voidloom %*) || (py -3 -m voidloom %*)'
        )
        $cmdPath = Join-Path $LocalBin "voidloom.cmd"
        # cmd.exe reads a .cmd in the console's OEM code page, so that is what the
        # file is written in -- not ASCII, which cannot carry the path at all.
        try {
            $enc = [System.Text.Encoding]::GetEncoding(
                     [System.Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
        } catch { $enc = [System.Text.Encoding]::UTF8 }
        [System.IO.File]::WriteAllLines($cmdPath, $cmdLines, $enc)
        # READ THE WORLD BACK. A launcher whose embedded path did not survive the
        # encoding is worse than no launcher: it fails later, elsewhere, and with a
        # message that names neither this step nor this cause. If it did not
        # round-trip we SAY SO and print the lines for a manual paste, which is the
        # same refusal shape the hooks installer already uses.
        $readBack = [System.IO.File]::ReadAllText($cmdPath, $enc)
        if ($readBack -notmatch [regex]::Escape($brainAbs)) {
            Warn ("the generated launcher at $cmdPath does not round-trip your brain path " +
                  "in this console's code page, so `voidloom` would fail with 'No module named " +
                  "voidloom'. Nothing else was changed. Create it by hand with these lines, or " +
                  "run voidloom as: $PySys -m voidloom")
            foreach ($l in $cmdLines) { Say "    $l" }
        }
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($userPath -notlike "*$LocalBin*") {
            [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(';') + ';' + $LocalBin), "User")
            Say "  added $LocalBin to user PATH (restart shells to pick it up)"
        }
        Say "  voidloom CLI installed -> $VoidloomDest (+ launcher on PATH)"
    }
} else {
    Say "  (no voidloom CLI in package — the brain works fully without it; hook uses python3 claude_memory.py)"
}

$Lite = Join-Path $BrainDir "organs\claude_memory_lite.py"
$Seed = Join-Path $BrainDir "seed\seed_pack.json"

# ---------------------------------------------------------------------------
# STEP B — TIER 0 SEED LOAD (ALWAYS; CANNOT FAIL THE INSTALL)
# ---------------------------------------------------------------------------
Step "STEP B — Tier 0: create DB + load agnostic seed pack (stdlib-only)"
$Tier0Ok = $false
$AcceptPass = $false
if ($DryRun) {
    Say "  DRY: would create $DbPath (schema + FTS5 + kind), load $Seed, verify recall"
    $Tier0Ok = $true
} else {
    if (-not (Test-Path $DbPath)) {
        Say "  creating fresh brain DB (stdlib sqlite3): $DbPath"
        $createPy = @'
import sqlite3, sys, pathlib
db_path, brain_dir = sys.argv[1], pathlib.Path(sys.argv[2])
conn = sqlite3.connect(db_path)
def apply(name, required):
    # CRUCIBLE-3 U3: this read `brain_dir / name` -- the brain ROOT -- because an
    # earlier version of THIS script copied every .sql a second time, flat, into
    # the root. That second copy was DELETED (see the C3/M8 note above, "one copy,
    # one location") and this consumer was never moved with it, so STEP B exited 1
    # on "required schema file missing" and THE WINDOWS INSTALLER COULD NOT
    # COMPLETE. The installer received the migration's comments and not its code.
    # One location, and it is the one install.sh already uses: migrations/.
    p = brain_dir / "migrations" / name
    if p.exists():
        conn.executescript(p.read_text(encoding="utf-8")); return True
    if required:
        stray = brain_dir / name
        extra = (f" (a copy exists at {stray}, but the flat layout was retired -- "
                 f"the package ships SQL in migrations/ only)") if stray.exists() else ""
        sys.stderr.write(f"FATAL: required schema file missing: {p}{extra}\n"); sys.exit(1)
    return False
apply("schema.sql", True)
apply("migration_001_fts5.sql", True)
cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
if "kind" not in cols: conn.execute("ALTER TABLE memories ADD COLUMN kind TEXT DEFAULT 'episodic'")
if "confidence" not in cols: conn.execute("ALTER TABLE memories ADD COLUMN confidence REAL DEFAULT 1.0")
conn.execute("INSERT OR IGNORE INTO metadata (key, value) VALUES ('schema_version','1.0')")
conn.commit()
conn.close()
# C2/M8 -- this applied schema.sql + migration_001 only, then asserted a ONE-name
# list (`memories_fts`). The package ships 29 migrations; the brain came up with
# a fraction of them and the gate said FTS5 ready. Apply what the package ships,
# derived from migrations/, and assert against the package rather than a name.
# `brain_dir` here is ~/.claude/memory itself. The SQL is copied ONCE, into
# <brain_dir>/migrations/ (the wholesale loop above); the flat second copy this
# comment used to describe was retired, and `apply()` now reads from migrations/
# like install.sh does. The migrator wants the same real directory.
sys.path.insert(0, str(brain_dir / "organs"))
import schema_migrator as sm
res = sm.apply(db_path, brain_dir / "migrations")
if res.get("files_with_errors"):
    sys.stderr.write(f"FATAL: migrations failed: {res['files_with_errors'][:2]}\n"); sys.exit(1)
if res.get("missing"):
    sys.stderr.write(
        f"FATAL: brain is missing {res['missing_count']} of the {res['reference_objects']} "
        f"objects this package builds: {', '.join(res['missing'][:10])}\n"); sys.exit(1)
for u in res.get("unmeasurable") or []:
    sys.stderr.write(f"UNMEASURABLE: {u}\n")
print(f"  DB created. {res['brain_objects']} objects, "
      f"{res['migrations_on_disk']} migrations applied. FTS5 ready.")
'@
        $tmp = Join-Path $env:TEMP "wl_create_db.py"; Set-Content -Path $tmp -Value $createPy -Encoding UTF8
        Invoke-Py $PySys @($tmp, $DbPath, $BrainDir) | Out-Null
        if ($LASTEXITCODE -ne 0) { Die "could not create the brain DB (Step B is the guaranteed tier — aborting)" }
        Remove-Item $tmp -ErrorAction SilentlyContinue
    } else { Say "  brain DB already exists; leaving it in place (idempotent)" }

    Say "  loading agnostic seed pack via lite CLI"
    $env:WIZARD_LIBRARY_DB = $DbPath
    Invoke-Py $PySys @($Lite, "load-seed", "--file", $Seed) | Out-Null

    # VERIFY the brain is alive via the shipped self_check.py — the SAME command
    # the teammate runs — which exercises the real recall path against the seed
    # memories. Stdlib-only (Tier 0). Falls back to a minimal recall gate.
    $SelfCheck = Join-Path $BrainDir "install\self_check.py"
    if (Test-Path $SelfCheck) {
        Say "  verifying the brain is alive (self_check.py against the seed pack)"
        & { $env:WIZARD_LIBRARY_DB = $DbPath; & $PySys $SelfCheck --quiet }
        if ($LASTEXITCODE -eq 0) {
            Say "  Tier 0 VERIFIED — self-check passed; your keyword-recall brain is live."
            $Tier0Ok = $true
            $AcceptPass = $true      # the ONLY place an explicit acceptance PASS is recorded (STEP P gates on it)
        } else {
            Warn "self_check.py reported failures; falling back to the minimal recall gate."
            $r = & { $env:WIZARD_LIBRARY_DB = $DbPath; (Invoke-Py $PySys @($Lite, "recall", "--query", "store discipline", "--top_k", "1")) } 2>&1 | Out-String
            if ($r -match '"status":\s*"ok"') {
                Say "  minimal recall gate passed — brain is usable; investigate the self-check warnings."
                $Tier0Ok = $true
            } else {
                Die "Tier 0 verification FAILED — neither self-check nor minimal recall returned ok. Check $Lite and $DbPath."
            }
        }
    } else {
        Say "  verifying keyword recall works (self_check.py not present)"
        $r = & { $env:WIZARD_LIBRARY_DB = $DbPath; (Invoke-Py $PySys @($Lite, "recall", "--query", "store discipline", "--top_k", "1")) } 2>&1 | Out-String
        if ($r -match '"status":\s*"ok"') {
            Say "  Tier 0 VERIFIED — keyword-recall brain is live."
            $Tier0Ok = $true
        } else {
            Die "Tier 0 verification FAILED — recall did not return ok. Check $Lite and $DbPath."
        }
    }
}

# ---------------------------------------------------------------------------
# STEP C — TIER 1 ATTEMPT (OPTIONAL; GRACEFUL FALLBACK)
# ---------------------------------------------------------------------------
Step "STEP C — Tier 1 (optional): venv + semantic stack ($EmbedTier, CPU)"
$Tier1Ok = $false
$Tier1Skip = ""
function Tier1-Hint ($reason) {
    Write-Host @"
  ----------------------------------------------------------------
  Tier 0 is ACTIVE — you have a working keyword-recall brain now.
  The optional SEMANTIC upgrade (Tier 1) was not completed:
    reason: $reason
  To add semantic recall later, ensure Python >= 3.10, then re-run:
    powershell -ExecutionPolicy Bypass -File install.ps1
  or build by hand:
    $PySys -m venv "$VenvDir"
    "$VenvPy" -m pip install --upgrade pip
    "$VenvPy" -m pip install sentence-transformers sqlite_vec einops
    `$env:WIZARD_EMBED_MODEL='$EmbedTier'; "$VenvPy" "$(Join-Path $BrainDir 'organs\claude_memory.py')" init
  ----------------------------------------------------------------
"@
}

if ($Tier0Only) {
    $Tier1Skip = "-Tier0Only requested"; Say "  skipping Tier 1 ($Tier1Skip)"; Tier1-Hint $Tier1Skip
} elseif (-not (Test-Path (Join-Path $BrainDir "organs\claude_memory.py"))) {
    $Tier1Skip = "full CLI (claude_memory.py) not shipped in this package"
    Say "  skipping Tier 1 ($Tier1Skip) — this is a lean Tier-0 handoff"; Tier1-Hint $Tier1Skip
} elseif (-not $EngineTier) {
    $Tier1Skip = "the engine's tier registry (organs\claude_memory.py EMBED_TIERS) could not be read for '$EmbedTier'"
    Say "  skipping Tier 1 ($Tier1Skip)"; Tier1-Hint $Tier1Skip
} elseif ($DryRun) {
    Say "  DRY: would build venv at $VenvDir, pip install heavy stack, PRE-DOWNLOAD the embedder + the reranker backend '$RerankerBackend' ($RerankerRepo), init, re-embed"
} else {
    Invoke-Py $PySys @("-m","venv",$VenvDir) | Out-Null
    if (Test-Path $VenvPy) {
        Say "  venv created at $VenvDir"
        & $VenvPy -m pip install --upgrade pip *> $null
        & $VenvPy -m pip install sentence-transformers sqlite_vec einops *> $null
        if ($LASTEXITCODE -eq 0) {
            Say "  heavy stack installed (sentence-transformers + sqlite_vec)"
            $env:WIZARD_EMBED_MODEL = $EmbedTier
            $env:WIZARD_LIBRARY_DB = $DbPath
            $env:WIZARD_LIBRARY_HOME = $BrainDir
            $env:PYTHON_KEYRING_BACKEND = "keyring.backends.null.Keyring"
            $env:HF_HUB_DISABLE_TELEMETRY = "1"

            # PRE-DOWNLOAD the models this host will ACTUALLY load.
            #
            # CRUCIBLE-3 D9-F2: this fetched BAAI/bge-reranker-v2-m3 and labelled it
            # "<- default hybrid recall uses this". It is the FALLBACK. The runtime
            # default is Qwen/Qwen3-Reranker-0.6B, so "nothing will lazy-download on
            # first recall" was false on every Windows install, by ~1.2 GB — while
            # the fetch it DID perform was 2.29 GB for a model most hosts never load.
            # Resolve the backend the way the runtime does (see $RerankerBackend
            # above) and fetch THAT.
            Say "  pre-downloading required models to ~/.cache/huggingface (one-time):"
            Say "    - embedding model  $EmbedRepo"
            Say "    - reranker model   $RerankerRepo   (backend '$RerankerBackend' — this host loads this one)"
            $env:RERANKER_MODEL = $RerankerRepo
            $env:EMBED_MODEL_REPO = $EmbedRepo
            $predlPy = @'
import os, sys
EMBED_MODEL    = os.environ["EMBED_MODEL_REPO"]
RERANKER_MODEL = os.environ["RERANKER_MODEL"]
try:
    from sentence_transformers import SentenceTransformer
    from huggingface_hub import snapshot_download
except Exception as e:
    print(f"  ! sentence-transformers import failed: {e}", file=sys.stderr); sys.exit(3)
try:
    print(f"  ... fetching embedder {EMBED_MODEL}", flush=True)
    SentenceTransformer(EMBED_MODEL, trust_remote_code=True)
    # snapshot_download, not CrossEncoder: the default backend is a
    # Qwen3ForCausalLM, which CrossEncoder cannot instantiate. The snapshot is the
    # cache both loaders read.
    print(f"  ... fetching reranker {RERANKER_MODEL}", flush=True)
    snapshot_download(RERANKER_MODEL)
except Exception as e:
    print(f"  ! model pre-download failed: {e}", file=sys.stderr); sys.exit(4)
print("  both models cached — full L4 (semantic + reranked hybrid) recall ready.")
sys.exit(0)
'@
            $tmpPre = Join-Path $env:TEMP "wl_predownload.py"; Set-Content -Path $tmpPre -Value $predlPy -Encoding UTF8
            & $VenvPy $tmpPre
            $preDlOk = ($LASTEXITCODE -eq 0)
            Remove-Item $tmpPre -ErrorAction SilentlyContinue
            if ($preDlOk) {
                Say "  models pre-downloaded ($EmbedRepo + $RerankerRepo) — nothing will lazy-download on first recall."
            } else {
                Warn "could not pre-download one or both models (offline, low disk, or HF unreachable)."
                Warn "Tier 0 (keyword recall) is unaffected. To fetch the models by hand:"
                Warn "  & `"$VenvPy`" -c `"from sentence_transformers import SentenceTransformer; from huggingface_hub import snapshot_download; SentenceTransformer('$EmbedRepo', trust_remote_code=True); snapshot_download('$RerankerRepo')`""
            }

            & $VenvPy (Join-Path $BrainDir "organs\claude_memory.py") init *> $null
            if ($LASTEXITCODE -eq 0) {
                Say "  full CLI init OK (vec0 embedding table ready, $EmbedTier $EmbedDim-dim)"
                # Re-embed seeded memories using the full CLI's own encoder for this tier.
                $reembedPy = @'
import sys, sqlite3
db_path, brain_dir = sys.argv[1], sys.argv[2]
sys.path.insert(0, brain_dir)
import sqlite_vec
import claude_memory as cm
conn = sqlite3.connect(db_path)
conn.enable_load_extension(True); sqlite_vec.load(conn); conn.enable_load_extension(False)
table = cm._active_embed_table()
rows = conn.execute("SELECT id, content FROM memories WHERE is_archived=0").fetchall()
have = {r[0] for r in conn.execute(f"SELECT memory_id FROM {table}").fetchall()}
todo = [(mid, c) for mid, c in rows if mid not in have]
n = 0
for mid, content in todo:
    conn.execute(f"INSERT OR REPLACE INTO {table} (memory_id, embedding) VALUES (?, ?)", (mid, cm._encode(content, "search_document")))
    n += 1
conn.commit(); conn.close()
print(f"  re-embedded {n} memories into {table} ({cm._active_embed_dim()}-dim)")
'@
                $tmp2 = Join-Path $env:TEMP "wl_reembed.py"; Set-Content -Path $tmp2 -Value $reembedPy -Encoding UTF8
                & $VenvPy $tmp2 $DbPath $BrainDir
                Remove-Item $tmp2 -ErrorAction SilentlyContinue
                & $VenvPy (Join-Path $BrainDir "organs\claude_memory.py") recall --query "how do I capture knowledge as I work" --top_k 1 *> $null
                if ($LASTEXITCODE -eq 0) {
                    Say "  Tier 1 VERIFIED — semantic recall is live."
                    $Tier1Ok = $true
                } else { $Tier1Skip = "semantic recall verification did not succeed" }
            } else { $Tier1Skip = "full CLI init failed (sqlite_vec/model load)" }
        } else { $Tier1Skip = "pip install of the heavy stack failed (network or build tools)" }
    } else { $Tier1Skip = "venv creation failed" }

    if (-not $Tier1Ok) {
        Warn "Tier 1 not completed: $Tier1Skip"
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue }
        Tier1-Hint $Tier1Skip
    }
}

# ---------------------------------------------------------------------------
# STEP D — WIRE THE SESSIONSTART HOOK (DEFENSIVE merge via merge_config.py)
# ---------------------------------------------------------------------------
Step "STEP D — SessionStart hook: not wired here (STEP P asks; -WireHooks / -NoWireHooks)"
$Merge = Join-Path $BrainDir "merge_config.py"   # STEP E's merger
# This step used to wire the hook UNASKED (--yes). The only wiring path is now STEP P's question (or -WireHooks),
# exactly as install.sh STEP P does: $Settings is never touched without the owner's answer.
Say "  $Settings is not touched here; STEP P asks whether to wire the hook (it runs: $HookCmd)."

# ---------------------------------------------------------------------------
# STEP E — CREATE/APPEND CLAUDE.md (DEFENSIVE marker-delimited via merge_config.py)
# ---------------------------------------------------------------------------
Step "STEP E — Installing teaching CLAUDE.md at $ClaudeMd (append-safe)"
$Tmpl = Join-Path $PkgRoot "CLAUDE.md.template"
if (Test-Path $Merge) {
    if ($DryRun) { Say "  DRY: merge_config.py claude-md --file $ClaudeMd --block-file $Tmpl (marker-delimited)" }
    else { Invoke-Py $PySys @($Merge, "claude-md", "--file", $ClaudeMd, "--block-file", $Tmpl, "--begin", $MdBegin, "--end", $MdEnd) }
} else {
    Warn "merge_config.py not present — CLAUDE.md NOT modified (refusing to risk clobber)."
    Warn "Append $Tmpl to $ClaudeMd manually."
}

# >>> env-writer (the gate extracts the text between these two marker lines and runs it alone) >>>
# THE WINDOWS ENV-FILE WRITER (env-file writer pass, iteration 4: the POSIX writer's contract, CRITERIA W1, applied here).
# Measured on the writer this replaces (Windows PowerShell 5.1, executed on extracted copies): `Set-Content -Encoding UTF8`
# wrote a UTF-8 BYTE-ORDER MARK and CRLF line ends, and wrote Windows paths unquoted -- so the package's ONE reader of this
# file (install/env_file.py: systemd's EnvironmentFile= rules, which token_doctor and organs/voidloom_device.py read it by)
# read WIZARD_LIBRARY_DB=D:\brain\... as 'D:brain...' (an unquoted backslash keeps only the next character) and flagged
# every line. And its create-only test was `Test-Path $EnvFile`, a WILDCARD test: under a directory named with '[' it
# answered "absent" for an EXISTING owner file and went on to write -- saved only because `Set-Content -Path` with that
# same wildcard path then failed to bind its own -Encoding parameter; for the same reason, with no file there, it never
# created one (no message either time).
# Now: each line is spelled so that reader AND a POSIX shell read exactly the value (bare when every character is one both
# take literally, else single-quoted, a ' written as '"'"'); no value with CR, LF or NUL is written (no spelling carries
# it); UTF-8 WITHOUT a byte-order mark, LF line ends; CREATE-ONLY twice over (-LiteralPath, then FileMode.CreateNew, which
# fails if the file appeared meanwhile) -- an existing file is never opened for writing; the file is READ BACK (bytes ==
# the image) and read by the package's reader, every key compared with the value meant and every line required to read
# the same to systemd and to a shell -- a file that reader misreads is REMOVED, never left. ASCII only in this block.
function ConvertTo-VoidloomEnvLine {
    param([string]$Key, [string]$Value)
    if ($Key -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$') { return $null }
    if ($Value.IndexOfAny([char[]]@([char]13, [char]10, [char]0)) -ge 0) { return $null }
    if ($Value -cmatch '^[A-Za-z0-9_@%+,./:=#-]+$') { return ($Key + "=" + $Value) }
    return ($Key + "=" + ((($Value -split "'") | ForEach-Object { "'" + $_ + "'" }) -join '"''"'))
}
function Write-VoidloomEnvFile {
    # Returns one line: "created", "exists" (nothing was opened for writing), "refused: <why>" (nothing is left behind),
    # or "unverified: <why>" (written and read back byte for byte; the package's reader could not be run).
    param([string]$Path, [string[]]$Comments, [System.Collections.IDictionary]$Values, [string]$Py, [string]$Reader)
    if (Test-Path -LiteralPath $Path) { return "exists" }
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($c in $Comments) {
        if ($c.IndexOfAny([char[]]@([char]13, [char]10, [char]0)) -ge 0) { return "refused: a comment line holds a line break" }
        $lines.Add("# " + $c)
    }
    foreach ($k in @($Values.Keys)) {
        $l = ConvertTo-VoidloomEnvLine ([string]$k) ([string]$Values[$k])
        if ($null -eq $l) { return ("refused: " + $k + " has a value no env-file line can carry (a CR, LF or NUL), or an invalid name") }
        $lines.Add($l)
    }
    $image = ($lines -join "`n") + "`n"
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($image)
    try {
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    } catch {
        if (Test-Path -LiteralPath $Path) { return "exists" }
        return ("refused: " + $Path + " could not be created (" + $_.Exception.Message + ")")
    }
    try {
        $fs.Write($bytes, 0, $bytes.Length)
        $fs.Flush($true)
    } catch {
        $fs.Dispose()
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        return ("refused: the write of " + $Path + " failed (" + $_.Exception.Message + "); the partial file was removed")
    }
    $fs.Dispose()
    $back = [System.IO.File]::ReadAllBytes($Path)
    if ([Convert]::ToBase64String($back) -ne [Convert]::ToBase64String($bytes)) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        return ("refused: " + $Path + " read back " + $back.Length + " bytes, not the " + $bytes.Length + " written; it was removed")
    }
    if (-not $Reader -or -not (Test-Path -LiteralPath $Reader)) { return ("unverified: the package's reader is not at " + $Reader) }
    $u8 = New-Object System.Text.UTF8Encoding($false)
    $hex = { param($s) "h" + ((@($u8.GetBytes([string]$s)) | ForEach-Object { $_.ToString("x2") }) -join "") }
    $code = "import sys; h=lambda a: bytes.fromhex(a[1:]).decode('utf-8'); sys.path.insert(0, h(sys.argv[1])); import env_file as e; " +
            "r=e.parse(open(h(sys.argv[2]),'rb').read()); ev=r['effective']; " +
            "bad=[h(k) for k,v in zip(sys.argv[3::2],sys.argv[4::2]) if ev.get(h(k),(None,))[0]!=h(v)]; " +
            "bad+=['line %d' % u.line for u in r['units'] if u.cls!='safe']; " +
            "print(' '.join(bad)); sys.exit(1 if (bad or r['error']) else 0)"
    $pyArgs = @("-B", "-c", $code, (& $hex (Split-Path -Parent $Reader)), (& $hex $Path))
    foreach ($k in @($Values.Keys)) { $pyArgs += (& $hex $k); $pyArgs += (& $hex $Values[$k]) }
    try {
        if ($Py -eq "py -3") { $said = & py -3 @pyArgs } else { $said = & $Py @pyArgs }
        $rc = $LASTEXITCODE
    } catch { return ("unverified: the package's reader could not be run with " + $Py + " (" + $_.Exception.Message + ")") }
    if ($rc -eq 0) { return "created" }
    if ($rc -eq 1) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        return ("refused: the package's reader (install\env_file.py, systemd's rules) reads " + (($said | Out-String).Trim()) + " of " + $Path + " differently from what was meant; the file was removed")
    }
    return ("unverified: the package's reader exited " + $rc + " with " + $Py)
}
# <<< env-writer <<<

# ---------------------------------------------------------------------------
# STEP F — SECURITY HARDENING (Windows: ACL lockdown + no-secret + audit)
# ---------------------------------------------------------------------------
Step "STEP F — Security hardening (ACL lockdown + no-secret check + audit)"
if ($DryRun) {
    Say "  DRY: icacls lockdown on $BrainDir (owner-only); audit log; no-secret scan"
} else {
    # F.1 — restrict the brain dir to the current user (remove inheritance, grant owner only).
    try {
        $me = "$env:USERDOMAIN\$env:USERNAME"
        # CRUCIBLE-3 U6: `& icacls … *> $null` followed IMMEDIATELY by the success
        # line, with $LASTEXITCODE never checked — in a file that checks
        # $LASTEXITCODE at six other sites, so the idiom is known and was omitted on
        # the ONE security control. $ErrorActionPreference is "Continue", so a native
        # exe failure does not throw either: the catch below could not catch it. The
        # control manufactured its own evidence.
        & icacls "$BrainDir" /inheritance:r /grant:r "${me}:(OI)(CI)F" *> $null
        $aclRc = $LASTEXITCODE
        # READ IT BACK. A mode that was set and a mode that HOLDS are different facts,
        # and only the second one is what the success line claims.
        $inherited = $null
        try {
            $acl = Get-Acl -Path $BrainDir
            $inherited = @($acl.Access | Where-Object { $_.IsInherited }).Count
            $others    = @($acl.Access | Where-Object {
                             -not $_.IsInherited -and $_.IdentityReference -ne $me }).Count
        } catch { $others = $null }
        if ($aclRc -eq 0 -and $inherited -eq 0 -and $others -eq 0) {
            Say "  ACL lockdown applied to $BrainDir — read back: inheritance off, no other principals."
        } else {
            Warn ("the ACL on $BrainDir was NOT verified owner-only (icacls rc=$aclRc; " +
                  "inherited entries=$inherited; other principals=$others). Your brain may " +
                  "be readable by other users on this machine. Fix by hand: " +
                  "icacls `"$BrainDir`" /inheritance:r /grant:r `"${me}:(OI)(CI)F`"")
        }
    } catch { Warn "could not set ACLs on $BrainDir ($_) — verify manually." }

    # F.2 — audit log.
    if (-not (Test-Path $AuditLog)) { New-Item -ItemType File -Force -Path $AuditLog | Out-Null }
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $AuditLog -Value ('{"ts":"' + $ts + '","event":"install","actor":"install.ps1","note":"Voidloom memory brain installed (teammate handoff)"}')

    # F.3 — env file (no token shipped; teammate mints their own). Created ONLY when absent, by Write-VoidloomEnvFile
    # (above, between the env-writer markers): spelled, encoded and read back as the package's one reader reads it.
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $EnvFile) | Out-Null
    $embed = if ($Tier1Ok) { $EmbedTier } else { "none" }
    $envHow = Write-VoidloomEnvFile -Path $EnvFile -Py $PySys -Reader (Join-Path $PkgRoot "install\env_file.py") -Comments @(
            "Voidloom env (teammate). NO secret is shipped in the package.",
            "Mint YOUR OWN Claude token with 'claude setup-token' and add it below if you enable crons.") -Values ([ordered]@{
            WIZARD_LIBRARY_DB = $DbPath; WIZARD_LIBRARY_HOME = $BrainDir; WIZARD_EMBED_MODEL = $embed })
    if ($envHow -eq "created" -or $envHow -like "unverified*") {
        try { & icacls "$EnvFile" /inheritance:r /grant:r "$env:USERDOMAIN\${env:USERNAME}:F" *> $null } catch {}
    }
    if ($envHow -eq "created") { Say "  env file created: $EnvFile (read back as the package's reader reads it)" }
    elseif ($envHow -eq "exists") { Say "  env file kept as it is: $EnvFile (this installer only creates it when absent)" }
    elseif ($envHow -like "unverified*") { Warn ("env file created, byte-checked, NOT checked by the package's reader -- " + $envHow.Substring(12)) }
    else { Warn ("env file NOT written -- " + $envHow.Substring(9) + ". Create it by hand with bare KEY=value lines (a value with a backslash in single quotes).") }

    # F.4 — assert NO real token shipped (long body; ignore placeholder/docs with angle brackets).
    $leak = $false
    $re = 'sk-ant-(oat|api)[0-9]{2}-[A-Za-z0-9_-]{40,}'
    foreach ($scan in @($BrainDir, $PkgRoot)) {
        $hits = Get-ChildItem -Recurse -File $scan -ErrorAction SilentlyContinue |
                Select-String -Pattern $re -ErrorAction SilentlyContinue |
                Where-Object { $_.Line -notmatch '[<>]' }
        if ($hits) { Warn "a REAL token value was found under $scan — re-cut WITHOUT it:"; $hits | ForEach-Object { Write-Host "    $($_.Path):$($_.LineNumber)" }; $leak = $true }
    }
    if (-not $leak) { Say "  no-secret check PASSED (no live token shipped; placeholders/docs ignored)." }
    # Say only what was observed. The two claims above each print their own verified
    # or unverified line now; this summary no longer asserts both unconditionally.
    Say "  hardening step complete — see the per-control lines above for what was verified."
}

# ---------------------------------------------------------------------------
# STEP G — TOKEN CHECK (+ optional crons behind -WithCrons)
# ---------------------------------------------------------------------------
Step "STEP G — Token check + (optional) maintenance crons"
$TokDoc = Join-Path $BrainDir "token_doctor.py"
if (-not $WithCrons) {
    Say "  Crons are OPTIONAL and OFF by default. Core store/recall needs NO token and NO crons."
    Say "  A token is therefore NOT required for this install."
    if ((Test-Path $TokDoc) -and (-not $DryRun)) {
        Say "  (FYI) token status — informational only, no crons depend on it yet:"
        Invoke-Py $PySys @($TokDoc) | Out-Null
    }
    Say "  To enable maintenance later: mint YOUR OWN token (claude setup-token), put it in $EnvFile, then re-run with -WithCrons."
} else {
    Say "  -WithCrons requested: a valid token IS required (crons call 'claude -p')."
    $needFix = $false
    if ((Test-Path $TokDoc) -and (-not $DryRun)) {
        Invoke-Py $PySys @($TokDoc) | Out-Null
        if ($LASTEXITCODE -ne 0) { $needFix = $true }
    }
    if ($needFix) {
        Warn "No valid token found. We DO NOT register crons that would silently fail."
        Warn "Run 'claude setup-token', add CLAUDE_CODE_OAUTH_TOKEN=<token> to $EnvFile, then re-run with -WithCrons."
        Say  "  Skipping cron registration (brain is fully installed and works without crons)."
    } elseif (-not $DryRun) {
        $cronInstaller = Join-Path $BrainDir "install_crons.ps1"
        if (Test-Path $cronInstaller) { & powershell -ExecutionPolicy Bypass -File $cronInstaller }
        else { Warn "no cron installer in package; maintenance crons not registered (brain still fully functional)." }
    }
}

# ---------------------------------------------------------------------------
# STEP P — THE PERSONA FORGE (mandatory, last; mirrors install.sh STEP P and
# install/install_persona_forge.sh: additive, a differing same-named item is
# moved aside to *.voidloom-bak.<ts>, an identical one is left alone)
# ---------------------------------------------------------------------------
Step "STEP G2 — Approvals choice (install.sh --approvals; the owner's ruling, 2026-10-26)"
$ApprovalsPy = Join-Path $PkgRoot "organs\approvals_policy.py"
$apAnswer = ""; $apHow = ""
if ($Approvals) { $apAnswer = $Approvals; $apHow = "flag" }
else {
    try {
        $apRec = (Invoke-PyOut $PySys @($ApprovalsPy, "show", "--json") | Out-String) | ConvertFrom-Json
        if ($apRec.recorded) { $apAnswer = $apRec.mode; $apHow = "record" }
    } catch { }
}
if (-not $apHow) {
    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        Say "Approvals. Once a month the brain reviews its own health and proposes improvements."
        Say "  a) Automatic (recommended) -- the brain approves its own monthly proposals and names what it decided"
        Say "     at the start of each session for the next 7 days. Change it any time: voidloom approvals --mode manual"
        Say "  b) Manual -- each proposal waits for you in: voidloom approvals --tui"
        Say "  (Belief links and governance holds are not affected: they always wait for a person.)"
        if ($PriorInstall) { Say "  On this brain today: no choice is recorded, so each proposal waits for you. A blank line switches it to a)." }
        do {
            $ans = (Read-Host "Your answer, a or b (a blank line is a)").Trim()
            if ($ans -match '^(|a|auto)$') { $apAnswer = "auto"; $apHow = "question" }
            elseif ($ans -match '^(b|manual)$') { $apAnswer = "manual"; $apHow = "question" }
            else { Say "  '$ans' is not a or b (a blank line is a)." }
        } while (-not $apHow)
    } elseif ($PriorInstall) { $apHow = "kept" }
    else { $apAnswer = "auto"; $apHow = "default" }
}
if ($apHow -eq "record") { Say "  approvals: $apAnswer (your recorded choice). Change it any time: voidloom approvals --mode auto|manual" }
elseif ($apHow -eq "kept") { Say "  approvals: no choice is recorded on this brain, so its monthly proposals wait for a person. To let the brain decide them: voidloom approvals --mode auto" }
elseif (-not $AcceptPass -and -not $DryRun) { Warn "  the approvals choice ($apAnswer) was NOT recorded: the self-check did not pass. Later: voidloom approvals --mode $apAnswer" }
else {
    $apBy = switch ($apHow) { "flag" { "owner-flag" } "question" { "owner" } default { "installer-default" } }
    $apArgs = @($ApprovalsPy, "set", $apAnswer, "--by", $apBy)
    if ($DryRun) { $apArgs += "--dry-run" }
    $apOut = (Invoke-PyOut $PySys $apArgs 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { Say "  approvals: $apOut" } else { Warn "  approvals: $apOut" }
}

Step "STEP P — Persona Forge (mandatory next step after a verified install)"
$PersonaForgeOk = $false
$HooksWired = "no"
function Place-Forge ($src, $dst) {
    if (-not (Test-Path $src)) { Warn "  MISSING in package: $src"; return $false }
    if (Test-Path $dst) {
        $same = $false
        if ((Get-Item $src).PSIsContainer) {
            $a = Get-ChildItem -Recurse -File $src | ForEach-Object { (Get-FileHash $_.FullName).Hash } | Sort-Object
            $b = Get-ChildItem -Recurse -File $dst | ForEach-Object { (Get-FileHash $_.FullName).Hash } | Sort-Object
            $same = (($a -join ",") -eq ($b -join ","))
        } else {
            $same = ((Get-FileHash $src).Hash -eq (Get-FileHash $dst).Hash)
        }
        if ($same) { Say "  already current: $dst"; return $true }
        $bak = "$dst.voidloom-bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Say "  EXISTS and differs — backing up: $dst -> $bak"
        if (-not $DryRun) { Move-Item $dst $bak }
    }
    if ($DryRun) { Say "  DRY: copy $src -> $dst"; return $true }
    New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
    Copy-Item -Recurse -Force $src $dst
    Say "  installed: $dst"
    return $true
}
if (-not $AcceptPass -and -not $DryRun) {
    Warn "  Persona Forge NOT laid: the acceptance check (self_check.py) did not pass. Fix that first; a re-run lays it."
} else {
    $pf1 = Place-Forge (Join-Path $PkgRoot "persona\skills\persona-forge") (Join-Path $SkillsDir "persona-forge")
    $CommandsDir = Join-Path $ClaudeHome "commands"
    $pf2 = Place-Forge (Join-Path $PkgRoot "persona\commands\persona-forge.md") (Join-Path $CommandsDir "persona-forge.md")
    $PersonaForgeOk = ($pf1 -and $pf2)
}
# The first-run offer rides the SessionStart hook: ask before touching settings.json.
# The REAL hook state first, so the banner reports what is true.
$hooksNow = $false
if ((Test-Path $HookInstaller) -and -not $DryRun) {
    try {
        $st = (Invoke-Py $PySys @($HookInstaller, "--status", "--json") | Out-String) | ConvertFrom-Json
        $hooksNow = [bool](@($st.data.platforms | Where-Object { $_.id -eq "claude-code" -and $_.installed }).Count)
    } catch { $hooksNow = $false }
}
if ($PersonaForgeOk -and $hooksNow) { $HooksWired = "yes (already wired)" }
elseif ($PersonaForgeOk -and -not $DryRun) {
    $wire = $null
    if ($WireHooks) { $wire = $true } elseif ($NoWireHooks) { $wire = $false }
    elseif ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        $ans = Read-Host "Wire the session-start hook now so your first session offers the Persona Forge? [Y/n]"
        $wire = -not ($ans -match '^\s*(n|no)\s*$')
    }
    if ($wire -eq $true -and (Test-Path $HookInstaller)) {
        Invoke-Py $PySys @($HookInstaller, "--install", "--platform", "claude", "--yes")
        $HooksWired = if ($LASTEXITCODE -eq 0) { "yes" } else { "no -- voidloom hooks install reported a problem (above)" }
    } elseif ($wire -eq $false) { $HooksWired = "no -- your answer" }
    else { $HooksWired = "no -- no console to ask and no -WireHooks" }
}

# ---------------------------------------------------------------------------
# INSTALL_RECORD -- the interpreter this install PROVED, in install.sh's format (tier1-upgrade loop, iteration 3)
# ---------------------------------------------------------------------------
# install.sh writes <brain>/INSTALL_RECORD, and every launch site reads its interpreter= (voidloom\voidloom.cmd, the
# POSIX launcher, the session hook, the cron generator). This installer wrote none, so on Windows the .cmd could only
# guess the venv. Same key=value lines as install.sh, the keys that apply here: interpreter= is the venv's python.exe
# only when Tier 1 VERIFIED, else the absolute path of the python this run used. UTF-8 WITHOUT a BOM and LF line ends:
# the record's POSIX readers -- the launcher's and the cron generator's awk -- keep a CR in the value (executed:
# `[ -f "<path>\r" ]` is false), while a batch `for /f` strips it (executed under cmd.exe: a CRLF record reads fine). LF
# keeps one byte-format for every reader, the same as install.sh's record.
function Write-InstallRecord {
    $rec = Join-Path $BrainDir "INSTALL_RECORD"
    if ($Tier1Ok -and (Test-Path $VenvPy)) { $interp = $VenvPy }
    else {
        $interp = ""
        try { $interp = ((Invoke-Py $PySys @("-c", "import sys; print(sys.executable)")) | Out-String).Trim() } catch { $interp = "" }
    }
    $ver = ""
    try {
        $m = Select-String -Path (Join-Path $BrainDir "voidloom\__init__.py") -Pattern '__version__\s*=\s*"([^"]+)"' -ErrorAction Stop | Select-Object -First 1
        if ($m) { $ver = $m.Matches[0].Groups[1].Value }
    } catch { $ver = "" }
    $lines = @(
        "# <brain>/INSTALL_RECORD -- the CURRENT install shape, written by install.ps1 (same format as install.sh's).",
        "# Rewritten on every successful wet run. Launchers read interpreter= from it.",
        "schema=1",
        ("written_at=" + (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")),
        "shape=venv",
        ("interpreter=" + $interp),
        ("package_version=" + $ver),
        ("db=" + $DbPath),
        ("embed_model=" + $(if ($Tier1Ok) { $EmbedTier } else { "" })),
        ("embed_dim=" + $(if ($Tier1Ok) { $EmbedDim } else { "" })),
        ("tier1=" + $(if ($Tier1Ok) { "active" } else { "not-completed" }))
    )
    if ($DryRun) { Say "  DRY: would write $rec (interpreter=$interp)"; return }
    $tmp = "$rec.tmp.$PID"
    [System.IO.File]::WriteAllText($tmp, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -Force -Path $tmp -Destination $rec
    Say "  install record: $rec (interpreter=$interp)"
}
Step "INSTALL RECORD"
Write-InstallRecord

# ---------------------------------------------------------------------------
# DONE
# ---------------------------------------------------------------------------
Step "INSTALL COMPLETE"
Say ("Tier 0 (always): " + $(if ($Tier0Ok) { "ACTIVE — keyword-recall brain live" } else { "NOT verified (check Step B)" }))
Say ("Tier 1 (semantic): " + $(if ($Tier1Ok) { "ACTIVE — semantic recall live" } else { "not active" + $(if ($Tier1Skip) { " ($Tier1Skip)" } else { "" }) }))
if ($Tier1Ok) {
    Say "Models (in ~/.cache/huggingface): $EmbedRepo (embedder) + $RerankerRepo (reranker, backend '$RerankerBackend' — the one recall loads) — pre-downloaded, nothing lazy-loads on first recall."
}
Write-Host ""
Say "Your Claude Code now has a memory. Confirm your brain is alive:"
Say "  $PySys `"$(Join-Path $BrainDir 'install\self_check.py')`"        # full PASS/FAIL report (exit 0 = alive)"
if ($Tier1Ok) {
    Say "  `"$VenvPy`" `"$(Join-Path $BrainDir 'organs\claude_memory.py')`" stats"
    Say "  `"$VenvPy`" `"$(Join-Path $BrainDir 'organs\claude_memory.py')`" recall --query `"how do I store memories`" --top_k 5"
} else {
    Say "  $PySys `"$Lite`" stats"
    Say "  $PySys `"$Lite`" recall --query `"how do I store memories`" --top_k 5"
}
Say "  python3 `"$(Join-Path $BrainDir 'hooks\session_start_inject.py')`"   # preview the session-start preamble"
Write-Host ""
Say "Open a NEW Claude Code session — it will wake up brain-aware. The library grows from here."
Write-Host ""
Say "=================================================================="
Say " MANDATORY NEXT STEP: open Claude Code in any folder and type /persona-forge"
Say " -- you design your persona; Claude fills in the details. Check: voidloom persona status"
Say " Session-start hook wired: $HooksWired"
if (-not $HooksWired.StartsWith("yes")) { Say " (wire it so the first session offers it by itself: voidloom hooks install --platform claude)" }
if (-not $PersonaForgeOk) { Say " (the forge files were not laid -- see STEP P above)" }
Say "=================================================================="
exit 0
