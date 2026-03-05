# Tasks: Claude Memory Brain

Check off tasks as you complete them. Work top to bottom — each section depends on the previous.

---

## Phase 1: Repository Scaffold

- [ ] Create directory structure as specified in `agents.md`
- [ ] Create `.env.example` with all required variables and comments (no `OLLAMA_URL` — it's hardcoded as `http://ollama:11434` in compose)
- [ ] Create `docker-compose.yml` from the spec in `prd.md` including:
  - `memory-postgres` service
  - `ollama` service with healthcheck and `memory_ollama_data` volume
  - `ollama-init` one-shot service that pulls `nomic-embed-text` and exits
  - `memory-mcp` service that depends on both postgres and ollama being healthy
- [ ] Create `mcp/Dockerfile` — Python 3.11 slim base, install requirements, run `server.py`
- [ ] Create `mcp/requirements.txt`:
  - `fastmcp`
  - `asyncpg`
  - `httpx`
  - `pydantic`
  - `anthropic`
  - `python-dotenv`

---

## Phase 2: Database

- [ ] Write `db/init.sql` — full schema from `prd.md` in correct dependency order:
  - Extensions first (`vector`, `uuid-ossp`)
  - `entities`
  - `facts`
  - `summaries`
  - `episodes`
  - `working_memory`
  - `events`
  - `obligations`
  - `goals`
  - `relationships`
  - `references`
  - All indexes
- [ ] Write `db/seed.sql` — a single `self` entity for the user as a baseline (empty facts, no embedding yet)
- [ ] Verify `docker compose up` boots postgres cleanly and schema applies

---

## Phase 3: Core Modules

- [ ] Write `mcp/config.py` — load all env vars with `python-dotenv`, expose as typed config object, fail fast on missing required vars
- [ ] Write `mcp/db.py`:
  - `async init_pool()` — create asyncpg connection pool
  - `async get_pool()` — return existing pool
  - `async close_pool()` — cleanup
  - Helper: `async touch(pool, table, ids)` — increment touches + last_touched for a list of UUIDs in a given table
- [ ] Write `mcp/embeddings.py`:
  - `async embed(text: str) -> list[float] | None` — POST to Ollama, return vector or None on failure
  - `async embed_batch(texts: list[str]) -> list[list[float] | None]` — batch embedding
- [ ] Write `mcp/llm.py`:
  - `async generate_summary(entity_name: str, entity_type: str, facts: list[str]) -> str | None`
  - Calls `claude-haiku-4-5` with the prompt from `prd.md`
  - Returns summary string or None on failure
- [ ] Write `mcp/models/schemas.py` — Pydantic models for:
  - `Entity`, `Fact`, `Summary`, `Episode`, `WorkingMemoryEntry`
  - `Event`, `Obligation`, `Goal`, `Relationship`, `Reference`
  - `RecallResult` (summary + entity + active counts)
  - `UpcomingResult` (events list + obligations list)
  - All input models for tool parameters

---

## Phase 4: Deduplication

- [ ] Write `mcp/dedup.py`:
  - `async resolve_entity(pool, name: str) -> UUID | None`
    - Exact name match (case-insensitive)
    - Alias array match
    - Semantic similarity check against summaries (threshold 0.88)
    - Returns entity UUID if found, None if new
  - `async check_duplicate_fact(pool, entity_id: UUID, content: str, embedding: list[float]) -> DedupResult`
    - Cosine search existing active facts for entity
    - Returns: `{action: 'insert' | 'replace' | 'flag', existing_id: UUID | None, score: float}`
  - `async find_similar_entities(pool, name: str, embedding: list[float], threshold: float) -> list[EntityMatch]`
    - Used for entity dedup, returns candidates above threshold

---

## Phase 5: Read Tools

Implement all read tools in `mcp/tools/read.py`. Each tool must:
- Validate inputs
- Execute query
- Call `touch()` on returned rows
- Return Pydantic model or error string

- [ ] `recall(query, limit=5)`
- [ ] `get_facts(entity_name, category=None, include_expired=False)`
- [ ] `get_working_memory()`
  - Also expire stale entries (delete where `expires_at < NOW()`)
- [ ] `get_upcoming(days=14)`
- [ ] `get_obligations(status='active', priority=None)`
- [ ] `get_goals(horizon=None, status='active')`
- [ ] `recent_episodes(n=5)`
- [ ] `search_facts(query, limit=10)`
- [ ] `get_relationship(entity_name)`

---

## Phase 6: Write Tools

Implement all write tools in `mcp/tools/write.py`. Each tool must:
- Validate inputs
- Run dedup logic where applicable
- Execute write
- Return confirmation or error string

- [ ] `remember(entity_name, content, category, confidence=1.0, source='conversation')`
  - Resolve or create entity
  - Deduplicate fact
  - Write fact
  - Fire-and-forget async task: `generate_summary` → `embed` → upsert `summaries`
- [ ] `upsert_entity(name, type, aliases=None)`
- [ ] `invalidate_fact(fact_id, reason=None)`
- [ ] `merge_entities(keep_id, discard_id)`
  - UPDATE facts, relationships, obligations, goals, episodes SET entity_id/entity_ids
  - Copy aliases from discard to keep
  - Delete discard entity
- [ ] `add_event(title, event_date, category, description=None, recurrence='none', entity_names=None)`
- [ ] `add_obligation(title, description=None, priority=2, due_date=None, entity_names=None)`
- [ ] `update_obligation(obligation_id, status=None, priority=None, due_date=None)`
- [ ] `add_goal(title, horizon, description=None, parent_title=None, entity_names=None)`
- [ ] `upsert_relationship(entity_name, relationship, context=None, notes=None, cadence=None)`
- [ ] `log_episode(title, summary, entity_names=None)`
  - Write episode row
  - Embed summary
  - Update episode embedding
- [ ] `activate(entity_name, reason=None, days=7)`

---

## Phase 7: MCP Server Entrypoint

- [ ] Write `mcp/server.py`:
  - Initialize `fastmcp` app
  - On startup: init DB pool, verify postgres connection, log readiness
  - Register all tools from `tools/read.py` and `tools/write.py`
  - On shutdown: close DB pool cleanly
  - Expose on `MCP_SERVER_PORT`

---

## Phase 8: Working Memory Auto-Promotion

- [ ] In `recall()`: track entity hit frequency in-memory per session (module-level dict, keyed by entity_id)
- [ ] After each `recall()` call: if an entity has appeared in top 2 results more than once in this session, call `activate()` automatically
- [ ] In `get_working_memory()`: before returning results, run a query checking if any obligations are due within 72 hours or events within 48 hours — auto-activate their linked entities if not already active

---

## Phase 9: Portainer Stack

- [ ] Write `portainer/stack.yml`:
  - Same as `docker-compose.yml` but with `build:` replaced by an image reference (or keep build context with relative paths adjusted)
  - Add Portainer-compatible labels
  - Document in README how to deploy via Portainer UI (Stacks → Add Stack → Upload)

---

## Phase 10: Testing & Documentation

- [ ] Write `test_basic.py` as specified in `agents.md`
- [ ] Run `test_basic.py` against the live stack and fix any failures
- [ ] Write `README.md`:
  - [ ] Prerequisites section (Docker only — Ollama is bundled in the stack)
  - [ ] Environment setup (copy `.env.example`, fill in values)
  - [ ] Start command (`docker compose up -d`) — note that first boot pulls nomic-embed-text automatically, may take a minute
  - [ ] Claude MCP config JSON example
  - [ ] Portainer deployment steps
  - [ ] pgAdmin connection instructions
  - [ ] Troubleshooting section (pgvector extension missing, port conflicts, ollama-init failing)

---

## Final Checklist

- [ ] `docker compose up` starts cleanly
- [ ] Schema applied with all tables and indexes
- [ ] All 9 read tools work
- [ ] All 11 write tools work
- [ ] `remember()` triggers async summary regeneration
- [ ] `recall()` returns semantically relevant results
- [ ] Deduplication prevents duplicate entities and facts
- [ ] Touch tracking increments on all reads
- [ ] Working memory auto-expires and auto-promotes
- [ ] `test_basic.py` passes end-to-end
- [ ] `portainer/stack.yml` ready
- [ ] `README.md` complete
