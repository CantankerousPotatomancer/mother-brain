# Agent Instructions: Claude Mother Brain

## Your Mission

Build the complete `mother-brain` MCP server as specified in `prd.md`. This is a self-hosted memory system for Claude — a PostgreSQL-backed MCP server with semantic search, structured fact storage, and full lifecycle management for entities, facts, events, obligations, and goals.

When you are done, the user should be able to run `docker compose up` and have a fully working MCP server ready to connect to Claude.

---

## How to Work

- Read `prd.md` fully before writing any code.
- Read `tasks.md` and check off tasks as you complete them.
- Build incrementally: get the DB up, then the connection layer, then tools one group at a time.
- Test each layer before building the next. Do not write all the code and test at the end.
- When you are uncertain about a design decision not covered in `prd.md`, make the simpler choice and leave a `# TODO:` comment.
- Prefer explicit over clever. This codebase will be extended.
- Never hard-delete anything from the database. Always soft-delete.

---

## Key Constraints

### Database
- Use `pgvector/pgvector:pg16` Docker image — it ships with the pgvector extension pre-installed.
- Run `CREATE EXTENSION IF NOT EXISTS vector;` in `init.sql` before any table definitions.
- Every table must have a `touches INT DEFAULT 0` and `last_touched TIMESTAMPTZ DEFAULT NOW()` column.
- All primary keys are UUID, generated with `uuid_generate_v4()`.
- Use `TIMESTAMPTZ` not `TIMESTAMP` everywhere.
- No hard deletes. `valid_until` for facts, `status` for obligations/goals.

### MCP Server
- Use `fastmcp` as the MCP framework.
- Use `asyncpg` for database access (async connection pool).
- Use `httpx` for all HTTP calls (Ollama, Anthropic).
- All tool functions must be `async`.
- All tool inputs and outputs must use Pydantic models defined in `models/schemas.py`.
- Handle the case where Ollama is unavailable gracefully — log the error and continue without embedding (store NULL, exclude from vector search).
- Handle the case where Anthropic API is unavailable — log the error, skip summary regeneration, do not fail the write.

### Embeddings
- Ollama runs as part of this stack. `OLLAMA_URL` is always `http://ollama:11434` — hardcoded in compose, not an env var.
- Endpoint: `POST {OLLAMA_URL}/api/embeddings` with body `{"model": "nomic-embed-text", "prompt": "text"}`
- Response: `{"embedding": [float, ...]}`
- nomic-embed-text produces 768-dimensional vectors.
- Always embed the full summary content, not individual sentences.
- `nomic-embed-text` is pulled automatically by the `ollama-init` init container on first boot. Do not add manual pull instructions anywhere.

### Summary Regeneration
- Called inside `remember()` after writing the fact, as a background task (do not block the tool response).
- Use `claude-haiku-4-5` model via Anthropic API.
- System prompt: `"You are maintaining a persistent memory system. Respond only with the summary text, no preamble, no markdown."`
- User prompt: see `prd.md` Summary Regeneration section.
- After generating summary text, immediately embed it and upsert to `summaries` table.

### Deduplication
- This is critical. Do not skip it or stub it.
- See `prd.md` Deduplication Logic section for the exact flow.
- Implement in `dedup.py` as standalone async functions called from write tools.
- Thresholds: entity similarity > 0.88 = likely same entity. Fact similarity > 0.90 = update (invalidate old, insert new). Fact similarity 0.75–0.90 = insert but flag.

### Touch Tracking
- Every read operation that returns rows must increment `touches` and update `last_touched` on every returned row.
- Do this in a single UPDATE after the SELECT, not row-by-row.
- Touch tracking should never cause a read to fail — wrap in try/except, log errors, continue.

### Error Handling
- All tool functions must return meaningful error messages as strings if they fail, not raise exceptions to the MCP layer.
- Log all errors with enough context to debug (entity name, operation, error type).
- The MCP server must not crash on bad input — validate everything with Pydantic before hitting the DB.

---

## File Structure to Produce

```
mother-brain/
├── docker-compose.yml
├── .env.example
├── README.md
├── db/
│   ├── init.sql
│   └── seed.sql
├── mcp/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── server.py
│   ├── config.py
│   ├── db.py
│   ├── embeddings.py
│   ├── llm.py
│   ├── dedup.py
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── read.py
│   │   └── write.py
│   └── models/
│       ├── __init__.py
│       └── schemas.py
└── portainer/
    └── stack.yml
```

---

## Tool Implementation Checklist

### Read Tools (implement in `tools/read.py`)
- [ ] `recall(query, limit)` — working memory check → vector search summaries → return with touch increment
- [ ] `get_facts(entity_name, category, include_expired)` — resolve entity by name/alias, return facts
- [ ] `get_working_memory()` — return non-expired entries, expire stale, sort by touches
- [ ] `get_upcoming(days)` — events + obligations due within N days
- [ ] `get_obligations(status, priority)` — filtered obligations list
- [ ] `get_goals(horizon, status)` — filtered goals list
- [ ] `recent_episodes(n)` — last N episodes
- [ ] `search_facts(query, limit)` — full-text search across fact content
- [ ] `get_relationship(entity_name)` — relationship record for an entity

### Write Tools (implement in `tools/write.py`)
- [ ] `remember(entity_name, content, category, confidence, source)` — full dedup + write + async summary regen
- [ ] `upsert_entity(name, type, aliases)` — create or update with dedup check
- [ ] `invalidate_fact(fact_id, reason)` — soft delete
- [ ] `merge_entities(keep_id, discard_id)` — migrate all linked data
- [ ] `add_event(title, event_date, category, description, recurrence, entity_names)`
- [ ] `add_obligation(title, description, priority, due_date, entity_names)`
- [ ] `update_obligation(obligation_id, status, priority, due_date)`
- [ ] `add_goal(title, horizon, description, parent_title, entity_names)`
- [ ] `upsert_relationship(entity_name, relationship, context, notes, cadence)`
- [ ] `log_episode(title, summary, entity_names)` — write + embed episode
- [ ] `activate(entity_name, reason, days)` — push to working memory

---

## Testing

Write a `test_basic.py` script that:
1. Connects to the DB
2. Creates an entity via `upsert_entity`
3. Writes a fact via `remember`
4. Calls `recall` and verifies the entity comes back
5. Creates an obligation and verifies `get_upcoming` returns it
6. Logs an episode

Run this script to validate the stack before declaring done.

---

## README Requirements

The `README.md` must include:
- Prerequisites (Docker, Ollama with nomic-embed-text pulled)
- How to copy `.env.example` to `.env` and fill in values
- `docker compose up` command
- How to add to Claude's MCP config (with example JSON)
- How to deploy via Portainer (brief steps)
- How to connect pgAdmin to inspect the DB

---

## Definition of Done

- `docker compose up` starts cleanly with no errors
- All tools in the checklist above are implemented
- `test_basic.py` passes
- `README.md` is complete
- `portainer/stack.yml` is ready to deploy
- No TODO stubs in critical paths (dedup, touch tracking, summary regen)
