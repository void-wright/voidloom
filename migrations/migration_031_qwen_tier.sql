-- migration_031_qwen_tier.sql
--
-- The SHIPPED GPU embedding tier becomes Qwen3-Embedding-0.6B (apache-2.0).
--
-- WHY A THIRD TABLE RATHER THAN REUSING memory_embeddings_v2:
--   Qwen and jina are both 1024-dim -- they are finetunes of the same base model
--   (Qwen/Qwen3-0.6B-Base) at the same size. Dimensional compatibility is exactly
--   what makes reusing v2 dangerous: the tier resolver reverse-maps a TABLE NAME
--   back to a MODEL, so one table serving two models makes that lookup ambiguous.
--   A brain embedded with Qwen would be read as a jina brain, the installer would
--   fetch jina, and every recall would run one model's queries against the other
--   model's vectors -- same width, different space, no error anywhere. Giving Qwen
--   its own table keeps the mapping BIJECTIVE, so the reverse lookup cannot lie.
--
-- Additive and idempotent. Creates nothing on a brain that never selects Qwen;
-- Drew's own brain stays on jina/v2 and is untouched by applying this.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings_v3 USING vec0(
    memory_id INTEGER PRIMARY KEY,
    embedding float[1024]
);
