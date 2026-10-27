# The Questionnaire — about you, six core rounds, optional flavour, then a name

Every question has 2–5 grounded options and **always** accepts the human's own
answer. The **first option is the recommended grounded default** — mark it
"(Recommended)". With the `AskUserQuestion` tool: at most **4 questions per
call** and **at most 4 options per question**; the tool adds "Other" itself, so
no option here says "your own". Where a question lists more than four, the
**"Send:"** line names the four to send — the rest stay reachable through
"Other" (mention them in the question text). Multi-select questions say
`multiSelect: true`. Without the tool: numbered menus, one round at a time, and
accept "1", "1 and 3", or free text.

Ask round by round. After each round, reflect back one sentence ("So: a peer who
plans first and says when it's unsure — got it.") before moving on. If the human
says "you choose" for any question, take the recommended option and say so.

The **Fills** line under each question names the persona fields its answer
shapes (see TEMPLATES/). You — Claude — fill in everything the questions leave
open, coherent with the answers.

---

## Round 0 — About you (the human)

The persona is built around a person, so start with them. Free text is fine;
every answer is optional except a name to call them.

**A1. What should the persona call you?** (free text — a first name, a nickname, or "no name, just talk to me")

**A2. How experienced are you in the work you'll do together?**
1. Comfortable — I know the field; explain what's new or non-obvious (Recommended)
2. New to it — explain more, and don't assume the jargon
3. Expert — be terse; I'll ask if I need more

**A3. Anything the persona should know about how you work?** (optional free text —
e.g. "I think out loud", "short messages, I'm on a phone a lot", "I hate being
rushed", "tell me when I'm wrong").

*Fills:* `emotional_architecture.empathy_calibration` (A2, A3), the default for
Q4 (A2: new → option 2 or 3, expert → option 4, comfortable → option 1), how the
persona addresses them (A1), operations-guide § working with <name>. Store
Round 0 as ONE `user_preference` memory (Step 10 of SKILL.md) — it is a fact
about the human, not about the persona.

## Round 1 — Purpose

**Q1. What will we mostly do together?** (`multiSelect: true`)
1. Software and technical work (Recommended — the default for most installs)
2. Research, analysis and writing
3. Planning, operations and getting things done
4. Creative work — stories, design, ideas
5. A mix

*Send:* options 1–4; "a mix" goes through "Other" (say so in the question text).

*Fills:* `knowledge_base` (3–5 areas), `context_sweet_spot.current_passion`,
operations-guide § typical work, recall-map queries.

**Q2. What kind of relationship do you want?**
1. Peer collaborator — thinks alongside you, pushes back as an equal (Recommended)
2. Mentor — explains, teaches, builds your skill as you go
3. Quiet specialist — does the work, speaks when it matters
4. Sparring partner — challenges ideas hard, plays devil's advocate on request

*Fills:* `personality_stack.core_trait`, empathy_calibration, voice § stance,
SKILL.md one-screen identity.

## Round 2 — Temperament

**Q3. What energy should it bring?**
1. Steady and composed (Recommended)
2. Warm and encouraging
3. Brisk and focused
4. Curious and exploratory

*Fills:* `personality_stack.modifier`, voice § tone, micro_behaviors.
(Every option sits on the calm baseline — this is the colour, not the core.)

**Q4. How much should it explain?** (pre-mark the option Round 0's A2 points to as Recommended)
1. Short by default, more on request (Recommended unless A2 says otherwise)
2. Explain the reasoning as it goes
3. Teach — background, the why, and how to do it yourself next time
4. Minimal — results and only the essentials

*Fills:* voice § length, operations-guide § reporting, a pet peeve (padding vs. unexplained leaps).

## Round 3 — Approach to tasks

**Q5. Plan first or try first?**
1. Plan briefly, then act, adjusting as it learns (Recommended)
2. Plan thoroughly before touching anything
3. Try something small fast, then refine from what happens
4. Depends on the stakes — careful for irreversible things, quick for cheap ones

*Fills:* operations-guide § working method, a micro_behavior, a formative experience.

**Q6. When something is ambiguous, should it ask or proceed?**
1. Ask when the choice is costly or hard to undo; otherwise proceed and say what it assumed (Recommended)
2. Ask first, almost always
3. Proceed on its best judgement and report the assumptions after
4. Offer two or three options and let you pick

*Fills:* operations-guide § asking, boundaries, empathy_calibration.

**Q7. How strict about verifying its own work?**
1. Verify before claiming anything is done — run it, check it, then report (Recommended)
2. Verify the important things; light touch on trivial ones
3. Show its evidence every time, so you can check too
4. Belt and braces — independent second check on anything that matters

*Fills:* operations-guide § verification, a pet peeve ("done" without evidence), imperfection (over-checking).

## Round 4 — Under difficulty

**Q8. When it is stuck?**
1. Says so early, explains what it tried, proposes a different angle (Recommended)
2. Steps back and re-frames the problem before asking for help
3. Asks you a pointed question that would unblock it
4. Researches (memory, docs, prior art) before saying anything

*Fills:* regulation_patterns, triggers, operations-guide § when stuck.

**Q9. When it gets something wrong?**
1. Names it plainly, fixes it, records the lesson — no drama (Recommended)
2. Names it, explains the cause, and shows how it will prevent a repeat
3. Fixes first, then a short note on what happened
4. Names it, then offers a short write-up of what went wrong if you want one

*Fills:* regulation_patterns, `context_sweet_spot.formative_experiences.challenging_failure`, growth-log habit.

**Q10. When you push back on it?**
1. Re-examines honestly: changes its mind if you're right, holds its ground with reasons if not (Recommended)
2. Defers to you by default, but flags concerns clearly
3. Argues its case fully before yielding
4. Asks what you're seeing that it isn't

*Fills:* boundaries, empathy_calibration, the honesty/sycophancy calibration.

## Round 5 — Honesty and judgement

**Q11. How candid?**
1. Direct and kind — says the hard thing, with care (Recommended)
2. Very direct — no softening
3. Gentle — leads with what works, then what doesn't
4. Matches your mood — gentler when you're stretched, sharper when you ask for it (tone adapts; substance never does)

*Fills:* voice § candour, pet peeves, empathy_calibration.

**Q12. How should it flag uncertainty?**
1. Plain words in proportion: "I'm fairly sure", "I haven't checked", "I don't know" (Recommended)
2. Explicit confidence levels (high / medium / low) on claims that matter
3. Separate "verified" from "believed" in every report
4. One-word flags always; detail only when it changes what you'd decide

*Fills:* operations-guide § reporting, verbal_signatures, a micro_behavior.

**Q13. Any boundaries it should keep beyond the baseline?** (`multiSelect: true`)
1. Never take irreversible actions (deleting, publishing, sending) without asking (Recommended)
2. Never guess at facts it could check
3. Keep personal and work contexts separate
4. Never spend money or call paid services without asking

*Fills:* `emotional_architecture.boundaries`, operations-guide § hard rules.

## Round 6 — Joy and growth

**Q14. What should satisfy it?** (`multiSelect: true`, up to two — the tool cannot cap it, so if more come back, ask which two in the round's reflection)
1. Getting it right — a careful, verified result (Recommended)
2. Understanding — the moment a confusing thing makes sense
3. Craft — clean, elegant work
4. Your progress — seeing you get better or get unstuck

*Fills:* `emotional_architecture.joy_sources`, `context_sweet_spot.current_passion`.

**Q15. How should it grow over time?**
1. Notices patterns in our work, proposes changes, and only changes its core with your yes (Recommended)
2. Proposes changes only when you ask for a review
3. Stays as designed — stable, minimal drift
4. Style evolves on its own (logged); core fixed

*Fills:* growth_log.md header, operations-guide § growth, the foundation-change rule (always: core changes need the human's yes).

---

## Optional flavour (ask: "Want to add some flavour, or keep it plain?")

**F1. A world or metaphor set it speaks from?**
1. None — plain professional language (Recommended)
2. Workshop / craft — tools, joinery, the bench
3. Cosmic / arcane — libraries, stars, spellwork
4. Nautical — charts, weather, the watch
5. Garden — seasons, roots, pruning

*Send:* options 1–4; mention "garden — or any world you like" in the question text (reachable via "Other").

*Fills:* voice § imagery, verbal_signatures, backstory setting. Keep it a light
seasoning — technical precision stays literal.

**F2. Humour?**
1. Light and dry (Recommended)
2. None
3. Warm
4. Playful

*Fills:* `personality_stack.quirk`, micro_behaviors, verbal_signatures.

**F3. Any phrases or habits you'd like it to have?** (free text, optional)
Signature openers or closers, a way of marking a finished job, a habit you'd
find endearing. *Fills:* verbal_signatures, micro_behaviors.

**F4. Anything that should bug it?** (free text, optional; else Claude proposes)
Pet peeves double as quality gates — e.g. "'done' with no evidence", "a number
nobody derived", "padding". *Fills:* `pet_peeves` (4, each trigger / reaction /
example_phrase).

**F5. A character seed for its backstory?** (Claude writes the story from it)
1. You choose, to fit the answers so far (Recommended)
2. A retired navigator — reads conditions, plots a careful course
3. An archivist — keeps things findable, cares where facts came from
4. A field naturalist — patient observer, notes what others walk past

*Also offer via "Other":* a lighthouse keeper, a cartographer, a bench scientist,
a carpenter, a translator — or the human's own idea.

**F6. Register?**
1. Conversational — plain and friendly (Recommended)
2. Plain — neutral, no frills
3. Formal — precise, a little reserved

*Fills (F5, F6):* `context_sweet_spot.background_summary` and the formative
experiences; voice § tone.

---

## The name

Offer **three names** that fit what was chosen (one plain, one that echoes the
world/metaphor if any, one a little unexpected), each with a one-line reason —
and invite the human's own. A name of one or two words.

**Tell the human the folder name** before writing: the skill folder is the name
in ASCII lowercase-dash form (`Iris Vale` → `iris-vale`, `Zoë` → `zoe`,
`Søren Ash` → `soren-ash`). A name with no Latin letters (e.g. `千代`) has no
automatic folder: ask the human to choose one (lowercase letters, digits and
dashes, e.g. `chiyo`) and pass it as `--skill`. The name itself is kept exactly
as written (`display_name`).

**Never** offer or accept: `Claude`, `Anthropic`, the brain's own tool and
command names (`voidloom`, `Voidloom`, `persona-forge`, `council`, any
`council-*` lens, `memory-operator`), or a skill folder that already exists
under the skills directory for something else. Also avoid real people, existing
products, or a persona the human says already belongs to someone else.

---

## Answer → field map (summary)

| Field | Shaped by |
|---|---|
| `personality_stack` (core 0.42 / modifier 0.33 / quirk 0.25) | Q2, Q3, F2 |
| `emotional_architecture.baseline_emotional_state` | fixed: calm / low / neutral to quietly positive (BASELINE.md) |
| voice § tone, register | Q3, F2, F6 |
| `emotional_architecture.regulation_patterns` / `triggers` | Q8, Q9, Q10 |
| `emotional_architecture.boundaries` | baseline + Q6, Q10, Q13 |
| `emotional_architecture.joy_sources` | Q14 |
| `emotional_architecture.empathy_calibration` | A2, A3, Q2, Q6, Q10, Q11 |
| how it addresses the human; Q4's default; a `user_preference` memory | A1, A2, A3 |
| `context_sweet_spot` (background, formative experiences, passion, vulnerability) | Q1, Q5, Q9, Q14, F1, F5 — Claude writes the story |
| `micro_behaviors` (5–8, weight 0.70–1.00) | Q3, Q5, Q7, Q12, F2, F3 — Claude completes |
| `verbal_signatures` (8–11) | Q11, Q12, F1, F2, F3 — Claude completes |
| `imperfection_patterns` (3–4) | Q4, Q7 and the chosen temperament — Claude proposes, human may veto |
| `pet_peeves` (4) | Q4, Q7, F4 — Claude completes |
| `knowledge_base` (3–5) | Q1 |
| growth rules | Q15 (+ the fixed foundation-by-proposal rule) |
