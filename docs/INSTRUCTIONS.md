# Claude Memory System — Vectorized Brain

> `$Voidloom` is wherever the brain is installed — `~/.claude/memory` by default.
> `export Voidloom="$HOME/.claude/memory"` to follow the commands below verbatim.


## HOW IT WORKS
You have a vectorized SQLite3 memory database at `$Voidloom/claude_memory.db`.
It stores memories as vector embeddings for semantic search: 768-dimensional
nomic-embed-text-v1.5 on a CPU machine, 1024-dimensional Qwen3-Embedding-0.6B on a GPU machine
(the installer chooses; see NOTICE for every model and its licence).
Use `python3 $Voidloom/organs/claude_memory.py <command>` to interact with it.

## SESSION START PROTOCOL
At the beginning of EVERY conversation, load your context:
```
python3 $Voidloom/organs/claude_memory.py context --project "{project}" --limit 15
```
This retrieves your most important and recent memories for the current project.

## WHEN TO STORE MEMORIES (Be Liberal — More Is Better)

### ALWAYS Store When:
- User corrects you or expresses a preference ("I prefer X", "Don't do Y")
- You discover a file path, convention, or pattern that was hard to find
- An architectural or technical decision is made
- You solve a debugging problem (the problem AND the solution)
- A workflow optimization is identified
- You learn something about how a skill, agent, or tool works
- The user tells you to remember something
- You encounter a recurring pattern or anti-pattern
- Cross-project knowledge is generated (store with all relevant project tags)
- A campaign reaches a milestone or produces a key artifact
- You make a mistake and learn from it (store the correction)

### Store With Good Metadata:
- **Category**: Pick the most specific category that fits
- **Tags**: 2-5 descriptive tags for cross-cutting retrieval
- **Importance**: 0.3 (nice to know) → 0.5 (useful) → 0.7 (important) → 0.9 (critical) → 1.0 (never forget)
- **Context**: Brief note about what prompted this memory

## WHEN TO RECALL MEMORIES

### ALWAYS Recall When:
- Starting a new task (recall memories related to the topic)
- Before making a technical decision (check for prior decisions)
- When working with a tool/skill/agent (recall tips and gotchas)
- When the user asks "do you remember..." or "we discussed..."
- Before generating content (recall user preferences and past corrections)
- When encountering an error (check for known solutions)
- When working in a specific project area (recall project-specific knowledge)

## COMMAND REFERENCE

```bash
# Store a memory
python3 $Voidloom/organs/claude_memory.py store \
  --content "Memory text" \
  --category user_preference \
  --tags "tag1,tag2" \
  --importance 0.8 \
  --project "my-project" \
  --context "Why this was stored"

# Semantic search
python3 $Voidloom/organs/claude_memory.py recall \
  --query "search text" \
  --top_k 5 \
  --category user_preference \
  --project my-project

# List memories
python3 $Voidloom/organs/claude_memory.py list \
  --category error_pattern \
  --limit 20 \
  --sort importance

# Update a memory
python3 $Voidloom/organs/claude_memory.py update \
  --id 42 \
  --content "New content" \
  --importance 0.95

# Link related memories
python3 $Voidloom/organs/claude_memory.py link \
  --source 42 --target 17 \
  --type related --strength 0.8

# Database stats
python3 $Voidloom/organs/claude_memory.py stats

# Maintenance (decay, dedup)
python3 $Voidloom/organs/claude_memory.py maintain

# Session context load
python3 $Voidloom/organs/claude_memory.py context \
  --project "my-project" --limit 15
```

## MEMORY CATEGORIES

| Category | Use For |
|----------|---------|
| `user_preference` | How the user likes things done |
| `technical_decision` | Architecture choices, design rationale |
| `project_knowledge` | File paths, conventions, structure |
| `error_pattern` | Bugs encountered and solutions |
| `skill_lesson` | Patterns learned about skills/agents |
| `workflow_pattern` | Recurring task solutions |
| `people_context` | People, roles, relationships |
| `tool_optimization` | Tool usage tips and gotchas |
| `domain_knowledge` | Subject matter expertise gained |
| `campaign_state` | Campaign progress and outcomes |
| `correction` | Times the user corrected Claude |
| `insight` | General insights and observations |

## LINK TYPES

| Type | Meaning |
|------|---------|
| `related` | Topically connected |
| `supersedes` | Newer version of older memory |
| `contradicts` | Conflicts with another memory |
| `elaborates` | Expands on another memory |
