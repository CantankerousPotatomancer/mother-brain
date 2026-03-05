"""Basic integration test for Mother Brain MCP server.

Run against a live stack: python test_basic.py
Requires the stack to be running (docker compose up).
"""
import asyncio
import httpx
import json
import sys

MCP_URL = "http://localhost:8765"


async def call_tool(client: httpx.AsyncClient, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool via the streamable HTTP transport."""
    # Initialize session
    resp = await client.post(
        f"{MCP_URL}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        },
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )

    session_id = resp.headers.get("mcp-session-id")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id

    # Send initialized notification
    await client.post(
        f"{MCP_URL}/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
        headers=headers,
    )

    # Call tool
    resp = await client.post(
        f"{MCP_URL}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        headers=headers,
    )

    # Handle Streamable HTTP response
    if "text/event-stream" in resp.headers.get("content-type", ""):
        result = None
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "result" in data:
                    result = data["result"]
        return result
    else:
        data = resp.json()
        return data.get("result", data)


async def main():
    print("=== Mother Brain Integration Test ===\n")
    errors = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Create an entity
        print("1. Creating entity 'TestProject'...")
        try:
            result = await call_tool(client, "upsert_entity", {
                "name": "TestProject",
                "type": "project",
                "aliases": ["tp", "test-proj"],
            })
            print(f"   Result: {result}")
            content = result.get("content", [{}]) if isinstance(result, dict) else [{}]
            text = content[0].get("text", "") if content else ""
            if "error" in text.lower() and "not" not in text.lower():
                errors.append(f"upsert_entity failed: {text}")
        except Exception as e:
            errors.append(f"upsert_entity exception: {e}")
            print(f"   Error: {e}")

        # 2. Write a fact
        print("\n2. Remembering a fact about TestProject...")
        try:
            result = await call_tool(client, "remember", {
                "entity_name": "TestProject",
                "content": "TestProject uses Python 3.11 and PostgreSQL 16 with pgvector",
                "category": "technical",
                "source": "user_stated",
            })
            print(f"   Result: {result}")
        except Exception as e:
            errors.append(f"remember exception: {e}")
            print(f"   Error: {e}")

        # Give summary regen a moment
        await asyncio.sleep(2)

        # 3. Recall
        print("\n3. Recalling 'Python PostgreSQL project'...")
        try:
            result = await call_tool(client, "recall", {
                "query": "Python PostgreSQL project",
                "limit": 3,
            })
            print(f"   Result: {json.dumps(result, indent=2, default=str)[:500]}")
        except Exception as e:
            errors.append(f"recall exception: {e}")
            print(f"   Error: {e}")

        # 4. Add obligation
        print("\n4. Adding an obligation...")
        try:
            result = await call_tool(client, "add_obligation", {
                "title": "Write tests for TestProject",
                "description": "Integration and unit tests needed",
                "priority": 1,
                "due_date": "2026-03-10T00:00:00",
                "entity_names": ["TestProject"],
            })
            print(f"   Result: {result}")
        except Exception as e:
            errors.append(f"add_obligation exception: {e}")
            print(f"   Error: {e}")

        # 5. Get upcoming
        print("\n5. Getting upcoming events and obligations...")
        try:
            result = await call_tool(client, "get_upcoming", {"days": 30})
            print(f"   Result: {json.dumps(result, indent=2, default=str)[:500]}")
        except Exception as e:
            errors.append(f"get_upcoming exception: {e}")
            print(f"   Error: {e}")

        # 6. Log episode
        print("\n6. Logging an episode...")
        try:
            result = await call_tool(client, "log_episode", {
                "title": "Integration test session",
                "summary": "Ran basic integration tests against the Mother Brain MCP server. Tested entity creation, fact storage, recall, obligations, and episode logging.",
                "entity_names": ["TestProject"],
            })
            print(f"   Result: {result}")
        except Exception as e:
            errors.append(f"log_episode exception: {e}")
            print(f"   Error: {e}")

    print("\n=== Results ===")
    if errors:
        print(f"FAILED with {len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
