-- Claude Code Vectorized Memory Database
-- Schema v1.0
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Core memory storage
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    summary TEXT,
    category TEXT NOT NULL,
    importance REAL DEFAULT 0.5,
    source_project TEXT,
    source_context TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    last_accessed TEXT DEFAULT (datetime('now')),
    access_count INTEGER DEFAULT 0,
    is_archived INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(source_project);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_accessed ON memories(last_accessed);
CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(is_archived);

-- Tags for flexible categorization
CREATE TABLE IF NOT EXISTS memory_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    UNIQUE(memory_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_tags_memory ON memory_tags(memory_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON memory_tags(tag);

-- Memory links (bidirectional relationships)
CREATE TABLE IF NOT EXISTS memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL,
    strength REAL DEFAULT 0.5,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id, link_type)
);

CREATE INDEX IF NOT EXISTS idx_links_source ON memory_links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_target ON memory_links(target_id);

-- Access log for decay/reinforcement tracking
CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    access_type TEXT NOT NULL,
    query_text TEXT,
    relevance_score REAL,
    accessed_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_access_memory ON access_log(memory_id);

-- Metadata
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
