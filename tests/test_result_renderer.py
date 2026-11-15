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

    assert ResultRenderer.to_gemini_part(result) == {"functionResponse": {"response": {"content": ["ok"]}}}


def test_error_results_are_marked_in_openai_and_gemini() -> None:
    result = ToolResult(content=[ContentBlock(type="text", text="boom")], is_error=True)

    openai = ResultRenderer.to_openai_message(result, tool_call_id="call_123")
    assert openai["content"] != "boom"
    assert openai["content"].startswith("Error: ")
    assert "boom" in openai["content"]

    gemini = ResultRenderer.to_gemini_part(result, name="search_memories")
    response = gemini["functionResponse"]["response"]
    assert response.get("is_error") is True
    assert response["content"]
    assert any("boom" in entry for entry in response["content"])


def test_resource_link_only_results_render_non_empty_in_openai_and_gemini() -> None:
    result = ToolResult(content=[ContentBlock(type="resource_link", uri="https://example.com/resource")])

    openai = ResultRenderer.to_openai_message(result, tool_call_id="call_123")
    assert openai["content"] != ""
    assert "https://example.com/resource" in openai["content"]

    gemini = ResultRenderer.to_gemini_part(result, name="search_memories")
    content = gemini["functionResponse"]["response"]["content"]
    assert content != []
    assert any("https://example.com/resource" in entry for entry in content)


def test_image_only_results_render_placeholder_in_openai_and_gemini() -> None:
    result = ToolResult(content=[ContentBlock(type="image", data=b"image-bytes", mime_type="image/png")])

    openai = ResultRenderer.to_openai_message(result, tool_call_id="call_123")
    assert openai["content"] != ""

    gemini = ResultRenderer.to_gemini_part(result, name="search_memories")
    assert gemini["functionResponse"]["response"]["content"] != []
