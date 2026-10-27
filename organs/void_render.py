#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""void_render.py — the single source of truth for the Voidloom Sparse-Void aesthetic.

Voidloom is the agnostic public identity of the wizard-library memory system:
"a living memory tapestry woven for AI agents." This module renders that
identity into a terminal. The terminal IS the void, so a dark void-indigo
ground is the on-brand extension of the brand surface.

DESIGN PRINCIPLES (brand voice — calm, restrained):
  - "won't trade durability for spectacle — one arcane touch, calm."
  - RESTRAINT is mandatory. A few scattered stars, one sigil, one wordmark.
    Never a wall of glyphs. This aligns with the persona's calm baseline
    (Anthropic April 2026 research: the "calm" vector reduces corner-cutting;
    the "desperate" vector drives spectacle/over-reach).
  - The sigil is a single composed constant. It is ALWAYS violet+gold.
    It is never recolored away from the brand and never decomposed.

DEPENDENCIES: Python standard library ONLY. No torch, no embedding model,
no network. This module is safe to import on the session-start hot path
(<2s budget) and from a cron. (Cross-pollinated from Loop E2 / C002:
a renderer is CPU-only by nature; it must stay stdlib so it never drags
the GPU model onto a hot path.)

COLOR TIERS (auto-detected, override-able):
  1. truecolor  — 24-bit ANSI (exact hex). Used when COLORTERM=truecolor/24bit.
  2. ansi256    — 256-color ANSI (nearest palette match). Default for color TTYs.
  3. none       — plain text, no escapes. Used when NO_COLOR is set, TERM=dumb,
                  or stdout is not a TTY (piped/redirected/injected).

PUBLIC API (the parts other code should use):
  PALETTE                       — dict of brand name -> Color
  SIGIL                         — the composed sigil glyph "◇◆✦"
  WORDMARK, TAGLINE             — brand strings
  Renderer(stream=None, force_mode=None)
      .paint(text, color, *, bold=False, dim=False) -> str
      .sigil() -> str           — the sigil, always violet/gold
      .wordmark() -> str        — "V O I D L O O M" in starlight gold, bold
      .tagline() -> str         — the tagline in dim slate
      .star(glyph) -> str       — a single dim star
  sparse_starfield(width, *, density=..., seed=None, renderer=None) -> str
  strip_ansi(text) -> str       — remove all SGR escapes (for width math / tests)
  visible_len(text) -> int      — display width ignoring escapes

This module deliberately exposes NO persona text and NO file paths — it is the
agnostic Voidloom-system surface. Persona/preamble lines live elsewhere and stay
strippable for the public/teammate repo.
"""
from __future__ import annotations

import os
import random
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, TextIO

__all__ = [
    "Color",
    "PALETTE",
    "SIGIL",
    "WORDMARK",
    "TAGLINE",
    "STAR_GLYPHS",
    "Renderer",
    "sparse_starfield",
    "lockup",
    "render_explanation",
    "strip_ansi",
    "sanitize_for_terminal",
    "visible_len",
    "detect_mode",
]

# ---------------------------------------------------------------------------
# Palette — the Voidloom Sparse-Void brand colors (exact hex).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Color:
    """A brand color with its exact RGB and a precomputed nearest xterm-256 index."""
    name: str
    r: int
    g: int
    b: int

    @property
    def hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @property
    def xterm256(self) -> int:
        return _rgb_to_xterm256(self.r, self.g, self.b)


def _from_hex(name: str, hexstr: str) -> Color:
    h = hexstr.lstrip("#")
    return Color(name, int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# The four-color Sparse-Void palette. These hex values are the brand contract.
VOID_INDIGO = _from_hex("void_indigo", "#211C44")   # canvas / ground
ARCANE_VIOLET = _from_hex("arcane_violet", "#6E56CF")  # the sigil
STARLIGHT_GOLD = _from_hex("starlight_gold", "#C8A24A")  # accent / wordmark / text
# SECONDARY TEXT. Was #5B5F73, a dark grey-blue: contrast 2.60:1 against a typical
# dark terminal ground -- below WCAG's 3:1 floor for LARGE text before anything
# else happens -- and every caller then ALSO applied the SGR-2 dim attribute on
# top, which terminals render at their own discretion and some render hard. Two
# dimmings compounding is why the detail lines were unreadable on Drew's setup.
# Now a light lavender drawn from the wordmark's own gradient (lockup g4 family),
# so the hierarchy is carried by HUE AND LIGHTNESS rather than by a terminal
# attribute whose rendering we cannot predict. 6.53:1.
SLATE = _from_hex("slate", "#A99BD8")               # secondary text (readable)


#: THE COMMAND WE TELL A HUMAN TO RUN. One string, for the same reason
#: REFUSAL_TEXT is one string: it was typed at SEVEN sites across four modules,
#: and seven copies of an instruction drift into seven instructions.
#:
#: `--tui` is the point. Bare `voidloom approvals` prints the machine record and
#: cannot approve anything, so pointing a human at it hands them a wall of JSON
#: and no way to act -- which is precisely what Drew reported. The flag is the
#: difference between "here is the queue" and "here is where you decide".
APPROVALS_CMD = "voidloom approvals --tui"
# Decorative field stars only. These are MEANT to recede, they carry no words,
# and they are the one place the old darkness was correct.
STARDUST = _from_hex("stardust", "#6E6A8A")         # dim field stars

PALETTE = {
    "void_indigo": VOID_INDIGO,
    "arcane_violet": ARCANE_VIOLET,
    "starlight_gold": STARLIGHT_GOLD,
    "slate": SLATE,
    "stardust": STARDUST,
}

# ---------------------------------------------------------------------------
# Brand marks — the one arcane touch. Restraint by construction.
# ---------------------------------------------------------------------------

# The composed sigil. Three glyphs: hollow diamond, filled diamond, four-point
# star. Always rendered violet->violet->gold (see Renderer.sigil). It is a
# single constant so it can never be decomposed or recolored piecemeal.
SIGIL = "◇◆✦"  # ◇◆✦

WORDMARK = "VOIDLOOM"
WORDMARK_SPACED = "V O I D L O O M"
TAGLINE = "a living memory tapestry woven for AI agents"

# A SMALL set of star glyphs. The starfield scatters a FEW of these — never many.
STAR_GLYPHS = ["·", "⋆", "✦", "✧"]  # · ⋆ ✦ ✧
# Glyphs used in banner status lines (kept here so callers share one vocabulary).
GLYPH_PENNANT = "⚑"   # ⚑  proposals await
GLYPH_STAR_FILLED = "✦"  # ✦  anticipated
GLYPH_LOZENGE = "◈"   # ◈  forgotten foundation
GLYPH_FADING_STAR = "·"  # ·  the single fading star on sign-off

# ---------------------------------------------------------------------------
# ANSI plumbing
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove all SGR color escapes. Used for width math and for tests."""
    return _ANSI_RE.sub("", text)


def visible_len(text: str) -> int:
    """Display length ignoring color escapes. (Assumes 1 cell per code point;
    the brand glyphs are all single-width, so this is accurate for our use.)"""
    return len(strip_ansi(text))


# --- Terminal-control sanitizer (HARD prerequisite for a safe systemMessage) --
# A banner carrying memory-derived strings reaches the terminal via the
# SessionStart `systemMessage` channel. Untrusted memory text could embed
# terminal control sequences — OSC 52 (clipboard write/hijack), OSC 8
# (hyperlink spoof), CSI cursor/clear injection, etc. `strip_ansi` only removes
# SGR *color* escapes; it is NOT a security boundary. `sanitize_for_terminal`
# is: it neutralizes ALL terminal control sequences and raw control chars while
# preserving \t \n \r and printable UTF-8, so memory text renders as inert text.
# (This is the focused #6099 prerequisite, distinct from the broader deferred
# #176 content audit.) Apply it to every memory-derived string before it enters
# the banner / systemMessage. Static brand art has no memory content and needs
# no sanitizing; the DYNAMIC status lines do.

# Order is significant: payload-bearing string sequences (OSC, DCS/SOS/PM/APC)
# are removed before generic CSI / lone-ESC handling so their payloads can never
# be partially left behind. Both 7-bit (ESC-introduced) and 8-bit C1 forms are
# covered. Terminators: BEL (\x07), ST (ESC \\  or 8-bit \x9c).
_OSC_RE = re.compile(r"(?:\033\]|\x9d).*?(?:\007|\033\\|\x9c)", re.DOTALL)
_OSC_UNTERM_RE = re.compile(r"(?:\033\]|\x9d).*\Z", re.DOTALL)
_STRSEQ_RE = re.compile(r"\033[PX^_].*?(?:\033\\|\x9c)", re.DOTALL)
_STRSEQ_UNTERM_RE = re.compile(r"\033[PX^_].*\Z", re.DOTALL)
_CSI_RE = re.compile(r"(?:\033\[|\x9b)[0-?]*[ -/]*[@-~]")
_CSI_UNTERM_RE = re.compile(r"(?:\033\[|\x9b)[0-?]*[ -/]*\Z")
_ESC2_RE = re.compile(r"\033[@-Z\\-_a-z0-9=>()#%+\-./ ]")
_ESC_LONE_RE = re.compile(r"\033")
# C0/C1 control chars to drop — everything except TAB/LF/CR.
_CTRL_KEEP = ("\t", "\n", "\r")
_CTRL_RE = re.compile(
    "[" + "".join(
        re.escape(chr(c))
        for c in list(range(0x00, 0x20)) + [0x7F] + list(range(0x80, 0xA0))
        if chr(c) not in _CTRL_KEEP
    ) + "]"
)


def sanitize_for_terminal(text: object) -> str:
    """Neutralize terminal control sequences + raw control chars in `text`.

    Strips OSC (incl. OSC 52 clipboard / OSC 8 hyperlink), DCS/SOS/PM/APC, CSI
    (incl. SGR color), other ESC sequences, and C0/C1 control chars — while
    PRESERVING \\t \\n \\r and all printable UTF-8. Use on EVERY memory-derived
    string before it enters the banner / systemMessage.

    Non-string input is coerced to str. NEVER raises (safe on the hot path).
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    # Payload-bearing sequences first (terminated, then unterminated remnants).
    text = _OSC_RE.sub("", text)
    text = _STRSEQ_RE.sub("", text)
    text = _OSC_UNTERM_RE.sub("", text)
    text = _STRSEQ_UNTERM_RE.sub("", text)
    # CSI (terminated then unterminated).
    text = _CSI_RE.sub("", text)
    text = _CSI_UNTERM_RE.sub("", text)
    # Other ESC sequences, then any lone ESC.
    text = _ESC2_RE.sub("", text)
    text = _ESC_LONE_RE.sub("", text)
    # Finally raw control chars (keep tab / newline / carriage-return).
    text = _CTRL_RE.sub("", text)
    return text


def _rgb_to_xterm256(r: int, g: int, b: int) -> int:
    """Map an RGB triple to the nearest xterm-256 color index.

    Considers both the 6x6x6 color cube (16-231) and the grayscale ramp
    (232-255), returning whichever is closer in squared-distance. Pure
    arithmetic; no tables beyond the cube levels.
    """
    cube_levels = (0, 95, 135, 175, 215, 255)

    def nearest_cube_axis(v: int) -> int:
        best_i, best_d = 0, 1 << 30
        for i, level in enumerate(cube_levels):
            d = (level - v) ** 2
            if d < best_d:
                best_i, best_d = i, d
        return best_i

    ri = nearest_cube_axis(r)
    gi = nearest_cube_axis(g)
    bi = nearest_cube_axis(b)
    cube_idx = 16 + 36 * ri + 6 * gi + bi
    cr, cg, cb = cube_levels[ri], cube_levels[gi], cube_levels[bi]
    cube_dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2

    # Grayscale ramp: 232..255 maps to gray 8,18,...,238.
    gray = round((r + g + b) / 3)
    gidx = round((gray - 8) / 10)
    gidx = max(0, min(23, gidx))
    gval = 8 + 10 * gidx
    gray_dist = (gval - r) ** 2 + (gval - g) ** 2 + (gval - b) ** 2

    if gray_dist < cube_dist:
        return 232 + gidx
    return cube_idx


def explicit_color_override() -> Optional[str]:
    """The user's EXPLICIT colour instruction, or ``None`` if they gave none.

    Split out of ``detect_mode()`` because TWO surfaces need this vocabulary and
    only one had it.  ``detect_mode()`` finishes by consulting the STREAM (a TTY
    check), which is meaningless on the session-start hook's injection channel --
    so ``hooks/session_start_inject.py`` chose a mode directly and, in choosing,
    silently ignored ``NO_COLOR`` and every ``VOIDLOOM_COLOR`` spelling but one
    (Crucible-3, seat U4: ``NO_COLOR=1 -> truecolor``, ``VOIDLOOM_COLOR=off ->
    truecolor``, on the one surface that fires at every session start).

    The vocabulary now lives HERE and nowhere else, so a spelling added here
    reaches both callers.  Two copies of a list of strings is how they drifted.

    Returns the forced mode, or ``None`` meaning "the user said nothing; decide
    by whatever means your channel actually supports".
    """
    if os.environ.get("NO_COLOR") is not None:          # https://no-color.org
        return "none"
    override = os.environ.get("VOIDLOOM_COLOR", "").strip().lower()
    if override in ("none", "no", "off", "plain"):
        return "none"
    if override in ("truecolor", "24bit", "24-bit", "full"):
        return "truecolor"
    if override in ("ansi256", "256", "256color"):
        return "ansi256"
    return None


def detect_mode(stream: Optional[TextIO] = None) -> str:
    """Return 'truecolor', 'ansi256', or 'none' for the given stream.

    Resolution order (most authoritative first):
      - NO_COLOR set (any value)            -> 'none'   (https://no-color.org)
      - VOIDLOOM_COLOR override             -> forced value if recognized
      - stream is not a TTY                  -> 'none'   (piped/injected output)
      - TERM is 'dumb' or empty              -> 'none'
      - COLORTERM in {truecolor,24bit}       -> 'truecolor'
      - TERM mentions 256color               -> 'ansi256'
      - otherwise                            -> 'ansi256' (safe color default)
    """
    _ov = explicit_color_override()
    if _ov is not None:
        return _ov

    s = stream if stream is not None else sys.stdout
    try:
        is_tty = bool(s.isatty())
    except Exception:
        is_tty = False
    if not is_tty:
        return "none"

    term = os.environ.get("TERM", "").strip().lower()
    if term in ("", "dumb"):
        return "none"

    colorterm = os.environ.get("COLORTERM", "").strip().lower()
    if colorterm in ("truecolor", "24bit"):
        return "truecolor"

    if "256color" in term or "256" in term:
        return "ansi256"

    # A color terminal of unknown depth: 256-color is the safe, widely-supported
    # choice (every modern emulator supports it; truecolor is not universal).
    return "ansi256"


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class Renderer:
    """Paints brand-colored text in the detected (or forced) color mode.

    Use one Renderer per output stream. The renderer NEVER raises on a bad
    color or an odd terminal — at worst it returns the plain text. This makes
    it safe to call from a fail-graceful hook.
    """

    def __init__(self, stream: Optional[TextIO] = None,
                 force_mode: Optional[str] = None) -> None:
        self.stream = stream if stream is not None else sys.stdout
        if force_mode in ("truecolor", "ansi256", "none"):
            self.mode = force_mode
        else:
            self.mode = detect_mode(self.stream)

    # -- low-level paint -------------------------------------------------

    def paint(self, text: str, color: Optional[Color],
              *, bold: bool = False, dim: bool = False) -> str:
        """Wrap text in the SGR escape for `color` in the active mode.

        In 'none' mode (or when color is None) returns text unchanged. Bold and
        dim are honored in color modes only. Always appends a reset so adjacent
        spans never bleed.
        """
        if self.mode == "none" or color is None:
            return text

        codes: List[str] = []
        if bold:
            codes.append("1")
        if dim:
            codes.append("2")

        if self.mode == "truecolor":
            codes.append(f"38;2;{color.r};{color.g};{color.b}")
        else:  # ansi256
            codes.append(f"38;5;{color.xterm256}")

        return f"\033[{';'.join(codes)}m{text}{_RESET}"

    # -- brand marks -----------------------------------------------------

    def sigil(self) -> str:
        """The composed sigil ◇◆✦ — hollow+filled diamonds in arcane violet,
        the four-point star in starlight gold. ALWAYS these colors."""
        d1, d2, star = SIGIL[0], SIGIL[1], SIGIL[2]
        return (
            self.paint(d1, ARCANE_VIOLET, bold=True)
            + self.paint(d2, ARCANE_VIOLET, bold=True)
            + self.paint(star, STARLIGHT_GOLD, bold=True)
        )

    def wordmark(self, spaced: bool = True) -> str:
        """The Voidloom wordmark in starlight gold, bold."""
        text = WORDMARK_SPACED if spaced else WORDMARK
        return self.paint(text, STARLIGHT_GOLD, bold=True)

    def tagline(self) -> str:
        """The tagline — present but quiet. Quiet by COLOUR, not by SGR-2: the
        attribute is terminal-defined and compounds with an already-muted hue."""
        return self.paint(TAGLINE, SLATE)

    def star(self, glyph: str = "·") -> str:
        """A single field star. Decorative, carries no words, and is the one
        element that SHOULD recede -- so it keeps both the darker hue and dim."""
        return self.paint(glyph, STARDUST, dim=True)

    def accent(self, text: str, *, bold: bool = False) -> str:
        """Starlight-gold accent text (for ids, counts, key words)."""
        return self.paint(text, STARLIGHT_GOLD, bold=bold)

    def violet(self, text: str, *, bold: bool = False) -> str:
        """Arcane-violet text (for the action glyph / call-to-action)."""
        return self.paint(text, ARCANE_VIOLET, bold=bold)

    def dim(self, text: str) -> str:
        """Secondary detail. Named `dim` for its 26 call sites; it is no longer
        SGR-dimmed AND no longer tinted, because this method carries the
        banner's actual SENTENCES -- counts, reasons, breakdowns -- and they
        have to be legible.

        Drew's ruling, 2026-09-08, looking at a live TUI: the DOMINANT colour
        should be the terminal's own foreground (what an unwrapped `1.` renders
        as), and the saturated hues -- gold, violet -- should be the RARE ones
        that mean something. A palette where the bulk of the prose is tinted
        spends its whole colour budget on the least important text. So the
        sentence role is now untinted: it inherits the reader's foreground,
        which is also the one colour guaranteed readable against the reader's
        own background, whatever theme they run."""
        return text

    def chrome(self, text: str) -> str:
        """Structural furniture -- rule lines, bullet separators. This is what
        SHOULD recede, so it keeps the lavender that `dim` just gave up. Split
        out of `dim` rather than added beside it: they were one method doing two
        jobs, and the sentence job is the one that had to stay legible.

        Drew's follow-up ruling, 2026-09-08: the rules take ARCANE_VIOLET -- the
        same hue already carrying `Approve / Reject / Snooze` and the detail
        view's `ref` -- rather than SLATE. That leaves the TUI on THREE colours
        doing three jobs (violet = structure and the verbs, gold = identity and
        the committing key, terminal-default = everything you actually read),
        instead of four hues with no rule about which is which."""
        return self.paint(text, ARCANE_VIOLET)

    def lockup(self) -> str:
        """The composed Voidloom lockup (the locked variant-B art): the spaced
        nested-diamond sigil + the 5-row block Voidloom wordmark with the
        deep-purple gradient, woven grey threads, and stars. Rendered in THIS
        renderer's mode (truecolor / ansi256 / none). See module-level
        ``lockup`` for the geometry contract."""
        return lockup(renderer=self)


# ---------------------------------------------------------------------------
# Sparse starfield — RESTRAINT enforced by construction.
# ---------------------------------------------------------------------------


def sparse_starfield(width: int,
                     *,
                     density: float = 0.06,
                     max_stars: int = 7,
                     min_gap: int = 3,
                     seed: Optional[int] = None,
                     renderer: Optional[Renderer] = None) -> str:
    """Scatter a FEW stars across a line of the given width.

    Restraint is the whole point. The number of stars is
    ``min(max_stars, floor(width * density))`` — a hard ceiling AND a small
    density. Stars never sit adjacent (``min_gap`` columns minimum between
    them), so the line breathes. The default of density=0.06 / max_stars=7
    gives ~3-5 stars across an 80-column line: scattered, calm, never a wall.

    Args:
        width: line width in columns.
        density: stars-per-column ceiling fraction (small by design).
        max_stars: absolute hard cap on star count regardless of width.
        min_gap: minimum empty columns between any two stars.
        seed: optional RNG seed for reproducible fields (tests / mockups).
        renderer: a Renderer to color the stars (slate, dim). If None, a
            no-color renderer is used so the raw glyphs are returned.

    Returns:
        A single line (no trailing newline) of spaces with a few colored stars.
    """
    if width <= 0:
        return ""
    rnd = random.Random(seed)
    rndr = renderer if renderer is not None else Renderer(force_mode="none")

    n_stars = min(max_stars, int(width * density))
    if n_stars <= 0:
        return " " * width

    # Choose positions with a minimum gap so stars never clump. We pick from a
    # thinned index space then re-expand, which guarantees the gap cheaply.
    slots = max(1, width // (min_gap + 1))
    n_stars = min(n_stars, slots)
    chosen_slots = sorted(rnd.sample(range(slots), n_stars))

    cells = [" "] * width
    for slot in chosen_slots:
        base = slot * (min_gap + 1)
        # jitter within the slot (but keep the gap) for an organic scatter
        jitter = rnd.randint(0, min_gap) if min_gap > 0 else 0
        pos = min(width - 1, base + jitter)
        glyph = rnd.choice(STAR_GLYPHS)
        cells[pos] = rndr.star(glyph)

    return "".join(cells)


# ---------------------------------------------------------------------------
# The lockup — the locked, approved Voidloom banner art (variant B).
#
# The GEOMETRY is locked: the glyph dict, the spaced nested-diamond sigil, the
# ribbon (every segment horizontal | vertical | exactly 45°, asserted), the
# 2-D canvas, the thread polylines, and the star positions are reproduced
# faithfully from the approved variant-B lockup. Only the COLOR layer is routed
# through this module's mode system so the SAME geometry degrades:
#   truecolor -> deep-purple vertical gradient + sigil violets/gold + grey stars
#   ansi256   -> nearest xterm-256 (automatic via Color.xterm256)
#   none      -> the identical glyph grid with ZERO escapes (seed/public path)
# The static lockup carries NO memory content, so it needs no sanitizing.
# ---------------------------------------------------------------------------

_LOCKUP_HEIGHT = 5
_LOCKUP_GLYPHS = {
    "V": ["█   █", "█   █", "█   █", " █ █ ", "  █  "],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "I": ["███", " █ ", " █ ", " █ ", "███"],
    "D": ["████ ", "█   █", "█   █", "█   █", "████ "],
    "L": ["█   ", "█   ", "█   ", "█   ", "████"],
    "M": ["█     █", "██   ██", "█ █ █ █", "█  █  █", "█     █"],
}
for _lc, _lr in _LOCKUP_GLYPHS.items():  # alignment guard (matches the source art)
    assert len(_lr) == _LOCKUP_HEIGHT and len({len(x) for x in _lr}) == 1, \
        f"lockup glyph {_lc} ragged"
_LOCKUP_WORD = "VOIDLOOM"
_LOCKUP_SIGIL = ["    ◇    ", "  ◇ ◆ ◇  ", "◇ ◆ ✦ ◆ ◇", "  ◇ ◆ ◇  ", "    ◇    "]
_LOCKUP_SIG_COL = {"◇": "so", "◆": "si", "✦": "sp"}

# The locked palette as Color objects (so ansi256 nearest-match is automatic).
# g0..g4 = the deep->light vertical word gradient; so/si/sp = sigil
# outer/inner/spark; hi/md/lo = void-star greys; bg = dim interior stars;
# th = subtlest grey thread.
_LOCKUP_COLORS = {
    "g0": Color("lockup_g0", 92, 68, 192),
    "g1": Color("lockup_g1", 106, 82, 204),
    "g2": Color("lockup_g2", 120, 96, 212),
    "g3": Color("lockup_g3", 142, 120, 222),
    "g4": Color("lockup_g4", 171, 148, 236),
    "so": Color("lockup_so", 110, 86, 207),
    "si": Color("lockup_si", 150, 125, 235),
    "sp": Color("lockup_sp", 200, 162, 74),
    "hi": Color("lockup_hi", 224, 224, 232),
    "md": Color("lockup_md", 158, 159, 170),
    "lo": Color("lockup_lo", 112, 113, 124),
    "bg": Color("lockup_bg", 96, 92, 116),
    "th": Color("lockup_th", 70, 71, 80),
}
# Which color keys render bold (matches the source: word gradient + spark).
_LOCKUP_BOLD = frozenset({"g0", "g1", "g2", "g3", "g4", "sp"})


class _LockupCanvas:
    """A tiny 2-D character canvas. Letters / threads / stars are composited by
    (x, y); ``render`` emits per-mode. The character grid is mode-independent —
    only the color layer changes. (Faithful port of the source-art canvas, with
    the color step routed through a Renderer.)"""

    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.co: List[List[Optional[str]]] = [[None] * w for _ in range(h)]

    def put(self, x: int, y: int, c: str, color: Optional[str], over: bool = True) -> None:
        if 0 <= y < self.h and 0 <= x < self.w and (over or self.ch[y][x] == " "):
            self.ch[y][x] = c
            self.co[y][x] = color

    def glyph(self, x0: int, y0: int, rows, color_of, over: bool = True) -> None:
        for dy, row in enumerate(rows):
            for dx, c in enumerate(row):
                if c != " ":
                    col = color_of(dx, dy, c) if callable(color_of) else color_of
                    self.put(x0 + dx, y0 + dy, c, col, over)

    def render(self, renderer: "Renderer") -> List[str]:
        """Render every row.

        'none' mode  -> the plain path: each row rstripped, carries NO escapes
                        (byte-parity with the locked --plain ground truth).
        color modes  -> contiguous runs of equal color are painted as ONE span
                        via the Renderer, so each color change costs a single
                        escape. NEVER raises (the Renderer never raises)."""
        plain = renderer.mode == "none"
        out: List[str] = []
        for y in range(self.h):
            if plain:
                row = "".join(self.ch[y][x] for x in range(self.w))
                out.append(row.rstrip())
                continue
            line: List[str] = []
            run_chars: List[str] = []
            run_col: Optional[str] = None

            def flush() -> None:
                if run_chars:
                    color = _LOCKUP_COLORS.get(run_col)
                    line.append(renderer.paint("".join(run_chars), color,
                                               bold=(run_col in _LOCKUP_BOLD)))
                    run_chars.clear()

            for x in range(self.w):
                c, col = self.ch[y][x], self.co[y][x]
                if c == " ":
                    flush()
                    run_col = None
                    line.append(" ")
                    continue
                if col != run_col:
                    flush()
                    run_col = col
                run_chars.append(c)
            flush()
            out.append("".join(line))
        return out


def _lockup_ribbon(cv: "_LockupCanvas", pts, color: str) -> None:
    """Draw a polyline whose every segment is horizontal | vertical | exactly
    45°. Asserts cleanliness so the line chars always connect (no fragmented
    slashes) — the source art's invariant, preserved verbatim."""
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        if dy == 0 and dx:
            s = 1 if dx > 0 else -1
            for x in range(x0, x1 + s, s):
                cv.put(x, y0, "─", color, over=False)
        elif dx == 0 and dy:
            s = 1 if dy > 0 else -1
            for y in range(y0, y1 + s, s):
                cv.put(x0, y, "│", color, over=False)
        elif abs(dx) == abs(dy) and dx:
            sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
            ch = "╲" if sx == sy else "╱"
            for i in range(abs(dx) + 1):
                cv.put(x0 + sx * i, y0 + sy * i, ch, color, over=False)
        else:
            raise ValueError(f"non-clean lockup segment {(x0, y0)}->{(x1, y1)}")


def _lockup_assemble(word: str, gutter: int = 1):
    rows, offs, x = [""] * _LOCKUP_HEIGHT, [], 0
    for i, ch in enumerate(word):
        g = _LOCKUP_GLYPHS[ch]
        w = len(g[0])
        offs.append((ch, x, w))
        for r in range(_LOCKUP_HEIGHT):
            rows[r] += g[r] + (" " * gutter if i < len(word) - 1 else "")
        x += w + (gutter if i < len(word) - 1 else 0)
    return rows, x, offs


# Precompute the locked layout once at import (mirrors the source constants).
_LOCKUP_WORDROWS, _LOCKUP_WORD_W, _LOCKUP_OFFS = _lockup_assemble(_LOCKUP_WORD)
_LOCKUP_SIG_W = len(_LOCKUP_SIGIL[0])
_LOCKUP_SIG_X, _LOCKUP_WORD_X = 0, _LOCKUP_SIG_W + 3
_LOCKUP_CY = 2                                   # content top row (2 void rows above)
_LOCKUP_CANVAS_W = _LOCKUP_WORD_X + _LOCKUP_WORD_W + 5
_LOCKUP_CANVAS_H = _LOCKUP_CY + _LOCKUP_HEIGHT + 1   # 1 void row below
_LOCKUP_B = _LOCKUP_CANVAS_H - 1                  # bottom void row
_LOCKUP_XR = _LOCKUP_CANVAS_W - 2                 # right-margin column


def _lockup_build_canvas() -> "_LockupCanvas":
    """Composite the locked lockup onto a fresh canvas (geometry only — color is
    applied at render time). Faithful port of the approved variant-B build()."""
    cv = _LockupCanvas(_LOCKUP_CANVAS_W, _LOCKUP_CANVAS_H)
    # 1) threads — the woven wrap (wave over the top, down the right, across bottom)
    wrap = [(2, 1), (8, 1), (9, 0), (20, 0), (21, 1), (32, 1), (33, 0), (44, 0),
            (45, 1), (52, 1), (_LOCKUP_XR, 1), (_LOCKUP_XR, _LOCKUP_B), (4, _LOCKUP_B)]
    _lockup_ribbon(cv, wrap, "th")
    # 2) THREE strands woven FROM a top-star diagonally THROUGH the letters
    _lockup_ribbon(cv, [(14, 0), (21, _LOCKUP_B)], "th")   # through V–O
    _lockup_ribbon(cv, [(38, 0), (31, _LOCKUP_B)], "th")   # through D–L
    _lockup_ribbon(cv, [(50, 0), (57, _LOCKUP_B)], "th")   # through O–M
    # 3) DIM interior stars spread the WHOLE way across the word, staggered rows
    ctr = [_LOCKUP_WORD_X + s + w // 2 for (ch, s, w) in _LOCKUP_OFFS]  # center x/letter
    for (x, y) in [(ctr[0], _LOCKUP_CY + 2), (ctr[1], _LOCKUP_CY + 1),
                   (ctr[3], _LOCKUP_CY + 2), (ctr[4], _LOCKUP_CY),
                   (ctr[5], _LOCKUP_CY + 2), (ctr[6], _LOCKUP_CY + 3),
                   (ctr[7], _LOCKUP_CY + 1)]:
        cv.put(x, y, "✦", "bg", over=True)   # V(spark-line) O1 D L O5 O6 M, LOO filled
    # 4) the mark (sigil + word) over the threads/interior-stars
    cv.glyph(_LOCKUP_SIG_X, _LOCKUP_CY, _LOCKUP_SIGIL,
             lambda dx, dy, c: _LOCKUP_SIG_COL.get(c, "so"))
    cv.glyph(_LOCKUP_WORD_X, _LOCKUP_CY, _LOCKUP_WORDROWS,
             lambda dx, dy, c: f"g{dy}")
    # 5) the bright void-stars on top, sitting on the wrap thread (grey/white)
    stars = [(2, 1, "·", "lo"), (14, 0, "✦", "md"), (26, 1, "✧", "hi"),
             (38, 0, "⋆", "md"), (50, 0, "✦", "md"), (52, 1, "·", "lo"),
             (_LOCKUP_XR, 1, "✦", "md"), (_LOCKUP_XR, _LOCKUP_B, "✦", "md"),
             (45, _LOCKUP_B, "⋆", "lo"), (30, _LOCKUP_B, "·", "lo"),
             (16, _LOCKUP_B, "✧", "hi"), (4, _LOCKUP_B, "·", "lo")]
    for (x, y, g, col) in stars:
        cv.put(x, y, g, col, over=True)
    return cv


def lockup(*, force_mode: Optional[str] = None,
           renderer: Optional[Renderer] = None) -> str:
    """The composed Voidloom lockup as a multi-line string (NO trailing
    newline).

    The locked variant-B art: the spaced nested-diamond sigil + the 5-row block
    Voidloom wordmark (deep-purple vertical gradient), with subtlest-grey woven
    threads and grey/white + dim interior stars. Degrades via the mode system:
    truecolor gradient -> ansi256 nearest -> mono (zero escapes, identical
    geometry). Pass a ``renderer`` to share a detected mode, or ``force_mode``
    to pin one. NEVER raises.
    """
    r = renderer if renderer is not None else Renderer(force_mode=force_mode)
    cv = _lockup_build_canvas()
    return "\n".join(cv.render(r))


# ---------------------------------------------------------------------------
# Recall-explanation one-liner — the L5-N-beta render primitive (render-only).
#
# This is the rendering SSOT for the "I surfaced this because…" line. It takes a
# pre-assembled list of FACTOR dicts (built by recall_explain.build_explanation
# from the sub-scores the producers already wrote) and paints a calm one-liner.
# It performs NO score computation — it only formats already-decided values.
# Each factor dict::
#     {"label": str, "present": bool, "display": str, "note": str|None}
# A present factor renders "<label-dim> <value-gold> [<note-dim>]"; an absent
# factor renders the whole "<label> —" dim. Memory-derived strings are run
# through sanitize_for_terminal so a hostile field can't inject terminal control.
# 'none' mode emits ZERO escapes (the mono path). NEVER raises.
# ---------------------------------------------------------------------------

EXPLAIN_ABSENT = "—"          # the calm "this producer said nothing" glyph
_EXPLAIN_SEP = " · "          # separator between factors


def render_explanation(factors, *, mode: Optional[str] = None,
                       renderer: Optional[Renderer] = None) -> str:
    """Render a list of explanation FACTOR dicts into one calm one-liner.

    ``factors`` is the ``obj["factors"]`` list from
    ``recall_explain.build_explanation`` (or any list of dicts with the keys
    ``label`` / ``present`` / ``display`` / ``note``). ``mode`` pins
    'truecolor' / 'ansi256' / 'none' (None -> auto-detect on stderr, the channel
    the --explain surface prints to). Pass ``renderer`` to share a mode.

    Render-only: never recomputes a score. Absent factors render '—'. In 'none'
    mode the line carries ZERO ANSI escapes. NEVER raises (mirrors the lockup
    contract); a bad factor list returns ''.
    """
    try:
        if not factors:
            return ""
        if renderer is not None:
            r = renderer
        elif mode in ("truecolor", "ansi256", "none"):
            r = Renderer(force_mode=mode)
        else:
            r = Renderer(stream=sys.stderr)

        parts: List[str] = []
        for fac in factors:
            if not isinstance(fac, dict):
                continue
            label = sanitize_for_terminal(fac.get("label", ""))
            present = bool(fac.get("present"))
            if present:
                display = sanitize_for_terminal(fac.get("display", EXPLAIN_ABSENT))
                seg = r.dim(label) + " " + r.accent(display)
                note = fac.get("note")
                if note:
                    seg += " " + r.dim(sanitize_for_terminal(note))
            else:
                seg = r.dim(label + " " + EXPLAIN_ABSENT)
            parts.append(seg)
        return _EXPLAIN_SEP.join(parts)
    except Exception:  # pragma: no cover - render must never break a caller
        return ""


# ---------------------------------------------------------------------------
# Self-demo — `python3 void_render.py` prints a calm sample in every mode.
# ---------------------------------------------------------------------------


def _demo() -> None:
    for mode in ("truecolor", "ansi256", "none"):
        r = Renderer(force_mode=mode)
        print(f"--- mode: {mode} ---")
        for _ln in lockup(renderer=r).split("\n"):
            print("  " + _ln)
        print()
        print("  " + sparse_starfield(60, seed=7, renderer=r))
        print("    " + r.sigil() + "   " + r.wordmark())
        print("  " + r.star("⋆") + "  " + r.star("✦") + "      "
              + r.tagline())
        print("  " + r.violet(GLYPH_PENNANT) + " "
              + r.accent("3", bold=True) + r.dim(" proposals await ")
              + r.violet("→ " + APPROVALS_CMD))
        print("  " + r.accent(GLYPH_STAR_FILLED) + r.dim(" anticipated · ")
              + r.accent("#412 #399") + "   "
              + r.accent(GLYPH_LOZENGE) + r.dim(" forgotten · ")
              + r.accent("#118") + r.dim(" (23d)"))
        print()


if __name__ == "__main__":
    _demo()
