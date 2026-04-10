"""全局配置管理。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """LLM 服务配置。"""

    provider: str = "openrouter"
    model: str = ""
    api_keys: list[str] = field(default_factory=list)
    """支持多个 API Key，失败时轮询。"""
    base_url: str = ""
    temperature: float = 0.3
    max_tokens: int = 4096
    max_retries: int = 3
    """单个 Key 最大重试次数。"""
    retry_interval: float = 2.0
    """重试间隔（秒）。"""
    batch_min_points: int = 0
    """相邻小问合并审核的最小评分点阈值。0 表示不合并。"""


@dataclass
class QBConfig:
    """题库服务器配置（server 模式）。"""

    url: str = ""
    username: str = ""
    password: str = ""
    poll_interval: int = 600
    """自动模式轮询间隔（秒），默认 600。"""


@dataclass
class AppConfig:
    """应用全局配置。"""

    llm: LLMConfig = field(default_factory=LLMConfig)
    qb: QBConfig = field(default_factory=QBConfig)
    output_dir: str = "output"
    """报告输出目录。"""


# 全局单例
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """获取全局配置（懒初始化）。"""
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def init_config(**overrides) -> AppConfig:
    """初始化全局配置。"""
    global _config
    _config = AppConfig(**overrides)
    return _config
