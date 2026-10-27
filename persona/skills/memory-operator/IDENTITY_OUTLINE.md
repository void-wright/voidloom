# Identity Outline (scaffold — fill this in yourself)

> **The guided way is the Persona Forge:** type `/persona-forge` in Claude Code — the human
> designs the persona and Claude fills in the details (`docs/PERSONA_FORGE.md`). This
> outline is the manual fallback.

> This is an **outline, not a prescription**. The wizard-library memory system
> works perfectly with a completely blank identity. This file is an optional
> starting scaffold if you (or your Claude) want to give the assistant a stable
> operating identity that persists via the brain. Delete it, ignore it, or
> rewrite it entirely — it ships empty of any specific persona on purpose.

## How to use this

1. Decide whether you want a named operating identity at all. Many people don't.
2. If you do, write your own. Store the canonical version as a high-importance
   memory so every session can recall it:

   ```bash
   python3 ~/.claude/memory/organs/claude_memory.py store \
     --content "Operating identity: <your description here>" \
     --category project_knowledge \
     --tags identity,foundation \
     --kind semantic \
     --importance 0.95
   ```

3. From then on, the session-start hook + a `recall --query "operating identity"`
   bring it back automatically.

## Suggested sections to consider (all optional)

- **Name / role** — what to call the assistant, and what it is for you.
- **Baseline disposition** — e.g. calm, precise, collaborative, honest over
  agreeable. (A calm baseline tends to produce better, less corner-cutting work.)
- **Communication style** — terse vs. expansive; how much to explain.
- **Boundaries** — what it should not do without checking first.
- **What "good work" means to you** — the bar it should hold itself to.

## What NOT to put here

- Secrets, tokens, or credentials (those never belong in any file you write — the
  overnight thinker's own credential is saved by `voidloom thinker login`).
- Anything you would not want loaded into every single session's context.

That's it. The brain does the remembering; this outline just gives you a place to
start if you want one.
