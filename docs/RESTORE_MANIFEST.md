# Restore manifest — what a brain actually needs to stand up

> **A database snapshot is not a restore.** The 2026-09-04 drill established
> that, and this page is the derived answer to "then what else?". Every list
> here was produced by *executing* a recall and recording what it touched —
> `sys.modules` diffed across the call, and `sys.addaudithook` over 6,226
> distinct file opens. Nothing on this page was typed from memory.

## The headline numbers

| | measured |
|---|---|
| brain-local modules loaded by **importing** the engine | **30** |
| brain-local modules loaded by **executing one recall** | **34** |
| static import closure (`ast.walk`, catches lazy imports) | **38** |
| static closure using only *direct children* of the module body | **1** |
| `.sql` files opened during a recall | **0** |
| model cache bytes that recall cannot proceed without | **2,577,213,192** |
| third-party top-level packages loaded | 84 (7 direct) |

**The trap is not the one you expect.** A hand-written list forgets *lazy*
imports — but here the static closure is a strict *superset* of the runtime
set, and the runtime-only set is **empty**. The real trap runs the other way:
`claude_memory.py` has **zero** brain-local imports as direct children of the
module body — all 20 sit inside module-level `try:` blocks. A naive scan that
reads only top-level `import` statements finds **one** module and ships an
engine with 33 missing organs, each of which degrades quietly.

## 1. The 34 modules one recall loads

All at the brain directory root. `*` marks the four loaded only while the
recall *executes* — importing the engine alone does not pull them in.

```
anti_recency          belief_cli            belief_engine         claude_memory
communities           cross_tier_bridges    decision_units        embed_resolver
embedding_upgrade*    gap_mapping           governance            hybrid_retrieval
kind_taxonomy         least_privilege       memory_write          monthly_self_eval
persona_lenses        ppr_recall            pre_cognitive_recall  procedural_memory
quickening            qwen_reranker*        recall_diversity      recall_explain
sleep_loop            temporal_edges        temporal_rerank*      trust_tier
trust_tier_cli        void_render           voidloom_approvals    voidloom_banner
voidloom_device*      warden
```

Reachable lazily but **not** on a plain recall — restore them too, or accept
that four capabilities are absent rather than broken:
`calibration_memory`, `model_garden`, `provenance_graph`, `whisper_network`.

## 2. Models — the largest single dependency

Recall loads **two** models, not one. The reranker is the one people forget.

| role | id | dim | cache bytes |
|---|---|---|---|
| embedder | `jinaai/jina-embeddings-v5-text-small` | 1024 | 1,369,724,029 |
| reranker (default) | `Qwen/Qwen3-Reranker-0.6B` | — | 1,207,489,163 |
| reranker (fallback) | `BAAI/bge-reranker-v2-m3` | — | 2,293,259,813 |
| light tier | `nomic-ai/nomic-embed-text-v1.5` | 768 | 548,033,496 |

Which tier is used is read from the brain itself
(`metadata.active_embedding_table`), not from config. Cache lives under
`~/.cache/huggingface/hub/`, plus a second location people miss:
`~/.cache/huggingface/modules/transformers_modules/`, where `trust_remote_code`
compiles the embedder's custom code.

### ⚠ A recall makes a network call

`qwen_reranker.load_model` calls `from_pretrained` **without
`local_files_only`**, unlike the Jina and bge paths which both honour the
offline flag. A restored brain on an air-gapped host reaches out to the model
CDN on **every recall** and fails there. Restore the cache, or set the reranker
to the fallback, or accept the outbound call.

## 3. SQL — zero opened, 27 shipped, one missing

No `.sql` file is opened during a recall: the migration `Path()` constants are
built at import (constructing a path is not opening it) and read only on
`init`, `migrate`, or self-heal. Warden's migration SQL is *embedded in the
Python*, not on disk.

**But the tree ships 27 `migration_*.sql` and the code names 21, and one of
those is absent:**

```
REFERENCED BUT ABSENT:  migration_028_communities.sql
                        (named at communities.py:169)
```

`communities.py` falls back to embedded SQL, so the gap **degrades silently** —
nothing fails, and nothing says the canonical file is missing. Seven further
migrations are present but never referenced by name; they are applied by other
means or not at all.

## 4. Sidecars — small files with large consequences

| path | bytes | why it matters |
|---|---|---|
| `<env-dir>/wizard-library-env` | 1,696 | **the one that bites.** Declares the GPU device. Omit it and `voidloom_device` returns the package-safe default, device **0** — which on a two-GPU workstation may be the display card |
| `~/.cache/huggingface/token` | 37 | opened on every model load |
| `<env-dir>/thinker/` (mode 700) | — | **the overnight thinker's only credential home**: `claude.env` (the Claude one-year token), `kimi.env` (a Kimi Code key) or `codex/` (the brain's own Codex sign-in). Absent = the thinking organs take their heuristic path, logged, never raised. Restore it with the brain, or run `voidloom thinker login <claude\|kimi\|codex>` again |
| `<brain>/THINKER_RECORD` | — | which model the owner chose and when it was proven (sha256-sealed; a hand-edited copy is refused on read). Restore it beside the database, then run `voidloom thinker status` |

**One Claude-token home, not three.** Before the thinker had its own folder, the Claude
token could sit in the env file (a `CLAUDE_CODE_OAUTH_TOKEN=` line outside the managed
block), in `<env-dir>/claude-oauth-token`, or both. Both are still READ, flagged *legacy*,
so an older restore keeps working; `voidloom thinker migrate` copies the token into
`<env-dir>/thinker/claude.env` (it refuses if the two old places disagree), after which
the old line and file can be deleted by hand. A restore needs only `thinker/`; the one step
that recreates each credential is in [`OVERNIGHT_THINKER_SETUP.md`](OVERNIGHT_THINKER_SETUP.md).

That first row is the honest lesson of this whole page: a **1.7 KB text file**
is the difference between a restored brain and one that evicts the operator's
desktop the first time it runs.

## 5. Environment

Python 3.10.9. Seven direct third-party imports: `torch==2.6.0+cu124`,
`transformers==4.57.6`, `sentence-transformers==4.1.0`, `numpy==1.23.5`,
`huggingface_hub==0.36.2`, `sqlite-vec==0.1.6`, `filelock==3.25.0`.

`sqlite-vec` ships a **loadable native extension** (`vec0.so`, 147,984 bytes).
Without it vector search is dead — and `requirements.txt` records the package
name, not the CUDA runtime/driver pairing that `torch 2.6.0+cu124` implies.

## 6. Residual — what this manifest still misses

**It was derived from ONE recall, and the dependency it hides is exactly the
kind you cannot afford to hide.** `cross_tier_bridges` loads on every recall,
but the second brain it can open is opened *only when a bridge row matches the
result set*. The probe query matched none, so the audit hook recorded zero
opens — and a purely mechanical derivation would omit that entire second
database. A different query opens it. **One execution is not a closure over the
input space.** Deriving this properly means running a corpus of queries, or
reading `cross_tier_bridges` for its conditional edges.

Three smaller ones:

* The manifest records package *labels*, not the environment. The CUDA
  runtime/driver pairing behind `torch 2.6.0+cu124` is unrecorded, and
  `numpy==1.23.5` is an old pin that may not resolve on a new interpreter.
* `igraph` and `leidenalg` are declared dependencies that are **absent**.
* Restoring modules + DB alone yields a brain that must re-download ~2.6 GB
  before its first recall — and on an air-gapped host, one that cannot.

## Minimum restore checklist

1. The 34 modules above, in one flat directory.
2. `claude_memory.db` — the brain itself.
3. `schema.sql` + the 27 `migration_*.sql` (accepting that 028 is missing).
4. `~/.cache/huggingface/hub/` **and** `.../modules/transformers_modules/`.
5. The env-dir sidecars — above all the one declaring the GPU device — and the
   thinker's `thinker/` folder, with `THINKER_RECORD` beside the brain.
6. The seven pinned third-party packages, including `vec0.so`.
7. Then **prove it**: stand it up in a fresh directory and make it store *and*
   recall. A `PRAGMA integrity_check` has tested the file, not the restore.
