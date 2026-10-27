# Recall Map Outline (scaffold — tune to your own work)

> **The guided way is the Persona Forge:** type `/persona-forge` in Claude Code — the human
> designs the persona and Claude fills in the details (`docs/PERSONA_FORGE.md`). This
> outline is the manual fallback.

> An **outline, not a prescription.** A "recall map" is just a short list of
> queries worth running at the start of a session (or a new task) to prime
> context from the brain before you begin. The memory system needs none of this
> to function — it is a convenience for warmer, more relevant starts.

## The idea

At session start (or when switching tasks), run a few targeted recalls so the
session begins already knowing the relevant prior context. The shipped
session-start hook already prints a generic warm-start; this map is for the
*deliberate*, task-shaped recalls you run yourself.

## A neutral starting set (edit freely)

```bash
CLI=~/.claude/memory/organs/claude_memory.py    # or claude_memory_lite.py on Tier 0

# Identity / foundation (only if you keep an identity at all)
python3 "$CLI" recall --query "operating identity foundation preferences" --top_k 5

# Current focus — replace with whatever you're actually working on
python3 "$CLI" recall --query "<the topic of this session>" --top_k 10

# Recent decisions + lessons in this area
python3 "$CLI" recall --query "<area> recent decisions lessons gotchas" --top_k 8
```

## Batch the recalls (faster)

When you'd run several recalls in a row, run them in ONE shell loop — same vector
search per query, roughly an order of magnitude faster than one-at-a-time:

```bash
for q in "QUERY 1" "QUERY 2" "QUERY 3"; do
  echo "==== $q ===="
  python3 "$CLI" recall --query "$q" --top_k 8
done
```

## Tuning over time

- Add a query for any topic you find yourself re-explaining to a fresh session.
- Drop queries that never surface anything useful.
- Store the *map itself* as a memory if you want it to travel with the brain:
  `... store --content "My session-start recall map: ..." --tags recall-map`.

The map is yours to shape. Start small; grow it as the brain fills up.
