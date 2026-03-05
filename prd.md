# PRD: Claude Mother Brain — MCP Server

## Overview

A self-hosted MCP (Model Context Protocol) server that gives Claude persistent, structured memory across conversations. Acts as a "second brain" — storing facts, summaries, events, obligations, relationships, and episodic history, retrievable via semantic search and structured queries.

Deployed on a home server via Docker Compose, managed via Portainer.

---

## Goals

- Claude can recall facts about the user's life, projects, people, systems, and obligations without being re-briefed each session
- Memory is self-maintaining: Claude writes and updates its own summaries as a side effect of learning things
- Retrieval is fast and graceful — semantic search means Claude never needs to know exact keys
- Fully local: embeddings via Ollama, storage in PostgreSQL, only external call is Anthropic API for summary generation
- Deployable as a Docker stack, manageable via Portainer

---

## Non-Goals

- Multi-user support (single user only)
- A UI (Portainer is the management interface; pgAdmin handles DB inspection)
- Real-time sync with external calendar/task systems (future scope)

---

## Tech Stack

| Component | Technology |
|---|---|
| MCP Server | Python 3.11+, `fastmcp` library |
| Database | PostgreSQL 16 + `pgvector` extension |
| Embeddings | `nomic-embed-text` via Ollama REST API |
| Summary LLM | Anthropic API, `claude-haiku-4-5` model |
| Deployment | Docker Compose |
| Container Management | Portainer (existing, on host) |

---

## System Architecture

### Three-Layer Memory Model

```
┌─────────────────────────────────────┐
│         WORKING MEMORY              │  Hot cache: recently active entities
│   Checked first on every recall     │  Zero-cost lookup, touch-weighted
└──────────────┬──────────────────────┘
               │ miss
┌──────────────▼──────────────────────┐
│         SUMMARY LAYER               │  pgvector cosine search
│   One summary per entity            │  LLM-written, always current
│   Dense natural language            │  Points down to fact rows
└──────────────┬──────────────────────┘
               │ drill down
┌──────────────▼──────────────────────┐
│           FACT LAYER                │  Atomic, timestamped, sourced
│   The actual details                │  Soft-delete only, confidence-scored
│   Keyed to entities                 │  Append-mostly
└─────────────────────────────────────┘
```

Episodes (conversation summaries) float alongside as a temporal log.

---

## Database Schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────
-- ENTITIES: the "things" that have memory
-- ─────────────────────────────────────────
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

-- ─────────────────────────────────────────
-- FACTS: atomic knowledge, append-mostly
-- ─────────────────────────────────────────
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

-- ─────────────────────────────────────────
-- SUMMARIES: LLM-written, vector-indexed
-- ─────────────────────────────────────────
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

CREATE INDEX idx_summaries_embedding ON summaries
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ─────────────────────────────────────────
-- EPISODES: conversation-level temporal log
-- ─────────────────────────────────────────
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

CREATE INDEX idx_episodes_embedding ON episodes
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
CREATE INDEX idx_episodes_time ON episodes(occurred_at DESC);

-- ─────────────────────────────────────────
-- WORKING MEMORY: hot cache
-- ─────────────────────────────────────────
CREATE TABLE working_memory (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id    UUID NOT NULL UNIQUE REFERENCES entities(id),
    reason       TEXT,
    touches      INT DEFAULT 0,
    last_touched TIMESTAMPTZ DEFAULT NOW(),
    activated_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at   TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

-- ─────────────────────────────────────────
-- EVENTS: dates that matter
-- ─────────────────────────────────────────
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

-- ─────────────────────────────────────────
-- OBLIGATIONS: actionable commitments
-- ─────────────────────────────────────────
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

-- ─────────────────────────────────────────
-- GOALS: short/medium/long term intentions
-- ─────────────────────────────────────────
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

-- ─────────────────────────────────────────
-- RELATIONSHIPS: richer than flat entity rows
-- ─────────────────────────────────────────
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

-- ─────────────────────────────────────────
-- REFERENCES: things worth remembering
-- ─────────────────────────────────────────
CREATE TABLE references (
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
```

---

## MCP Tool Surface

### Read Tools

**`recall(query: str, limit: int = 5) -> RecallResult`**
Primary retrieval. Flow:
1. Check working_memory for active matching entries (zero-cost)
2. Embed query → cosine search on summaries
3. Return top N summaries with entity metadata and active obligation/event counts
4. Increment touches on all returned entities and summaries

**`get_facts(entity_name: str, category: str = None, include_expired: bool = False) -> list[Fact]`**
Direct fact retrieval. Resolves entity by name or alias. Returns active facts, optionally by category. Increments touches.

**`get_working_memory() -> list[WorkingMemoryEntry]`**
Returns non-expired working memory entries, sorted by touches desc. Called at conversation start.

**`get_upcoming(days: int = 14) -> UpcomingResult`**
Returns events and active obligations due within N days. Sorted by date ascending.

**`get_obligations(status: str = 'active', priority: int = None) -> list[Obligation]`**
Returns obligations filtered by status and optionally priority.

**`get_goals(horizon: str = None, status: str = 'active') -> list[Goal]`**
Returns goals, optionally filtered by horizon.

**`recent_episodes(n: int = 5) -> list[Episode]`**
Last N conversation summaries, newest first.

**`search_facts(query: str, limit: int = 10) -> list[Fact]`**
Full-text search across fact content. Fallback when entity is unknown.

**`get_relationship(entity_name: str) -> Relationship`**
Returns the rich relationship record for a named person/org entity.

### Write Tools

**`remember(entity_name: str, content: str, category: str, confidence: float = 1.0, source: str = 'conversation')`**
Primary write tool. Deduplication flow:
1. Resolve entity: exact name match → alias match → semantic similarity check (if best match > 0.88, use existing)
2. Check for duplicate fact: embed content, cosine search existing facts for entity, if > 0.90 similarity invalidate old and replace
3. Write new fact
4. Trigger summary regeneration for entity

**`upsert_entity(name: str, type: str, aliases: list[str] = None) -> Entity`**
Create or update entity. Checks for near-duplicate names before creating.

**`invalidate_fact(fact_id: str, reason: str = None)`**
Soft-delete: sets valid_until = NOW(). Never hard deletes.

**`merge_entities(keep_id: str, discard_id: str)`**
Merge duplicate entities: repoints all facts, relationships, and obligations from discard to keep, copies aliases, deletes discard entity.

**`add_event(title: str, event_date: str, category: str, description: str = None, recurrence: str = 'none', entity_names: list[str] = None)`**
Add a date to the events table.

**`add_obligation(title: str, description: str = None, priority: int = 2, due_date: str = None, entity_names: list[str] = None)`**
Add an actionable commitment.

**`update_obligation(obligation_id: str, status: str = None, priority: int = None, due_date: str = None)`**
Update obligation status or metadata.

**`add_goal(title: str, horizon: str, description: str = None, parent_title: str = None, entity_names: list[str] = None)`**
Add a goal.

**`upsert_relationship(entity_name: str, relationship: str, context: str = None, notes: str = None, cadence: str = None)`**
Create or update a relationship record.

**`log_episode(title: str, summary: str, entity_names: list[str] = None)`**
Log a conversation summary as an episode. Called at the end of significant sessions.

**`activate(entity_name: str, reason: str = None, days: int = 7)`**
Push an entity into working memory with an expiry.

---

## Summary Regeneration

On every `remember()` call, after writing the fact:

1. Fetch all current facts (`valid_until IS NULL`) for the entity
2. POST to Anthropic API (`claude-haiku-4-5`):

```
System: You are maintaining a persistent memory system. 
        Respond only with the summary text, no preamble.

User: Generate a dense, specific summary of everything known about 
      '{entity_name}' (type: {entity_type}) based on these facts.
      Write it as a briefing for someone who needs to get up to speed 
      instantly. Include all concrete details, numbers, statuses, and 
      decisions. Be specific, not vague.
      
      Facts:
      {fact_list}
```

3. Embed the resulting summary text via Ollama (`nomic-embed-text`)
4. Upsert to `summaries` table

---

## Deduplication Logic

### Entity Deduplication
Before creating any entity:
1. Exact match on `name` (case-insensitive)
2. Check if name exists in any entity's `aliases` array
3. Embed the candidate name, cosine search entity summaries — if best score > 0.88, confirm with a disambiguation call to the LLM before deciding whether to merge
4. If new: create entity, log to episode

### Fact Deduplication
Before inserting a fact for an entity:
1. Embed the new fact content
2. Cosine search existing active facts for that entity
3. If best match > 0.90: invalidate the old fact, insert the new one (effectively an update)
4. If 0.75–0.90: insert both but flag the new fact with a note that a similar fact exists

---

## Working Memory Promotion

Auto-promote to working memory when:
- Entity appears in top 2 of `recall()` results more than once in a session
- Entity is linked to an obligation due within 72 hours
- Entity is linked to an event within 48 hours

Auto-expire: checked on `get_working_memory()` call, expired rows removed.

---

## Project Structure

```
mother-brain/
├── docker-compose.yml
├── .env.example
├── README.md
├── db/
│   ├── init.sql              # Full schema
│   └── seed.sql              # Optional seed data
├── mcp/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py             # MCP server entrypoint
│   ├── config.py             # Env var loading
│   ├── db.py                 # Async DB connection pool
│   ├── embeddings.py         # Ollama embedding client
│   ├── llm.py                # Anthropic summary generation client
│   ├── dedup.py              # Deduplication logic
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── read.py           # All read tools
│   │   └── write.py          # All write tools
│   └── models/
│       ├── __init__.py
│       └── schemas.py        # Pydantic models for all DB types
└── portainer/
    └── stack.yml             # Portainer-ready stack definition
```

---

## Environment Variables

```env
POSTGRES_HOST=memory-postgres
POSTGRES_PORT=5432
POSTGRES_DB=memory_brain
POSTGRES_USER=memory
POSTGRES_PASSWORD=changeme

OLLAMA_MODEL=nomic-embed-text

ANTHROPIC_API_KEY=sk-ant-...

MCP_SERVER_PORT=8765
MCP_LOG_LEVEL=INFO
```

---

## Docker Compose

```yaml
version: "3.9"

services:
  memory-postgres:
    image: pgvector/pgvector:pg16
    container_name: memory-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - memory_pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/01-init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5

  ollama:
    image: ollama/ollama:latest
    container_name: memory-ollama
    restart: unless-stopped
    volumes:
      - memory_ollama_data:/root/.ollama
    ports:
      - "11435:11434"
    healthcheck:
      test: ["CMD-SHELL", "ollama list || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s

  ollama-init:
    image: ollama/ollama:latest
    container_name: memory-ollama-init
    depends_on:
      ollama:
        condition: service_healthy
    environment:
      - OLLAMA_HOST=http://ollama:11434
    entrypoint: ["ollama", "pull", "nomic-embed-text"]
    restart: "no"

  memory-mcp:
    build: ./mcp
    container_name: memory-mcp
    restart: unless-stopped
    depends_on:
      memory-postgres:
        condition: service_healthy
      ollama:
        condition: service_healthy
    environment:
      - POSTGRES_HOST=memory-postgres
      - POSTGRES_PORT=${POSTGRES_PORT}
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - OLLAMA_URL=http://ollama:11434
      - OLLAMA_MODEL=${OLLAMA_MODEL}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - MCP_SERVER_PORT=${MCP_SERVER_PORT}
      - MCP_LOG_LEVEL=${MCP_LOG_LEVEL}
    ports:
      - "${MCP_SERVER_PORT}:${MCP_SERVER_PORT}"
    labels:
      - "com.docker.compose.project=mother-brain"

volumes:
  memory_pgdata:
  memory_ollama_data:
```

Ollama runs as part of this stack. The `ollama-init` service pulls `nomic-embed-text` automatically on first boot and exits. The MCP server waits for Ollama to be healthy before starting. The port `11435` is exposed on the host (offset from 11434 to avoid conflicts with any host-level Ollama instance).

---

## Portainer Deployment

The `portainer/stack.yml` file is a copy of `docker-compose.yml` with absolute paths and no build context — uses a pre-built image or builds inline. Deploy via Portainer → Stacks → Add Stack → Upload.

The MCP server exposes on port `8765` by default. Add to Claude's MCP config pointing at `http://<server-ip>:8765`.

---

## Acceptance Criteria

- [ ] `docker compose up` brings up postgres and mcp server cleanly
- [ ] All 9 read tools return correct data with touches incremented
- [ ] `remember()` deduplicates entities and facts correctly
- [ ] `remember()` triggers summary regeneration and updates embedding
- [ ] `recall()` returns semantically relevant results for a natural language query
- [ ] `get_upcoming()` returns events and obligations sorted by date
- [ ] `merge_entities()` correctly migrates all linked data
- [ ] Working memory auto-expires stale entries
- [ ] All DB writes are idempotent where appropriate
- [ ] MCP server survives postgres restart (reconnects cleanly)
- [ ] Stack deploys via Portainer without modification
