"""voidloom_persona — WHO this brain is, read from the OPERATOR'S declaration.

ONBOARDING_GUIDE.md's central promise is that *"the package ships **no** persona
... it never hands you a someone."*  This module is where that promise is kept
or broken, because every surface that could name somebody asks it first: the
session-start preamble, the hook's priming queries, and the organs that prompt a
model on the brain's behalf.

So there is NO default name and NO default blurb anywhere in this file.  An
undeclared brain resolves to ``None`` — neutral — and every caller renders that.

The declaration is a JSON file in the brain home, written by the operator, never
tracked and never packaged::

    {"default": "NAME",
     "personas": {"NAME": {"blurb": "...",
                           "doctrine_example": ["...", "..."],
                           "priming_queries": ["...", "..."],
                           "twin": {"name": "OTHER", "remote_tier": "..."}}},
     "hints": {"NAME": ["substring-of-the-brain-home", ...]}}

``hints`` lets one operator's several machines choose among their OWN declared
personas by home path.  It can only CHOOSE among declarations; it can never
manufacture one.  That distinction is the whole repair: the code this replaced
ran the same kind of host heuristic with NO NEUTRAL BRANCH, so every host that
was not the author's Windows twin became the author's primary persona by
construction — on a stranger's machine, thirty seconds after install.

stdlib only, and nothing here raises: a persona is configuration, and a brain
whose configuration is absent or broken is a brain with no persona, not a brain
that fails to start.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PERSONA_DECLARATION_BASENAME = "persona.json"
# #26 (stranger test, 2026-10-13): every user's session-start header said "(wizard-library brain)" --
# the AUTHOR'S private name for HIS brain, shipped into everyone's. The label is the product's, neutral,
# and ONE constant: the persona header (voidloom_preamble), the neutral header below and the canary's
# presence marker (voidloom_canary.PREAMBLE_MARKER) all read it, so they cannot drift apart.
BRAIN_LABEL = "Voidloom memory brain"
# The label every install before 2026-10-13 emitted -- still RECOGNISED in transcripts and files
# (voidloom_canary.LEGACY_PREAMBLE_MARKERS), never emitted.
LEGACY_BRAIN_LABELS = ("wizard-library brain",)
PERSONA_NEUTRAL_HEADER = "=== SESSION START (%s) ===" % BRAIN_LABEL


def persona_header(owner: str) -> str:
    """The first line of a DECLARED persona's session-start block."""
    return "=== %s SESSION START (%s) ===" % (owner, BRAIN_LABEL)
PERSONA_NEUTRAL_BLURB_TEMPLATE = (
    "No persona is declared for this brain, and this package ships none — the identity is "
    "yours to author. Write who you are into the brain, then declare it in {path} "
    "to have it arrive here at every session start (docs/ONBOARDING_GUIDE.md § 3.5). "
    "Everything below this line is mechanism."
)


def persona_neutral_blurb(home: str) -> str:
    """The neutral line, naming THE PATH THIS BRAIN WOULD READ.

    Derived, never typed: a reader told to edit ``<brain home>/persona.json``
    has to work out which brain home; a reader shown the resolved path can
    paste it.  It also makes VOIDLOOM_PERSONA_FILE visible when it is set.
    """
    return PERSONA_NEUTRAL_BLURB_TEMPLATE.format(path=persona_declaration_path(home))
# Words that mean "no persona", accepted from the caller or from VOIDLOOM_PERSONA.
# An operator who asks for neutrality gets neutrality; the old code ignored the
# ask, which is how a reviewer's override attempt came back with the persona.
PERSONA_REFUSALS = frozenset({"none", "neutral", "off", "no", "0", "false"})


def default_brain_home() -> str:
    """The brain home, env-first then relative-self — the same resolution the
    session hook uses, so an organ asking "who am I" and the hook asking it get
    the same answer from the same file."""
    return os.environ.get("WIZARD_LIBRARY_HOME") or str(Path(__file__).resolve().parent.parent)


def declared_name(home: Optional[str] = None,
                  explicit: Optional[str] = None) -> Optional[str]:
    """The declared persona name for an organ that only needs the NAME — e.g. to
    address a model in a prompt.  ``None`` means no persona is declared, and the
    caller must have a sentence that works without one."""
    name, _blurb, _src = resolve_persona(home or default_brain_home(), explicit)
    return name


def persona_declaration_path(home: str) -> str:
    """Where the operator's persona declaration lives.  Env-first, like every
    other path this module resolves; no username literal, no home literal."""
    explicit = os.environ.get("VOIDLOOM_PERSONA_FILE", "").strip()
    return explicit or str(Path(home) / PERSONA_DECLARATION_BASENAME)


def load_persona_declaration(home: str) -> Dict[str, Any]:
    """Read the declaration.  NEVER raises and NEVER invents.

    Anything missing, unreadable, not a regular file, or malformed yields ``{}``
    — which renders NEUTRAL.  Absence is the answer, not an error: a brain with
    no declaration has no persona, and that is the shipped default.
    """
    path = persona_declaration_path(home)
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("personas")
    if not isinstance(raw, dict):
        return {}
    personas: Dict[str, Any] = {}
    for name, body in raw.items():
        if isinstance(name, str) and name.strip() and isinstance(body, dict):
            personas[name.strip().upper()] = body
    if not personas:
        return {}
    out: Dict[str, Any] = {"personas": personas, "path": path}
    top_example = data.get("doctrine_example")
    if isinstance(top_example, str) and top_example.strip():
        out["doctrine_example"] = top_example
    dflt = data.get("default")
    if isinstance(dflt, str) and dflt.strip().upper() in personas:
        out["default"] = dflt.strip().upper()
    hints = data.get("hints")
    if isinstance(hints, dict):
        out["hints"] = {k.strip().upper(): list(v)
                        for k, v in hints.items()
                        if isinstance(k, str) and k.strip().upper() in personas
                        and isinstance(v, (list, tuple))}
    return out


def resolve_persona(home: str,
                    explicit: Optional[str] = None) -> Tuple[Optional[str], Optional[str], str]:
    """Return ``(name, blurb, source)``.

    ``(None, None, reason)`` is the NEUTRAL answer and it is a first-class
    result, never a failure — it is what an un-personalised brain is supposed
    to render.  ``source`` names WHY, so the diagnostic can say which
    declaration spoke rather than asserting a name with no provenance.

    Order: an explicit refusal · the caller's name · ``WIZARD_OWNER_NAME`` ·
    ``VOIDLOOM_PERSONA`` · a declared home-path hint · the declared default ·
    a sole declaration.  A name that is NOT DECLARED is refused by name; it is
    never fabricated, and refusing is not falling back to somebody else.
    """
    want = (explicit or "").strip()
    env_persona = os.environ.get("VOIDLOOM_PERSONA", "").strip()
    if env_persona.lower() in PERSONA_REFUSALS:
        return (None, None, "refused:VOIDLOOM_PERSONA")
    if not want:
        want = os.environ.get("WIZARD_OWNER_NAME", "").strip()
    if not want:
        want = env_persona
    if want.lower() in PERSONA_REFUSALS:
        return (None, None, "refused:requested")

    decl = load_persona_declaration(home)
    personas = decl.get("personas") or {}
    if not personas:
        return (None, None, "undeclared")

    name: Optional[str] = None
    source = ""
    asked = want.strip().upper()
    if asked:
        if asked not in personas:
            # Named a persona this brain does not declare.  Refuse it BY NAME.
            return (None, None, "undeclared:" + asked)
        name, source = asked, "requested"
    if name is None:
        h = home.replace("\\", "/").lower()
        for cand, subs in (decl.get("hints") or {}).items():
            if any(isinstance(sub, str) and sub.strip() and sub.strip().lower() in h
                   for sub in subs):
                name, source = cand, "hint"
                break
    if name is None and decl.get("default"):
        name, source = str(decl["default"]), "default"
    if name is None and len(personas) == 1:
        name, source = next(iter(personas)), "sole"
    if name is None:
        return (None, None, "ambiguous")

    blurb = personas[name].get("blurb")
    blurb = blurb.strip() if isinstance(blurb, str) and blurb.strip() else None
    return (name, blurb, source)


def persona_doctrine_example(home: str, owner: Optional[str]) -> List[str]:
    """The OPERATOR'S own hard-won worked example, as rendered lines.

    Declared as ``personas.<NAME>.doctrine_example`` (or top-level
    ``doctrine_example``), a string or a list of strings.  Rendered verbatim
    into the recall-doctrine block.  The package declares none: a worked example
    is somebody's history, and history is not a thing a package hands you.
    """
    decl = load_persona_declaration(home)
    body = (decl.get("personas") or {}).get(owner or "")
    for src in (body if isinstance(body, dict) else {}, decl):
        val = src.get("doctrine_example") if isinstance(src, dict) else None
        if isinstance(val, str) and val.strip():
            return [val.rstrip()]
        if isinstance(val, (list, tuple)):
            lines = [str(x).rstrip() for x in val if isinstance(x, str) and x.strip()]
            if lines:
                return lines
    return []


def resolve_persona_twin(home: str,
                         owner: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The cross-tier twin, taken from the DECLARATION — never a name baked in.

    Returns ``(twin_name, remote_tier)``; ``(None, None)`` means the operator
    enabled cross-tier reads without declaring a twin, and the preamble says so
    rather than naming somebody.
    """
    if not owner:
        return (None, None)
    decl = load_persona_declaration(home)
    body = (decl.get("personas") or {}).get(owner)
    twin = body.get("twin") if isinstance(body, dict) else None
    if not isinstance(twin, dict):
        return (None, None)
    tname = twin.get("name")
    tier = twin.get("remote_tier")
    return (tname.strip() if isinstance(tname, str) and tname.strip() else None,
            tier.strip() if isinstance(tier, str) and tier.strip() else None)



# ===========================================================================
# THE PERSONA FORGE — status, declare, decline (``voidloom persona ...``)
# ===========================================================================
# A fresh install has no persona, by design.  The Persona Forge is the guided
# conversation (persona/skills/persona-forge/) in which the HUMAN designs one
# and Claude fills in the small details.  This section answers the one question
# every surface asks before offering it -- "is there a persona yet?" -- and owns
# the two small writes the forge needs: the declaration (persona.json, merged,
# backed up) and the "stop reminding me" marker.
#
# The promise above is unchanged: nothing here invents a persona.  ``declare``
# writes exactly the name the operator passes, and only when asked.
#
# States (stdlib-only, never raises):
#   forged                   a persona is declared AND its skill folder has a SKILL.md
#   declared-missing-skill   a persona is declared, but its skill folder has no SKILL.md
#   declined                 nothing forged, and the operator ran `voidloom persona decline`
#   absent                   no persona is declared (the shipped default)
FORGE_STATES = ("forged", "declared-missing-skill", "declined", "absent")
FORGE_OFFER_STATES = ("absent", "declared-missing-skill")
FORGE_DECLINED_BASENAME = "persona_forge.declined"
FORGE_SKILL_NAME = "persona-forge"
# Names a persona may not take: the assistant's and vendor's, and this brain's own tool and command names.
RESERVED_NAMES = frozenset({"claude", "anthropic", "voidloom", "persona-forge", "persona", "council",
                            "memory-operator", "wizard-library"})


# Letters NFKD does not decompose into ASCII, spelled the way a reader would type them.
_TRANSLIT = {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "đ": "d", "Đ": "D",
             "ł": "l", "Ł": "L", "þ": "th", "Þ": "TH", "ð": "d", "Ð": "D", "ı": "i"}


def skill_folder_name(name: str) -> str:
    """The default skill folder for a persona name: ASCII, lowercase, dash-separated.
    ``"Iris Vale"`` -> ``"iris-vale"``, ``"Zoë"`` -> ``"zoe"``, ``"Søren Ash"`` -> ``"soren-ash"``.
    A name with no Latin letters at all (``"千代"``) yields ``""``: the human then chooses
    the folder (``voidloom persona declare --skill <folder>``) -- nothing is invented."""
    import unicodedata
    raw = "".join(_TRANSLIT.get(ch, ch) for ch in (name or ""))
    ascii_ = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    out, prev_dash = [], False
    for ch in ascii_.strip().lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash and out:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def skills_dir(home: Optional[str] = None) -> str:
    """Where Claude Code skills live for THIS brain.

    VOIDLOOM_SKILLS_DIR > WIZARD_CLAUDE_HOME/skills (the installer's own root) >
    the brain home's sibling when the brain is ``<claude home>/memory`` (the
    installed layout) > ``~/.claude/skills``.  No username literal anywhere."""
    explicit = os.environ.get("VOIDLOOM_SKILLS_DIR", "").strip()
    if explicit:
        return explicit
    ch = os.environ.get("WIZARD_CLAUDE_HOME", "").strip()
    if ch:
        return str(Path(ch) / "skills")
    h = Path(home or default_brain_home())
    if h.name == "memory":
        return str(h.parent / "skills")
    return str(Path.home() / ".claude" / "skills")


def forge_tool_paths(home: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Where the forge's own tools are, for the forge to CALL them (never typed into a procedure):
    the validator lives where install_persona_forge.sh laid the skill (<claude home>/skills/persona-forge, or
    VOIDLOOM_SKILLS_DIR when the forge was laid there), the wiring script in <brain home>/install/."""
    h = Path(home or default_brain_home())
    claude_home = Path(os.environ.get("WIZARD_CLAUDE_HOME", "").strip() or
                       (str(h.parent) if h.name == "memory" else str(Path.home() / ".claude")))
    cands = [Path(skills_dir(str(h))) / FORGE_SKILL_NAME, claude_home / "skills" / FORGE_SKILL_NAME]
    val = next((str(c / "validate_persona.py") for c in cands if (c / "validate_persona.py").is_file()), None)
    wire = wire_script_for(h, os.name)
    return {"brain_home": str(h), "forge_validator": val,
            "wire_script": str(wire) if wire is not None else None}


def wire_script_for(brain_home: Path, platform_name: str) -> Optional[Path]:
    """The wiring script THIS platform runs: wire_persona.ps1 on native Windows (os.name == "nt") when it is
    laid, wire_persona.sh everywhere else. None when the chosen script is not there."""
    inst = Path(brain_home) / "install"
    if platform_name == "nt" and (inst / "wire_persona.ps1").is_file():
        return inst / "wire_persona.ps1"
    sh = inst / "wire_persona.sh"
    return sh if sh.is_file() else None


def forge_declined_path(home: Optional[str] = None) -> str:
    return str(Path(home or default_brain_home()) / FORGE_DECLINED_BASENAME)


def _persona_skill(body: Any, name: str) -> str:
    skill = body.get("skill") if isinstance(body, dict) else None
    if isinstance(skill, str) and skill.strip() and "/" not in skill and "\\" not in skill \
            and ".." not in skill:
        return skill.strip()
    return skill_folder_name(name)


def _display(body: Any, key: str) -> str:
    dn = body.get("display_name") if isinstance(body, dict) else None
    return dn.strip() if isinstance(dn, str) and dn.strip() else key


def forge_status(home: Optional[str] = None) -> Dict[str, Any]:
    """Is there a persona yet?  Returns a dict; NEVER raises.

    The declared persona is the declaration's ``default``, else its sole entry.
    ``forged`` needs BOTH the declaration and ``<skills>/<skill>/SKILL.md`` --
    a name in persona.json with no skill behind it is a persona in name only."""
    out: Dict[str, Any] = {"status": "absent", "persona": None, "display_name": None, "skill": None,
                           "skill_md": None, "declaration": None,
                           "declined_marker": None, "skills_dir": None}
    try:
        h = home or default_brain_home()
        sdir = skills_dir(h)
        out["skills_dir"] = sdir
        out.update(forge_tool_paths(h))
        out["declaration"] = persona_declaration_path(h)
        decl = load_persona_declaration(h)
        personas = decl.get("personas") or {}
        name = decl.get("default") or (next(iter(personas)) if len(personas) == 1 else None)
        candidates = [name] if name else list(personas)
        for cand in candidates:
            skill = _persona_skill(personas.get(cand), cand)
            md = Path(sdir) / skill / "SKILL.md"
            if skill and md.is_file():
                out.update(status="forged", persona=cand, skill=skill, skill_md=str(md),
                           display_name=_display(personas.get(cand), cand))
                return out
        marker = forge_declined_path(h)
        if os.path.isfile(marker):
            out.update(status="declined", declined_marker=marker)
            if name:
                out["persona"] = name
            return out
        if name:
            skill = _persona_skill(personas.get(name), name)
            out.update(status="declared-missing-skill", persona=name, skill=skill,
                       display_name=_display(personas.get(name), name),
                       skill_md=(str(Path(sdir) / skill / "SKILL.md") if skill else None))
        elif personas:
            out.update(status="declared-missing-skill", reason="ambiguous: several personas "
                       "declared, no default, none with a SKILL.md")
        return out
    except Exception as e:  # configuration, never a failure
        out["error"] = f"{type(e).__name__}: {e}"
        return out


def forge_offer_text(st: Optional[Dict[str, Any]] = None,
                     home: Optional[str] = None) -> Optional[str]:
    """The short, NEUTRAL session-start block -- or ``None`` when there is
    nothing to offer (forged, declined).  It names nobody: the package ships no
    persona, and this line only says that one could be made."""
    try:
        st = st if st is not None else forge_status(home)
        status = st.get("status")
        if status not in FORGE_OFFER_STATES:
            return None
        if status == "absent":
            first = "PERSONA FORGE — this brain has no persona yet (voidloom persona status: absent)."
        else:
            first = ("PERSONA FORGE — a persona is declared but its skill folder has no SKILL.md "
                     "(voidloom persona status: declared-missing-skill).")
        return "\n".join([
            "",
            first,
            "  Before other work, offer the human the Persona Forge: a short guided conversation in which",
            "  THEY design the working persona you will be with them (type /persona-forge, or load the",
            "  persona-forge skill). It is theirs to shape; nothing is inherited.",
            "  If they say \"not now\", skip it for this session only. `voidloom persona decline` stops this",
            "  reminder (`voidloom persona decline --undo` brings it back).",
            "  Only in an interactive session with the human present; in headless, scheduled or subagent",
            "  runs, ignore this.",
        ])
    except Exception:
        return None


def _write_json_atomic(path: str, data: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("." + p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(str(tmp), str(p))


def declare_persona(home: Optional[str], name: str, skill: Optional[str] = None,
                    blurb: Optional[str] = None, make_default: bool = True) -> Dict[str, Any]:
    """MERGE one persona into the operator's persona.json (backing up the old
    file beside it as ``persona.json.bak-<utc>``).  Other personas, hints and
    keys are kept.  Returns a result dict; never raises."""
    import datetime as _dt
    h = home or default_brain_home()
    path = persona_declaration_path(h)
    nm = " ".join((name or "").split())
    if not nm or not any(ch.isalnum() for ch in nm):
        return {"ok": False, "reason": "a persona name needs at least one letter or digit"}
    key = nm.upper()
    sk = (skill or "").strip() or skill_folder_name(nm)
    if not sk:
        return {"ok": False, "reason": (f"{nm!r} has no letters an ASCII folder name can carry; choose the folder "
                                        "yourself: --skill <lowercase-dash-name>")}
    if "/" in sk or "\\" in sk or ".." in sk or sk != skill_folder_name(sk):
        return {"ok": False, "reason": f"skill folder must be lowercase-dash ASCII (got {sk!r})"}
    for cand in (skill_folder_name(nm), sk):
        if cand in RESERVED_NAMES or cand.startswith("council-"):
            return {"ok": False, "reason": (f"{cand!r} is reserved (the assistant's, the vendor's or this brain's "
                                            "own tool/command name); choose another name or --skill folder")}
    raw: Dict[str, Any] = {}
    backup = None
    try:
        if os.path.lexists(path):
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                return {"ok": False, "reason": f"{path} exists and is not a regular file; refusing"}
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                return {"ok": False, "reason": f"{path} is not a JSON object; refusing to overwrite it"}
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = f"{path}.bak-{stamp}"
            with open(path, "rb") as src, open(backup, "wb") as dst:
                dst.write(src.read())
    except Exception as e:
        return {"ok": False, "reason": f"cannot read {path}: {type(e).__name__}: {e}"}
    personas = raw.get("personas") if isinstance(raw.get("personas"), dict) else {}
    # one key per persona, case-insensitively (the loader upper-cases names)
    existing = next((k for k in personas if isinstance(k, str) and k.strip().upper() == key), None)
    body = dict(personas.pop(existing)) if existing else {}
    body["skill"] = sk
    body["display_name"] = nm          # the name as the human wrote it ("Zoë", not "ZOË")
    if blurb and blurb.strip():
        body["blurb"] = blurb.strip()
    elif not body.get("blurb"):
        body["blurb"] = (f"You are {nm}. Load ~/.claude/skills/{sk}/SKILL.md for the full identity "
                         "(foundation, voice, operations, recall map, growth log).")
    personas[key] = body
    raw["personas"] = personas
    if make_default:
        raw["default"] = key
    try:
        _write_json_atomic(path, raw)
    except Exception as e:
        return {"ok": False, "reason": f"cannot write {path}: {type(e).__name__}: {e}",
                "backup": backup}
    return {"ok": True, "declaration": path, "persona": key, "display_name": nm, "skill": sk,
            "default": raw.get("default"), "backup": backup}


def decline_forge(home: Optional[str] = None, undo: bool = False) -> Dict[str, Any]:
    """Write (or with ``undo`` remove) the "stop reminding me" marker.  Never raises."""
    import datetime as _dt
    marker = forge_declined_path(home)
    try:
        if undo:
            existed = os.path.isfile(marker)
            if existed:
                os.remove(marker)
            return {"ok": True, "marker": marker, "removed": existed}
        _write_json_atomic(marker, {
            "declined_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "undo": "voidloom persona decline --undo",
            "why": "the operator asked the session-start reminder to stop offering the Persona Forge"})
        return {"ok": True, "marker": marker, "written": True}
    except Exception as e:
        return {"ok": False, "marker": marker, "reason": f"{type(e).__name__}: {e}"}


_PERSONA_USAGE = ("usage: voidloom persona status [--json] | forge | "
                  "declare --name NAME [--skill FOLDER] [--blurb TEXT] [--no-default] | "
                  "decline [--undo]")


def _penv(status: str, code: str, data: Any, human: Optional[str] = None,
          rc: Optional[int] = None) -> Dict[str, Any]:
    env: Dict[str, Any] = {"status": status, "code": code, "data": data, "verb": "persona"}
    if human is not None:
        env["_human"] = human
    if rc is not None:
        env["_exit_code"] = rc
    return env


def _status_human(st: Dict[str, Any]) -> str:
    s = st.get("status")
    lines = [f"persona: {s}"]
    if s == "forged":
        lines.append(f"  {st.get('display_name') or st.get('persona')} -> {st.get('skill_md')}")
    elif s == "declared-missing-skill":
        lines.append(f"  declared in {st.get('declaration')}, but no SKILL.md at "
                     f"{st.get('skill_md') or st.get('skills_dir')}")
        lines.append("  re-run the Persona Forge (/persona-forge) to restore it")
    elif s == "declined":
        lines.append(f"  the reminder is off ({st.get('declined_marker')}); "
                     "`voidloom persona decline --undo` turns it back on")
        lines.append("  /persona-forge still works whenever you want it")
    else:
        lines.append("  no persona yet -- open Claude Code and type /persona-forge "
                     "(the first session offers it too)")
    return "\n".join(lines)


def run(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    """``voidloom persona`` -- the organ verb.  Returns the house envelope."""
    import argparse
    args = list(argv or [])
    p = argparse.ArgumentParser(prog="voidloom persona", add_help=False)
    p.add_argument("action", nargs="?", default="status")
    p.add_argument("--json", action="store_true")
    p.add_argument("--home", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--skill", default=None)
    p.add_argument("--blurb", default=None)
    p.add_argument("--no-default", action="store_true")
    p.add_argument("--undo", action="store_true")
    p.add_argument("-h", "--help", action="store_true")
    try:
        a, extra = p.parse_known_args(args)
    except SystemExit:
        return _penv("error", "usage", {"message": _PERSONA_USAGE}, _PERSONA_USAGE, 2)
    if a.help:
        return _penv("ok", "persona_help", {"usage": _PERSONA_USAGE}, _PERSONA_USAGE)
    if extra:
        return _penv("error", "usage", {"message": "unrecognised: " + " ".join(extra),
                                        "usage": _PERSONA_USAGE},
                     "unrecognised: " + " ".join(extra) + "\n" + _PERSONA_USAGE, 2)
    home = a.home or default_brain_home()
    if a.action == "status":
        st = forge_status(home)
        return _penv("ok", "persona_status", st, _status_human(st))
    if a.action == "forge":
        st = forge_status(home)
        msg = (_status_human(st) + "\n\nThe Persona Forge is a conversation, so it runs inside Claude Code:\n"
               "  open Claude Code in any folder and type /persona-forge\n"
               "  (it asks a few multiple-choice questions, drafts the persona, and writes nothing "
               "until you approve it)")
        return _penv("ok", "persona_forge", dict(st, how="/persona-forge in Claude Code"), msg)
    if a.action == "declare":
        if not a.name:
            return _penv("error", "usage", {"message": "declare needs --name NAME",
                                            "usage": _PERSONA_USAGE},
                         "declare needs --name NAME\n" + _PERSONA_USAGE, 2)
        r = declare_persona(home, a.name, a.skill, a.blurb, make_default=not a.no_default)
        st = forge_status(home)
        r["status_after"] = st.get("status")
        if not r.get("ok"):
            return _penv("refused", "persona_declare_refused", r,
                         "declare refused: " + str(r.get("reason")), 3)
        human = (f"declared {r['display_name']} (skill folder: {r['skill']}) in {r['declaration']}"
                 + (f"\n  previous file kept at {r['backup']}" if r.get("backup") else "")
                 + "\n" + _status_human(st))
        return _penv("ok", "persona_declared", r, human)
    if a.action == "decline":
        r = decline_forge(home, undo=a.undo)
        if not r.get("ok"):
            return _penv("error", "persona_decline_failed", r,
                         "could not update " + str(r.get("marker")) + ": " + str(r.get("reason")), 1)
        if a.undo:
            human = ("the Persona Forge reminder is back on" if r.get("removed")
                     else "the reminder was already on (no marker to remove)")
        else:
            human = ("the Persona Forge reminder is off. /persona-forge still works any time; "
                     "`voidloom persona decline --undo` turns the reminder back on.")
        r["status_after"] = forge_status(home).get("status")
        return _penv("ok", "persona_decline", r, human)
    return _penv("error", "usage", {"message": f"unknown action {a.action!r}",
                                    "usage": _PERSONA_USAGE},
                 f"unknown action {a.action!r}\n{_PERSONA_USAGE}", 2)


def main(argv: Optional[List[str]] = None) -> int:
    import sys as _sys
    args_l = list(argv) if argv is not None else _sys.argv[1:]
    env = run(args_l)
    rc = int(env.pop("_exit_code", {"ok": 0, "refused": 3}.get(env.get("status"), 1)))
    human = env.pop("_human", None)
    if "--json" in args_l or human is None:
        print(json.dumps(env, ensure_ascii=False, indent=2))
    else:
        print(human)
    return rc


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
