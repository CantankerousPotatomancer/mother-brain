# Mother Brain

A self-hosted MCP server that gives Claude persistent, structured memory across conversations. Stores facts, summaries, events, obligations, relationships, and episodic history, retrievable via semantic search.

Built on PostgreSQL + pgvector for storage, Ollama for local embeddings, and the Anthropic API for summary generation. Connects to Claude via **Streamable HTTP** transport.

## Architecture

```
Claude <── Streamable HTTP ──> Mother Brain MCP Server
                                    │
                          ┌─────────┼─────────┐
                          │         │         │
                      PostgreSQL  Ollama   Anthropic API
                      + pgvector  (nomic-   (claude-haiku
                                  embed-    summary gen)
                                  text)
```

### Three-Layer Memory Model

| Layer | Purpose |
|-------|---------|
| **Working Memory** | Hot cache of recently active entities, checked first on every recall |
| **Summary Layer** | One LLM-written summary per entity, vector-indexed for semantic search |
| **Fact Layer** | Atomic, timestamped, sourced facts keyed to entities (append-mostly, soft-delete only) |

Episodes (conversation summaries) float alongside as a temporal log.

## Prerequisites

- Docker and Docker Compose
- An Anthropic API key (for summary generation)

Ollama and the `nomic-embed-text` embedding model are bundled in the stack and pulled automatically on first boot.

## Quick Start

```bash
cd mother-brain
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and a secure POSTGRES_PASSWORD

docker compose up -d
```

On first boot, the `ollama-init` container pulls the embedding model automatically. This may take a minute or two.

Verify the server is ready:

```bash
docker compose logs -f memory-mcp
# Look for: "Mother Brain MCP server ready"
```

## Connecting to Claude

Add this to your Claude MCP configuration:

```json
{
  "mcpServers": {
    "mother-brain": {
      "type": "streamable-http",
      "url": "http://<your-server-ip>:8765/mcp"
    }
  }
}
```

Replace `<your-server-ip>` with your server's IP address or hostname. The server uses **Streamable HTTP** as the MCP transport protocol.

## Available Tools

### Read Tools (9)

| Tool | Description |
|------|-------------|
| `recall(query, limit)` | Semantic search across all entity summaries |
| `get_facts(entity_name, category, include_expired)` | Get facts for an entity |
| `get_working_memory()` | Active hot-cache entries |
| `get_upcoming(days)` | Events and obligations due soon |
| `get_obligations(status, priority)` | Filtered obligations |
| `get_goals(horizon, status)` | Filtered goals |
| `recent_episodes(n)` | Recent conversation summaries |
| `search_facts(query, limit)` | Full-text search across facts |
| `get_relationship(entity_name)` | Relationship record for an entity |

### Write Tools (11)

| Tool | Description |
|------|-------------|
| `remember(entity_name, content, category, confidence, source)` | Store a fact with dedup + async summary regen |
| `upsert_entity(name, type, aliases)` | Create or update an entity |
| `invalidate_fact(fact_id, reason)` | Soft-delete a fact |
| `merge_entities(keep_id, discard_id)` | Merge duplicate entities |
| `add_event(title, event_date, category, ...)` | Add a date-based event |
| `add_obligation(title, description, priority, due_date, ...)` | Add a commitment |
| `update_obligation(obligation_id, status, priority, due_date)` | Update an obligation |
| `add_goal(title, horizon, description, parent_title, ...)` | Add a goal |
| `upsert_relationship(entity_name, relationship, ...)` | Create/update a relationship |
| `log_episode(title, summary, entity_names)` | Log a conversation summary |
| `activate(entity_name, reason, days)` | Push entity to working memory |

## Tech Stack

| Component | Technology |
|-----------|------------|
| MCP Server | Python 3.11+, `fastmcp` (Streamable HTTP transport) |
| Database | PostgreSQL 16 + `pgvector` extension |
| Embeddings | `nomic-embed-text` via Ollama (768-dim vectors) |
| Summary LLM | Anthropic API, `claude-haiku-4-5` |
| Deployment | Docker Compose |
| Management | Portainer (optional) |

## Project Structure

```
mother-brain/
├── docker-compose.yml
├── .env.example
├── README.md
├── test_basic.py
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

## Portainer Deployment

1. Open Portainer UI
2. Go to **Stacks** -> **Add Stack**
3. Upload `portainer/stack.yml` or paste its contents
4. Set environment variables (same as `.env`)
5. Deploy

## Inspecting the Database with pgAdmin

| Setting | Value |
|---------|-------|
| Host | `<your-server-ip>` |
| Port | `5432` (expose in docker-compose first) |
| Database | `memory_brain` |
| Username | `memory` |
| Password | (your POSTGRES_PASSWORD) |

By default, the PostgreSQL port is not exposed to the host. Add `ports: ["5432:5432"]` to the `memory-postgres` service to expose it.

## Troubleshooting

**pgvector extension missing:** Make sure you're using `pgvector/pgvector:pg16`, not plain `postgres:16`.

**Port conflicts:** MCP server defaults to `8765`, Ollama to `11435` (host side, offset from default `11434`). Change `MCP_SERVER_PORT` in `.env` if needed.

**ollama-init failing:** Needs network access to download the model. Check `docker compose logs ollama-init`. Restart with `docker compose restart ollama-init`.

**MCP server can't connect to postgres:** Check postgres health: `docker compose logs memory-postgres`.

## Running Tests

With the stack running:

```bash
cd mother-brain
python test_basic.py
```

This tests entity creation, fact storage, recall, obligations, and episode logging against the live stack via Streamable HTTP.
