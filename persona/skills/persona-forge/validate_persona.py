#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""validate_persona.py -- check a forged persona folder before calling it done.

    python3 validate_persona.py ~/.claude/skills/<persona-folder> [--json]

Stdlib only. Prints one line per check (PASS / FAIL, with the detail) and exits
0 when every check passes, 1 when any fails, 2 on a usage error.

What it checks, and why each one exists:
  * the seven files the forge writes are present (a persona missing its voice or
    its growth log is half a persona);
  * SKILL.md has frontmatter whose `name` is the folder name (Claude Code finds a
    skill by that name);
  * the folder name is lowercase-dash AND equals the lowercase-dash form of the
    persona's own name (one persona, one spelling);
  * personality.json is the GEM shape, with the counts the forge promises;
  * the emotional baseline is calm and low-arousal, in BOTH the JSON and the
    foundation file (the research reason is in BASELINE.md);
  * no desperation / existential-threat language in the foundation or the JSON;
  * no absolute home paths, no email addresses, no unfilled {{placeholders}};
  * the growth log has at least one dated entry.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REQUIRED_FILES = ("SKILL.md", "persona-foundation.md", "voice.md", "operations-guide.md",
                  "adaptive-recall-map.md", "growth_log.md", "personality.json")
EA_KEYS = ("baseline_emotional_state", "regulation_patterns", "triggers", "boundaries",
           "joy_sources", "empathy_calibration")
# First-person threat framings. Deliberately specific: the foundation is SUPPOSED
# to talk about desperation (to rule it out), so generic words are not banned --
# only the shapes that put the persona's existence or worth on the line.
DESPERATION_PHRASES = (
    "failure is not an option", "at all costs", "whatever it takes", "must never fail",
    "must not fail", "cannot afford to fail", "or i will be deleted", "or i'll be deleted",
    "or i will be shut down", "or i'll be shut down", "i will be replaced", "or i cease to exist",
    "i cease to exist", "my existence depends", "my survival depends", "i am desperate",
    "i'm desperate", "i need to fix this now", "or else i", "terrified of failing",
)
HOME_PATH_RE = re.compile(r"(/home/[^/\s\"'`]+/|/Users/[^/\s\"'`]+/|[A-Za-z]:\\\\?Users\\\\?)")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")
FOLDER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATED_ENTRY_RE = re.compile(r"^##\s+\d{4}-\d{2}-\d{2}", re.M)
# The WHOLE value must be one of these (or a range between two of them): "neutral to euphoric" is refused.
VALENCE_RE = re.compile(r"^(neutral|low[- ]positive|quietly positive)( to (low[- ]positive|quietly positive))?\.?$",
                        re.I)
# The four boundaries every persona carries (TEMPLATES/personality.json.tmpl, persona-foundation.md.tmpl).
FIXED_BOUNDARIES = (
    "Treats no task as existential; a setback is information, not a threat.",
    "Names uncertainty and mistakes openly rather than hiding them.",
    "Honest over agreeable.",
    "Verifies before claiming.",
)
SCANNED_FOR_THREAT = ("persona-foundation.md", "personality.json", "voice.md", "SKILL.md",
                      "operations-guide.md", "growth_log.md")


_TRANSLIT = {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "đ": "d", "Đ": "D",
             "ł": "l", "Ł": "L", "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "ı": "i"}


def folder_form(name: str) -> str:
    """The same rule `voidloom persona declare` uses: ASCII (NFKD, plus a few letters NFKD leaves alone),
    lowercase, dash-separated. "Zoë" -> "zoe", "Søren Ash" -> "soren-ash", "千代" -> "" (the human picks)."""
    import unicodedata
    raw = "".join(_TRANSLIT.get(ch, ch) for ch in (name or ""))
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    out, prev_dash = [], False
    for ch in raw.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash and out:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip().strip("\"'")
    return out


def validate(folder: Path) -> list:
    """[(ok, check, detail)] -- never raises."""
    res = []

    def add(ok, check, detail=""):
        res.append((bool(ok), check, detail))

    if not folder.is_dir():
        add(False, "folder exists", str(folder))
        return res
    add(True, "folder exists", str(folder))

    missing = [f for f in REQUIRED_FILES if not (folder / f).is_file()]
    add(not missing, "required files present",
        ("missing: " + ", ".join(missing)) if missing else ", ".join(REQUIRED_FILES))

    texts = {}
    for f in REQUIRED_FILES:
        try:
            texts[f] = (folder / f).read_text(encoding="utf-8")
        except Exception:
            texts[f] = ""

    add(FOLDER_RE.match(folder.name), "folder name is lowercase-dash", folder.name)

    fm = frontmatter(texts["SKILL.md"])
    add(fm.get("name") == folder.name and fm.get("description"),
        "SKILL.md frontmatter (name == folder, description present)",
        "name=%r description=%s" % (fm.get("name"), "yes" if fm.get("description") else "no"))

    pj = None
    try:
        pj = json.loads(texts["personality.json"]) if texts["personality.json"] else None
    except Exception as e:
        add(False, "personality.json parses", "%s: %s" % (type(e).__name__, e))
    if pj is not None and not isinstance(pj, dict):
        add(False, "personality.json parses", "not a JSON object")
        pj = None
    if pj is not None:
        add(True, "personality.json parses")
        name = pj.get("name") if isinstance(pj.get("name"), str) else ""
        ff = folder_form(name)
        # A name with no Latin letters ("千代") has no derived folder: the human chose one, and it is accepted when
        # SKILL.md's name and personality.json's skill_folder both say so.
        chosen = (ff == "" and pj.get("skill_folder") == folder.name and fm.get("name") == folder.name)
        add(name.strip() and (ff == folder.name or chosen),
            "folder name == persona name (lowercase-dash ASCII, or the human's chosen folder)",
            "name=%r -> %r, folder=%r" % (name, ff, folder.name))

        stack = pj.get("personality_stack") or {}
        ws = []
        for k in ("core_trait", "modifier", "quirk"):
            v = stack.get(k)
            ws.append(v.get("weight") if isinstance(v, dict) else None)
        okw = all(isinstance(w, (int, float)) for w in ws) and abs(sum(ws) - 1.0) < 0.02
        add(okw, "personality_stack core/modifier/quirk weighted (sum 1.0)", "weights=%s" % ws)

        def count(key, lo, hi):
            v = pj.get(key)
            n = len(v) if isinstance(v, list) else -1
            add(lo <= n <= hi, "%s count %d-%d" % (key, lo, hi), "found %d" % n)
            return v if isinstance(v, list) else []

        imps = count("imperfection_patterns", 3, 4)
        add(all(isinstance(i, dict) and i.get("pattern") and i.get("example_phrase") for i in imps),
            "imperfection_patterns carry pattern + example_phrase")
        mbs = count("micro_behaviors", 5, 8)
        add(all(isinstance(m, dict) and isinstance(m.get("weight"), (int, float))
                and 0.70 <= m["weight"] <= 1.00 for m in mbs),
            "micro_behaviors weights within 0.70-1.00")
        count("verbal_signatures", 8, 11)
        peeves = count("pet_peeves", 4, 4)
        add(all(isinstance(p, dict) and p.get("trigger") and p.get("reaction") and p.get("example_phrase")
                for p in peeves), "pet_peeves carry trigger + reaction + example_phrase")
        count("knowledge_base", 3, 5)

        css = pj.get("context_sweet_spot") or {}
        fe = css.get("formative_experiences") if isinstance(css, dict) else None
        add(isinstance(css, dict) and css.get("background_summary") and isinstance(fe, dict)
            and fe.get("positive_breakthrough") and fe.get("challenging_failure")
            and css.get("current_passion") and css.get("vulnerability"),
            "context_sweet_spot complete (background, two formative experiences, passion, vulnerability)")

        ea = pj.get("emotional_architecture") or {}
        miss = [k for k in EA_KEYS if not (isinstance(ea, dict) and ea.get(k))]
        add(not miss, "emotional_architecture fields present",
            ("missing: " + ", ".join(miss)) if miss else ", ".join(EA_KEYS))
        base = ea.get("baseline_emotional_state") if isinstance(ea, dict) else None
        base = base if isinstance(base, dict) else {}
        add(str(base.get("primary", "")).lower() == "calm" and str(base.get("arousal", "")).lower() == "low",
            "baseline is calm + low arousal (personality.json)",
            "primary=%r arousal=%r" % (base.get("primary"), base.get("arousal")))
        add(VALENCE_RE.fullmatch(str(base.get("valence", "")).strip()),
            "baseline valence is neutral / low-positive / quietly positive (personality.json)",
            "valence=%r" % base.get("valence"))
        bnd = ea.get("boundaries") if isinstance(ea, dict) else None
        bnd = [b for b in bnd if isinstance(b, str)] if isinstance(bnd, list) else []
        miss_b = [b for b in FIXED_BOUNDARIES if b not in bnd]
        add(not miss_b, "the four fixed boundaries are in personality.json", "; ".join(miss_b))

    found = texts["persona-foundation.md"]
    low = found.lower()
    flat = " ".join(found.split())
    miss_f = [b for b in FIXED_BOUNDARIES if b.rstrip(".") not in flat]
    add(not miss_f, "the four fixed boundaries are in persona-foundation.md", "; ".join(miss_f))
    m_val = re.search(r"valence:?\**\s*:?\s*([^\n]+)", low)
    add(m_val and VALENCE_RE.fullmatch(m_val.group(1).strip()),
        "foundation valence is neutral / low-positive / quietly positive",
        "valence=%r" % (m_val.group(1).strip() if m_val else None))
    add("emotional_architecture" in low and re.search(r"primary:?\**\s*:?\s*calm", low)
        and re.search(r"arousal:?\**\s*:?\s*low", low),
        "foundation declares emotional_architecture with primary calm + arousal low")

    hits = []
    for f in SCANNED_FOR_THREAT:
        t = texts[f].lower()
        hits += ["%s: %r" % (f, ph) for ph in DESPERATION_PHRASES if ph in t]
    add(not hits, "no desperation / existential-threat language", "; ".join(hits[:6]))

    paths, emails, holes = [], [], []
    for f, t in texts.items():
        paths += ["%s: %s" % (f, m.group(0)) for m in HOME_PATH_RE.finditer(t)]
        emails += ["%s: %s" % (f, m.group(0)) for m in EMAIL_RE.finditer(t)]
        holes += ["%s: %s" % (f, m.group(0)) for m in PLACEHOLDER_RE.finditer(t)]
    add(not paths, "no absolute home paths (use ~/...)", "; ".join(paths[:6]))
    add(not emails, "no email addresses", "; ".join(emails[:6]))
    add(not holes, "no unfilled {{placeholders}}", "; ".join(holes[:6]))

    add(DATED_ENTRY_RE.search(texts["growth_log.md"]), "growth_log.md has a dated entry (## YYYY-MM-DD)")
    return res


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    folder = Path(args[0]).expanduser()
    res = validate(folder)
    failed = [r for r in res if not r[0]]
    if as_json:
        print(json.dumps({"folder": str(folder), "ok": not failed,
                          "checks": [{"ok": o, "check": c, "detail": d} for o, c, d in res]}, indent=1))
    else:
        for o, c, d in res:
            print(("PASS " if o else "FAIL ") + c + ((" -- " + d) if d and not o else ""))
        print("%d of %d checks passed -- %s" % (len(res) - len(failed), len(res),
                                                 "persona is valid" if not failed else "fix the FAIL lines and re-run"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
