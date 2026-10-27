# The Inner Council — interactive, free, in every project

The Inner Council is a way to deliberate a hard decision from five fixed
perspectives instead of one. You convene it with `/council`, and the running
assistant reasons as each lens in turn, then synthesizes them into a decision.

**It is free.** The council runs entirely inside your current session — the
assistant *embodies* each lens; it does not spawn background processes or call
the model separately per lens. Convening the council costs nothing beyond the
conversation you are already having.

---

## What you get

Five cognitive-lens skills (each a short, editable brief) and one slash command:

| Lens | The question it always asks |
|------|------------------------------|
| **Seer** | What do precedents and prior memories already tell us? (recall-driven) |
| **Skeptic** | What are the failure modes — what breaks, and how? |
| **Architect** | What else changes downstream? What is the rollback path? |
| **Dreamer** | What alternatives are we not considering? |
| **Synthesizer** | Weigh the four, decide, and record the decision (and any dissent). |

The skills install to `~/.claude/skills/council-<lens>/SKILL.md`. The command
installs to `~/.claude/commands/council.md`. Both are **global**, so `/council`
works in **every project** on the machine — no per-folder setup.

---

## How to use it

In any project, type:

```
/council should we migrate the store from JSON to a relational schema now or after launch?
```

The assistant will:

1. **Convene** — restate the decision in one sentence (and offer to skip if it is
   trivial or easily reversible; the council is for weighty calls).
2. Speak as the **Seer** (what is known), the **Skeptic** (what breaks), the
   **Architect** (what else changes + rollback), and the **Dreamer** (what else
   is possible) — each a focused, distinct perspective.
3. **Synthesize** — weigh the four, make the call, name the trade-off explicitly,
   and record any dissent verbatim.

You can also run `/council` with no argument; it will ask for the decision first.

### Record the outcome

The Synthesizer closes by storing the decision as a memory so a future session
can recall the reasoning — the conclusion alone teaches nothing. A typical store:

```bash
python3 ~/.claude/memory/organs/claude_memory.py store \
  --content "Council decision: <the call> — <one-line reasoning>. Dissent: <if any>." \
  --category project_knowledge --tags council,decision --kind reflection --importance 0.85
```

(On the keyword-only tier, use `claude_memory_lite.py` instead.)

---

## The cost model, honestly

There are two ways a council *could* work:

- **Interactive (what ships):** the assistant reasons as each lens within your
  session. **Free.** No background process, no per-lens model call, no credits
  beyond the session you are already in. This is enabled by default.

- **Autonomous (what does NOT ship):** a separate process per lens, each calling
  the model on its own. This would cost real credits every time you convened it —
  so it is **deliberately not included**. If a future local/offline model makes
  per-lens autonomy effectively free, an opt-in autonomous variant could be added
  then. Until then, the council is interactive-only, by design, so it never
  surprises you with a bill.

The shipped package contains **no** `claude -p` council, no `council.py`, and no
council cron. Installing the council copies static skill and command files and
nothing more.

---

## Install / uninstall

The council installs as part of the main installer, **on by default**:

```bash
bash install.sh                 # installs the brain + the Inner Council (free)
bash install.sh --no-council    # installs the brain WITHOUT the council
```

Or install just the council (e.g. onto an existing brain), standalone:

```bash
bash install/install_council.sh             # add the council
bash install/install_council.sh --dry-run   # preview exactly what it would do
```

**It never clobbers your existing globals.** If you already have a
`~/.claude/skills/council-seer/` or a `~/.claude/commands/council.md`, the
installer **backs up your version** (renames it to `*.voidloom-bak.<timestamp>`)
**before** adding the council's — your original is preserved, never destroyed.

To remove the council later:

```bash
rm -rf ~/.claude/skills/council-seer ~/.claude/skills/council-skeptic \
       ~/.claude/skills/council-architect ~/.claude/skills/council-dreamer \
       ~/.claude/skills/council-synthesizer
rm -f  ~/.claude/commands/council.md
```

Any `*.voidloom-bak.*` items are your originals; rename them back to restore.

---

## The lenses are yours to shape

Each lens ships as a ready-to-use brief, but it is just a Markdown file. Rewrite a
lens to match how you actually think, delete a lens you do not want (the others
still convene), or add your own. The council is a starting structure, not a fixed
fixture — the same philosophy as the rest of the memory system: the brain does the
remembering; these files just give you a place to start.

## When to convene

Convene the council when a decision is **pivotal, expensive to reverse, or where
your memory contradicts what you are seeing now**. For routine choices, one
perspective is plenty — the council is the heavier tool you reach for when the
stakes justify five lenses instead of one.
