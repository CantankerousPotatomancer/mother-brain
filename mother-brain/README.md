# Claude Mother Brain — MCP Server

A self-hosted MCP server that gives Claude persistent, structured memory across conversations. Stores facts, summaries, events, obligations, relationships, and episodic history, retrievable via semantic search.

## Prerequisites

- Docker and Docker Compose
- An Anthropic API key (for summary generation)

That's it. Ollama and the embedding model are bundled in the stack.

## Quick Start

1. **Clone and configure:**

```bash
cd mother-brain
cp .env.example .env
```

Edit `.env` and set your `ANTHROPIC_API_KEY` and a secure `POSTGRES_PASSWORD`.

2. **Start the stack:**

```bash
docker compose up -d
```

On first boot, the `ollama-init` container will automatically pull the `nomic-embed-text` embedding model. This may take a minute or two depending on your connection.

3. **Verify:**

```bash
docker compose logs -f memory-mcp
```

You should see `Mother Brain MCP server ready` once everything is connected.

## Adding to Claude's MCP Config

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

Replace `<your-server-ip>` with your server's IP address or hostname.

## Available Tools

### Read Tools
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

### Write Tools
| Tool | Description |
|------|-------------|
| `remember(entity_name, content, category, confidence, source)` | Store a fact with dedup |
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

## Portainer Deployment

Portainer is a great way to manage Mother Brain — you get stack health monitoring, per-container logs, and one-click start/stop from the UI. There's one gotcha to be aware of before you deploy.

### The Bind Mount Problem

The `portainer/stack.yml` references the SQL init scripts using relative paths:

```yaml
- ./db/init.sql:/docker-entrypoint-initdb.d/01-init.sql
- ./db/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql
```

Portainer stacks don't have a working directory, so relative paths like `./db/` won't resolve — the database will start without being initialized and the MCP server will fail to connect.

The fix is straightforward: **replace the relative paths with absolute paths** pointing to wherever you cloned the repo on your host.

### Step-by-Step

1. Clone the repo to your server:

```bash
git clone git@github.com:CantankerousPotatomancer/mother-brain.git ~/repos/mother-brain
```

2. Open `portainer/stack.yml` in a text editor and replace the two bind mount lines under `memory-postgres` with absolute paths. For example, if you cloned to `/home/youruser/repos/mother-brain`:

```yaml
volumes:
  - memory_pgdata:/var/lib/postgresql/data
  - /home/youruser/repos/mother-brain/mother-brain/db/init.sql:/docker-entrypoint-initdb.d/01-init.sql
  - /home/youruser/repos/mother-brain/mother-brain/db/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql
```

3. In Portainer, go to **Stacks** → **Add Stack**.

4. Paste the contents of your edited `stack.yml` into the web editor.

5. Scroll down to **Environment variables** and add the following:

| Variable | Value |
|----------|-------|
| `POSTGRES_DB` | `memory_brain` |
| `POSTGRES_USER` | `memory` |
| `POSTGRES_PASSWORD` | *(choose a secure password)* |
| `POSTGRES_PORT` | `5432` |
| `OLLAMA_MODEL` | `nomic-embed-text` |
| `ANTHROPIC_API_KEY` | *(your Anthropic API key)* |
| `MCP_SERVER_PORT` | `8765` |
| `MCP_LOG_LEVEL` | `INFO` |

6. Click **Deploy the stack**.

### First Boot

On first boot, the `ollama-init` container pulls the `nomic-embed-text` embedding model (~270 MB). This is a one-shot container — it will show as **stopped** in Portainer once the pull completes, which is expected. The `memory-mcp` container won't start until both postgres and ollama are healthy, so give it a minute or two.

Check that everything came up cleanly by clicking into the `memory-mcp` container logs in Portainer and looking for:

```
Mother Brain MCP server ready
```

### Subsequent Restarts

The ollama model is stored in the `memory_ollama_data` volume, so it does not need to be re-downloaded on restart. The `ollama-init` container is `restart: "no"` and will remain in a stopped state — this is normal and harmless.

## Inspecting the Database with pgAdmin

Connect pgAdmin to the PostgreSQL instance:

| Setting | Value |
|---------|-------|
| Host | `<your-server-ip>` |
| Port | `5432` (or expose it in docker-compose) |
| Database | `memory_brain` |
| Username | `memory` |
| Password | (your POSTGRES_PASSWORD) |

Note: By default, the PostgreSQL port is not exposed to the host. To expose it, add `ports: ["5432:5432"]` to the `memory-postgres` service in `docker-compose.yml`.

## Troubleshooting

**pgvector extension missing:**
The `pgvector/pgvector:pg16` image ships with pgvector pre-installed. If you see extension errors, make sure you're using this image and not a plain `postgres:16`.

**Port conflicts:**
The MCP server defaults to port `8765` and Ollama to `11435` (host side). Change `MCP_SERVER_PORT` in `.env` if needed. The Ollama host port is offset from the default `11434` to avoid conflicts with any host-level Ollama instance.

**ollama-init failing:**
The init container needs network access to download the model. Check `docker compose logs ollama-init`. It's a one-shot container that exits after pulling — a non-zero exit code means the pull failed. Restart it with `docker compose restart ollama-init`.

**MCP server can't connect to postgres:**
The MCP server waits for postgres to be healthy before starting. If it keeps restarting, check postgres logs: `docker compose logs memory-postgres`.
