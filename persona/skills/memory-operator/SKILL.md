---
name: memory-operator
description: Operate this Voidloom memory brain from inside a session -- recall before you decide or build, store what you learn as you go, and (optionally) give the assistant a stable operating identity and a session-start recall map. Use when a session starts with a brain attached, when the human asks what was decided, preferred or tried before, when you are about to store a decision, a lesson or a correction, or when the human wants a named operating identity or a warmer session start without running the Persona Forge.
---

# Memory Operator

This skill is how a session works **with** the brain rather than beside it. It holds no persona and needs no
setup: the brain works with a blank identity, and everything below is optional except the first two habits.

## The two habits

1. **Recall first.** Before you answer a question about a past decision, a preference, a prior failure or a
   convention -- and before you build something that may already exist -- ask the brain:

   ```bash
   voidloom recall --query "<what you are about to decide or build>" --top-k 10 --json
   voidloom batch-recall --items '["<query one>", "<query two>"]' --top-k 10 --json
   ```

   A recall that answers by keyword says so at the top of its answer (`degraded: true`); treat it as narrower than
   a semantic one, and `voidloom doctor` names why.

2. **Store as you go.** A decision, a lesson, a correction or a fact worth keeping is stored when it happens, with
   its reason, not at the end:

   ```bash
   voidloom store --content "<what, and why>" --category <category> --json
   voidloom batch-store --items '[{"content": "...", "category": "..."}]' --json
   ```

   On a brain whose install lock names a model, a store refuses, typed, when that model cannot be loaded, rather
   than keep a memory semantic recall cannot reach; on a brain with no lock it stores by keyword and says
   `store_degraded`. `voidloom doctor` says what to fix.

## Optional: an operating identity and a recall map

Two outlines sit beside this file. Each is a scaffold to fill in yourself, never a prescription:

- [IDENTITY_OUTLINE.md](IDENTITY_OUTLINE.md) -- if the human wants the assistant to keep a stable operating
  identity across sessions, how to write one and store it so every session can recall it.
- [RECALL_MAP_OUTLINE.md](RECALL_MAP_OUTLINE.md) -- a short list of queries worth running at the start of a session
  or a new task, so it begins already knowing the relevant prior context.

The guided way to make a persona is the Persona Forge (`/persona-forge`, `docs/PERSONA_FORGE.md`); these outlines
are its manual fallback.
