from __future__ import annotations

import json

import pytest

from mnemos_bridge_core import ContentBlock, ResultRenderer, ToolResult


@pytest.mark.parametrize(
    "result",
    [
        ToolResult(content=[ContentBlock(type="text", text="ok")]),
        ToolResult(
            content=[
                ContentBlock(type="text", text="first"),
                ContentBlock(type="image", data=b"image-bytes", mime_type="image/png"),
                ContentBlock(type="text", text="second"),
            ]
        ),
        ToolResult(content=[ContentBlock(type="text", text="failed")], is_error=True),
    ],
)
def test_result_renderers_emit_expected_shapes(result: ToolResult) -> None:
    openai = ResultRenderer.to_openai_message(result, tool_call_id="call_123")
    assert openai["role"] == "tool"
    assert openai["tool_call_id"] == "call_123"
    assert isinstance(openai["content"], str)

    gemini = ResultRenderer.to_gemini_part(result, name="search_memories")
    assert "functionResponse" in gemini
    assert gemini["functionResponse"]["name"] == "search_memories"
    assert "response" in gemini["functionResponse"]

    anthropic = ResultRenderer.to_anthropic_message(result, tool_use_id="toolu_123")
    assert anthropic["role"] == "user"
    assert isinstance(anthropic["content"], list)
    assert anthropic["content"][0]["type"] == "tool_result"
    assert anthropic["content"][0]["tool_use_id"] == "toolu_123"
    json.dumps(anthropic)


def test_anthropic_renders_provider_specific_image_and_resource_blocks() -> None:
    result = ToolResult(
        content=[
            ContentBlock(type="image", data=b"image-bytes", mime_type="image/png"),
            ContentBlock(type="resource_link", uri="https://example.com/resource"),
        ]
    )

    message = ResultRenderer.to_anthropic_message(result, tool_use_id="toolu_123")

    assert message["content"][0]["content"] == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "aW1hZ2UtYnl0ZXM=",
            },
        },
        {"type": "text", "text": "https://example.com/resource"},
    ]


def test_gemini_renderer_preserves_one_argument_api() -> None:
    result = ToolResult(content=[ContentBlock(type="text", text="ok")])

    assert ResultRenderer.to_gemini_part(result) == {
        "functionResponse": {"response": {"content": ["ok"]}}
    }
