from __future__ import annotations

import base64
from typing import Any

from .types import ContentBlock, ToolResult


class ResultRenderer:
    @staticmethod
    def to_openai_message(r: ToolResult, tool_call_id: str) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": " ".join(block.text for block in r.content if block.text),
        }

    @staticmethod
    def to_gemini_part(r: ToolResult, name: str) -> dict[str, Any]:
        return {
            "functionResponse": {
                "name": name,
                "response": {
                    "content": [block.text for block in r.content if block.text],
                }
            }
        }

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
