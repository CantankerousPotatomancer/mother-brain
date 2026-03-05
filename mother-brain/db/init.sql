-- Mother Brain: Full Schema
-- Extensions must come first
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ENTITIES: the "things" that have memory
CREATE TABLE entities (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         TEXT NOT NULL UNIQUE,
    type         TEXT NOT NULL CHECK (type IN (
                     'self', 'project', 'person', 'system',
                     'organization', 'concept', 'reference'
                 )),
    aliases      TEXT[] DEFAULT '{}',
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- FACTS: atomic knowledge, append-mostly
CREATE TABLE facts (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id    UUID NOT NULL REFERENCES entities(id),
    content      TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN (
                     'status', 'decision', 'preference', 'technical',
                     'personal', 'relationship', 'financial', 'goal', 'other'
                 )),
    confidence   FLOAT DEFAULT 1.0 CHECK (confidence BETWEEN 0.0 AND 1.0),
    valid_from   TIMESTAMPTZ DEFAULT NOW(),
    valid_until  TIMESTAMPTZ,
    source       TEXT DEFAULT 'conversation' CHECK (source IN (
                     'conversation', 'user_stated', 'inferred', 'system'
                 )),
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_facts_entity ON facts(entity_id);
CREATE INDEX idx_facts_active ON facts(entity_id) WHERE valid_until IS NULL;

-- SUMMARIES: LLM-written, vector-indexed
CREATE TABLE summaries (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id    UUID NOT NULL UNIQUE REFERENCES entities(id),
    content      TEXT NOT NULL,
    embedding    VECTOR(768),
    fact_count   INT DEFAULT 0,
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Note: ivfflat index requires rows to exist before creation.
-- We use exact search (no index) until the table has enough rows,
-- then the index can be created manually:
-- CREATE INDEX idx_summaries_embedding ON summaries
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- EPISODES: conversation-level temporal log
CREATE TABLE episodes (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    embedding   VECTOR(768),
    entity_ids  UUID[] DEFAULT '{}',
    touches     INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    occurred_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_episodes_time ON episodes(occurred_at DESC);

-- WORKING MEMORY: hot cache
CREATE TABLE working_memory (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id    UUID NOT NULL UNIQUE REFERENCES entities(id),
    reason       TEXT,
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    activated_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

-- EVENTS: dates that matter
CREATE TABLE events (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title        TEXT NOT NULL,
    description  TEXT,
    entity_ids   UUID[] DEFAULT '{}',
    event_date   TIMESTAMPTZ NOT NULL,
    recurrence   TEXT CHECK (recurrence IN (
                     'none', 'daily', 'weekly', 'monthly', 'yearly'
                 )) DEFAULT 'none',
    category     TEXT CHECK (category IN (
                     'deadline', 'birthday', 'anniversary', 'appointment',
                     'release', 'reminder', 'other'
                 )) DEFAULT 'other',
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_events_date ON events(event_date ASC);

-- OBLIGATIONS: actionable commitments
CREATE TABLE obligations (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title        TEXT NOT NULL,
    description  TEXT,
    entity_ids   UUID[] DEFAULT '{}',
    status       TEXT CHECK (status IN (
                     'active', 'completed', 'deferred', 'dropped'
                 )) DEFAULT 'active',
    priority     INT DEFAULT 2 CHECK (priority BETWEEN 1 AND 5),
    due_date     TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_obligations_active ON obligations(due_date ASC)
    WHERE status = 'active';

-- GOALS: short/medium/long term intentions
CREATE TABLE goals (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title        TEXT NOT NULL,
    description  TEXT,
    entity_ids   UUID[] DEFAULT '{}',
    horizon      TEXT CHECK (horizon IN (
                     'immediate', 'short', 'medium', 'long', 'life'
                 )) NOT NULL,
    status       TEXT CHECK (status IN (
                     'active', 'achieved', 'abandoned', 'deferred'
                 )) DEFAULT 'active',
    parent_id    UUID REFERENCES goals(id),
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- RELATIONSHIPS: richer than flat entity rows
CREATE TABLE relationships (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id       UUID NOT NULL REFERENCES entities(id),
    relationship    TEXT NOT NULL,
    context         TEXT,
    shared_projects UUID[] DEFAULT '{}',
    cadence         TEXT,
    notes           TEXT,
    touches         INT DEFAULT 0,
    last_touched    TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- REFERENCES: things worth remembering
CREATE TABLE "references" (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title        TEXT NOT NULL,
    url          TEXT,
    description  TEXT,
    category     TEXT CHECK (category IN (
                     'book', 'article', 'tool', 'link', 'video', 'other'
                 )) DEFAULT 'other',
    entity_ids   UUID[] DEFAULT '{}',
    status       TEXT CHECK (status IN (
                     'unread', 'reading', 'done', 'archived'
                 )) DEFAULT 'unread',
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
