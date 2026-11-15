from __future__ import annotations

import base64
from typing import Any

from .types import ContentBlock, ToolResult


class ResultRenderer:
    @staticmethod
    def _to_text_block(block: ContentBlock) -> str:
        if block.type == "resource_link":
            return block.uri or ""
        if block.type == "image":
            return f"[image: {block.mime_type}]" if block.mime_type else "[image]"
        return block.text or ""

    @staticmethod
    def to_openai_message(r: ToolResult, tool_call_id: str) -> dict[str, Any]:
        content = " ".join(part for part in (ResultRenderer._to_text_block(block) for block in r.content) if part)
        if r.is_error:
            content = f"Error: {content}" if content else "Error:"
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }

    @staticmethod
    def to_gemini_part(r: ToolResult, name: str | None = None) -> dict[str, Any]:
        response: dict[str, Any] = {
            "content": [part for part in (ResultRenderer._to_text_block(block) for block in r.content) if part],
        }
        if r.is_error:
            response["is_error"] = True
        function_response: dict[str, Any] = {"response": response}
        if name is not None:
            function_response["name"] = name
        return {"functionResponse": function_response}

    @staticmethod
    def to_anthropic_message(r: ToolResult, tool_use_id: str) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [ResultRenderer._to_anthropic_content(block) for block in r.content],
                    "is_error": r.is_error,
                }
            ],
        }

    @staticmethod
    def _to_anthropic_content(block: ContentBlock) -> dict[str, Any]:
        if block.type == "image":
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.mime_type,
                    "data": base64.b64encode(block.data or b"").decode("ascii"),
                },
            }
        if block.type == "resource_link":
            return {"type": "text", "text": block.uri or ""}
        return {"type": "text", "text": block.text or ""}
