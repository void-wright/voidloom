---
description: Convene the Inner Council — five cognitive lenses deliberate a decision in-session, then synthesize. Free (no background process). Optionally pass the question as an argument.
argument-hint: [the decision or question to deliberate]
---

# /council — convene the Inner Council

Convene a multi-lens deliberation on a hard or hard-to-reverse decision. Five
cognitive lenses each examine the question from a fixed perspective, then a sixth
step synthesizes them into a decision and records it.

**This runs entirely in-session and is free.** You (the running assistant)
reason as each lens in turn — there is **no** `claude -p`, no subprocess, no
background process, and no extra API calls beyond the conversation you are
already in. Convening the council costs nothing more than thinking carefully.

## The decision under deliberation

$ARGUMENTS

> If the line above is empty, ask the user for the decision in one sentence
> before convening. The council needs a concrete question to deliberate.

## How to run the deliberation

Step into each lens in order. Each lens has a full brief in
`~/.claude/skills/council-<lens>/SKILL.md` — embody it, do not merely summarize
it. Speak as that lens, then move to the next. Keep each lens's contribution
focused (a few tight paragraphs); the value is distinctness, not length.

1. **Convene (Synthesizer).** Restate the decision in one clear sentence. If it
   is genuinely trivial or easily reversible, say so and offer to skip the
   council — it is for weighty, expensive-to-reverse, or contradiction-laden
   calls.

2. **Seer** — *what is already known?* Recall relevant precedents and prior
   memories from the brain, surface hidden assumptions, and report honestly if
   the record is empty. Ground the decision in what actually exists.

3. **Skeptic** — *what breaks?* Enumerate failure modes (specific mechanism +
   condition + consequence), audit unstated assumptions, classify by severity,
   and pose the one stress-test question that would resolve the primary concern.

4. **Architect** — *what else changes?* Trace the downstream ripple (first /
   second order / deferred), name the trade-offs between viable structures, and
   state the rollback path.

5. **Dreamer** — *what are we not considering?* Offer one or two alternatives
   (each tied to a concrete connection), optionally one reframe of the question,
   and hand each alternative to the Skeptic to stress-test.

6. **Synthesize (Synthesizer).** Weigh the four lenses. Decide — a path, an
   alternative, or a combination. Make the trade-off explicit (what risk is
   accepted and why; what the rollback covers). Record any dissent verbatim with
   the consequence it predicts.

## After the synthesis — record it

Store the outcome so a future session can recall it. The reasoning and any
dissent are the asset; the conclusion alone teaches nothing. For example:

```bash
python3 ~/.claude/memory/organs/claude_memory.py store \
  --content "Council decision: <the call> — <one-line reasoning>. Dissent: <if any>." \
  --category project_knowledge \
  --tags council,decision \
  --kind reflection \
  --importance 0.85
```

(Use `claude_memory_lite.py` instead if you are on the keyword-only tier. Adjust
tags and category to taste — the brain stores it either way.)

## Notes

- **Works in every project.** The five lens skills and this command install
  globally (`~/.claude/skills/` and `~/.claude/commands/`), so `/council` is
  available in any working directory on the machine.
- **The lenses are optional and editable.** They ship as ready-to-use briefs you
  can rewrite to fit how you think. Delete a lens you do not want; the others
  still convene.
- **No autonomous council ships.** An automated, one-process-per-lens version
  would call the model repeatedly and cost credits, so it is intentionally not
  included. The council is interactive-only by design.
