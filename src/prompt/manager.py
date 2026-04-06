"""基于 YAML 文件的提示词管理器。

提示词模板内置在 src/prompt/templates/ 下，随代码一起分发。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptManager:
    """从 YAML 文件加载和渲染提示词模板。

    YAML 文件格式::

        name: review_point
        description: 审核单个评分点
        system: |
          你是一个物理竞赛审题专家...
        user: |
          请审核以下评分点：
          {content}
    """

    def __init__(self, template_dir: str | Path | None = None) -> None:
        self._dir = Path(template_dir) if template_dir else _TEMPLATES_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def _load(self, name: str) -> dict[str, Any]:
        if name in self._cache:
            return self._cache[name]
        path = self._dir / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._cache[name] = data
        return data

    def render(self, name: str, **variables: str) -> list[dict[str, str]]:
        """加载并渲染提示词模板，返回 messages 列表。"""
        tpl = self._load(name)
        messages: list[dict[str, str]] = []
        if "system" in tpl:
            messages.append({"role": "system", "content": tpl["system"].format(**variables)})
        if "user" in tpl:
            messages.append({"role": "user", "content": tpl["user"].format(**variables)})
        return messages

    def list_templates(self) -> list[str]:
        """列出所有可用模板名称。"""
        return sorted(p.stem for p in self._dir.glob("*.yaml"))
