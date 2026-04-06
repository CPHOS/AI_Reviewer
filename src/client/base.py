"""LLM 客户端基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChatResponse:
    """LLM 单次调用响应。"""

    content: str
    """模型回复文本。"""
    prompt_tokens: int = 0
    """输入 token 数。"""
    completion_tokens: int = 0
    """输出 token 数。"""
    total_tokens: int = 0
    """总 token 数。"""


@dataclass
class UsageStats:
    """累计 token 用量统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def record(self, resp: ChatResponse) -> None:
        self.prompt_tokens += resp.prompt_tokens
        self.completion_tokens += resp.completion_tokens
        self.total_tokens += resp.total_tokens
        self.call_count += 1


class BaseLLMClient(ABC):
    """所有 LLM 客户端的抽象基类。

    支持多 API Key 轮询与带重试的调用。
    """

    def __init__(self) -> None:
        self.usage = UsageStats()

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> ChatResponse:
        """发送对话请求，返回响应（含 token 用量）。

        实现应自行处理多 Key 轮询与重试逻辑。
        """
        ...
