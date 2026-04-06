"""OpenRouter LLM 客户端实现。

支持多 API Key 轮询：当某个 Key 调用失败时，自动切换到下一个 Key 重试。
每个 Key 独立计数重试次数，达到 max_retries 后切换。
"""

from __future__ import annotations

import logging
import time

from openai import OpenAI, APIError

from src.client.base import BaseLLMClient, ChatResponse
from src.config import get_config

logger = logging.getLogger(__name__)


class OpenRouterClient(BaseLLMClient):
    """通过 OpenAI SDK 调用 OpenRouter API，支持多 Key 轮询与重试。"""

    def __init__(self) -> None:
        super().__init__()
        cfg = get_config().llm
        self._api_keys = cfg.api_keys
        if not self._api_keys:
            raise ValueError("至少需要配置一个 API Key (OPENROUTER_API_KEY)")
        self._base_url = cfg.base_url or "https://openrouter.ai/api/v1"
        self._model = cfg.model
        self._temperature = cfg.temperature
        self._max_tokens = cfg.max_tokens
        self._max_retries = cfg.max_retries
        self._retry_interval = cfg.retry_interval
        self._current_key_index = 0

    def _make_client(self, key_index: int) -> OpenAI:
        return OpenAI(api_key=self._api_keys[key_index], base_url=self._base_url)

    def chat(self, messages: list[dict], **kwargs) -> ChatResponse:
        """发送请求，失败时轮询 Key 并重试。"""
        total_keys = len(self._api_keys)
        keys_tried = 0
        last_error: Exception | None = None

        while keys_tried < total_keys:
            key_idx = (self._current_key_index + keys_tried) % total_keys
            client = self._make_client(key_idx)
            retries = 0

            while retries <= self._max_retries:
                try:
                    logger.debug(
                        "调用 LLM: key_index=%d, retry=%d/%d",
                        key_idx, retries, self._max_retries,
                    )
                    response = client.chat.completions.create(
                        model=kwargs.pop("model", self._model),
                        messages=messages,
                        temperature=kwargs.pop("temperature", self._temperature),
                        max_tokens=kwargs.pop("max_tokens", self._max_tokens),
                        **kwargs,
                    )
                    usage = response.usage
                    result = ChatResponse(
                        content=response.choices[0].message.content or "",
                        prompt_tokens=usage.prompt_tokens if usage else 0,
                        completion_tokens=usage.completion_tokens if usage else 0,
                        total_tokens=usage.total_tokens if usage else 0,
                    )
                    self.usage.record(result)
                    # 成功后记住当前 key 以便下次优先使用
                    self._current_key_index = key_idx
                    logger.debug(
                        "LLM 调用成功: tokens=%d (prompt=%d, completion=%d)",
                        result.total_tokens, result.prompt_tokens, result.completion_tokens,
                    )
                    return result
                except APIError as e:
                    last_error = e
                    retries += 1
                    logger.warning(
                        "LLM 调用失败 (key_index=%d, retry=%d/%d): %s",
                        key_idx, retries, self._max_retries, e,
                    )
                    if retries <= self._max_retries:
                        time.sleep(self._retry_interval)

            logger.warning("Key #%d 重试耗尽，切换下一个 Key", key_idx)
            keys_tried += 1

        raise RuntimeError(
            f"所有 API Key ({total_keys} 个) 均已耗尽重试次数"
        ) from last_error
