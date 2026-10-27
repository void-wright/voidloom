---
description: Craft (or revise) your own working persona for this Voidloom brain — a short guided conversation.
---

Load the `persona-forge` skill (`~/.claude/skills/persona-forge/SKILL.md`) and follow it from Step 0.

Only in an interactive session with the human present. Start by running
`voidloom persona status --json` to see whether a persona exists (keep `data.skills_dir`
— the forge writes there):
absent → offer the first crafting; declared-missing-skill → offer to re-forge;
forged → offer revise mode; declined → proceed only because the human asked.

Explain the baseline in plain words (BASELINE.md), ask the questionnaire in rounds —
Round 0 about the human first (QUESTIONNAIRE.md — use AskUserQuestion when available: at
most 4 questions per call, at most 4 options each, first option "(Recommended)"), synthesise the persona from the
templates, show a one-screen draft card, revise until approved, then write, validate,
declare, wire (ask first) and store the identity memories.

$ARGUMENTS
