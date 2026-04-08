"""Dedicated exception types used across layers."""

from __future__ import annotations


class LLMError(Exception):
    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(f"{stage}: {detail}" if detail else stage)
        self.stage = stage
        self.detail = detail


class ToolError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class NodeError(Exception):
    def __init__(self, node_name: str, reason: str) -> None:
        super().__init__(f"{node_name}: {reason}")
        self.node_name = node_name
        self.reason = reason
