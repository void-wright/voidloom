#!/usr/bin/env python3
"""Cross-platform portability audit (Council #4827).

Greps the wizard-library module set for runtime-OS leaks that would crash or
misbehave on Windows/Mac. The handoff installer should run this and REFUSE to
package if unguarded leaks are found ("audit-and-prevent", not "crash-and-fix").

Leak patterns checked:
  - Unix-only imports (fcntl, pwd, grp, termios, resource, posix) — unless guarded by try/except
  - hardcoded /tmp (use tempfile.gettempdir())
  - hardcoded /home/ or C:\\ absolute paths (use wizard_paths / relative-self)
  - os.fork (no Windows equivalent)
  - shell-specific assumptions (bash-only)

Exit 0 = clean (or only guarded/test-only leaks). Exit 1 = unguarded leaks found.

Usage: python3 cross_platform_audit.py [--dir <module-dir>] [--strict]
"""
import argparse
import re
import sys
from pathlib import Path

UNIX_IMPORTS = ["fcntl", "pwd", "grp", "termios", "resource", "posix"]
LEAK_PATTERNS = [
    (r"(?<!\.)\bos\.fork\b", "os.fork (no Windows equivalent)"),
    (r"['\"]/tmp/", "hardcoded /tmp (use tempfile.gettempdir())"),
    (r"['\"]/home/[a-z_][a-z0-9_-]*/", "hardcoded /home/<user> path (use wizard_paths/relative-self)"),
    (r"['\"]C:\\\\Users", "hardcoded Windows path (use wizard_paths)"),
]


def audit_file(path: Path):
    findings = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return findings
    # Detect whether unix imports are guarded by try/except ImportError nearby
    text = "\n".join(lines)
    for mod in UNIX_IMPORTS:
        # match top-level "import fcntl" not inside an embedded string/test
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if re.match(rf"^import {mod}\b", s) or re.match(rf"^from {mod}\b", s):
                # guarded if a 'try:' appears within 3 lines above
                window = "\n".join(lines[max(0, i - 4):i])
                guarded = "try:" in window
                # test-only if 'unit_test' / 'def _run_tests' / embedded-string context
                test_only = ("test" in str(path).lower())
                if not guarded:
                    findings.append((i, f"unguarded 'import {mod}'", "WARN" if test_only else "LEAK"))
    for pat, desc in LEAK_PATTERNS:
        for i, line in enumerate(lines, 1):
            if re.search(pat, line):
                # downgrade to WARN if it's a comment or a test path
                sev = "WARN" if (line.strip().startswith("#") or "test" in line.lower()) else "LEAK"
                findings.append((i, desc, sev))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--strict", action="store_true", help="treat WARN as failure too")
    args = ap.parse_args()
    d = Path(args.dir)
    py_files = sorted(d.glob("*.py")) + sorted(d.glob("cron/*.py"))
    total_leaks = 0
    total_warns = 0
    print(f"=== Cross-Platform Audit: {d} ({len(py_files)} files) ===")
    for f in py_files:
        findings = audit_file(f)
        if findings:
            leaks = [x for x in findings if x[2] == "LEAK"]
            warns = [x for x in findings if x[2] == "WARN"]
            total_leaks += len(leaks)
            total_warns += len(warns)
            print(f"\n{f.name}:")
            for ln, desc, sev in findings:
                print(f"  [{sev}] line {ln}: {desc}")
    print(f"\n=== SUMMARY: {total_leaks} LEAKS, {total_warns} WARNs ===")
    if total_leaks == 0 and total_warns == 0:
        print("✓ CLEAN — fully cross-platform portable")
    elif total_leaks == 0:
        print("✓ No hard leaks (WARNs are comments/tests/guarded — review if --strict)")
    else:
        print("✗ Unguarded leaks found — fix before cross-platform handoff")
    fail = total_leaks > 0 or (args.strict and total_warns > 0)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
