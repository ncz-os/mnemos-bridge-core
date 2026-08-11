# mnemos-bridge-core

Shared core library for MNEMOS-to-LLM-consumer bridges. It keeps MCP wire types, schema translation, auth lookup,
result rendering, and retry dispatch logic in one package so surface-specific adapters can stay small.

## Install

```bash
pip install mnemos-bridge-core
```

## Schema Translation

The same `ToolSchema` can be adapted to each target consumer:

```python
from mnemos_bridge_core import SchemaTranslator, ToolSchema

tool = ToolSchema(
    name="search_memories",
    description="Full-text search across stored MNEMOS memories.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 10}},
        "required": ["query"],
    },
)

openai_tool = SchemaTranslator.to_openai(tool)
gemini_tool = SchemaTranslator.to_gemini(tool)
anthropic_tool = SchemaTranslator.to_anthropic(tool)
```

## MCP Client

```python
import anyio

from mnemos_bridge_core import AuthResolver, McpClient


async def main() -> None:
    token = AuthResolver.resolve_mcp_token()
    async with McpClient.from_url("https://mnemos.example.com/sse", token=token) as client:
        tools = await client.list_tools()
        result = await client.call_tool("search_memories", {"query": "routing notes", "limit": 5})
        print(len(tools), result.is_error)


anyio.run(main)
```

## AuthResolver

```python
from mnemos_bridge_core import AuthResolver

mcp_token = AuthResolver.resolve_mcp_token()
api_key = AuthResolver.resolve()
explicit_api_key = AuthResolver.resolve(api_key="mnemos_sk_test")
```

`resolve_mcp_token()` looks up an explicit token and then `MNEMOS_MCP_TOKEN`.
For a per-user `MNEMOS_MCP_TOKENS` mapping, pass the selected MCP token explicitly.
`resolve()` remains the backend API-key resolver; its lookup order is explicit
argument, `MNEMOS_API_KEY`, `~/.mnemos/config.toml`, then
`~/.api_keys_master.json` at `mnemos.bridge.api_key`.

## Dispatcher

```python
from mnemos_bridge_core import dispatch

result = await dispatch(
    client,
    "search_memories",
    {"query": "forecasting"},
    retries=2,
    timeout=30,
    idempotent=True,
)
```


## Build infrastructure & partners

Continuous integration and package distribution for this project are generously
supported by our open-source infrastructure partners:

- **[GitLab](https://gitlab.com/)** — canonical source hosting and CI pipelines
  (format / lint / test gates), via the
  [GitLab for Open Source](https://about.gitlab.com/solutions/open-source/) program.
- **[Buildkite](https://buildkite.com/)** — CI/CD orchestration with hosted macOS
  and Linux agents, and our APT package registry host
  (`packages.buildkite.com/ncz-os/ncz`), via the
  [Buildkite Open Source](https://buildkite.com/pricing) program.

Thank you to both for backing open-source software.
