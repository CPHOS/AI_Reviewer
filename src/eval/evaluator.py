"""汇总评估模块。

基于题目整体概括和细致审核结果，生成三维难度评分和总结文字。
"""

from __future__ import annotations

import logging
import re

from src.client.openrouter import OpenRouterClient
from src.model import (
    EvalResult,
    NodeReview,
    Problem,
    ReviewResult,
)
from src.prompt.manager import PromptManager

logger = logging.getLogger(__name__)

_pm = PromptManager()


def _extract_tag_content(text: str, tag: str) -> str:
    """从文本中提取 <tag>...</tag> 之间的内容。"""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _collect_difficulty_stats(node_reviews: list[NodeReview]) -> str:
    """收集所有评分点的难度统计。"""
    lines: list[str] = []
    comp_vals: list[int] = []
    think_vals: list[int] = []

    def _walk(nrs: list[NodeReview]) -> None:
        for nr in nrs:
            for pr in nr.point_reviews:
                if pr.computation_difficulty or pr.thinking_difficulty:
                    comp_vals.append(pr.computation_difficulty)
                    think_vals.append(pr.thinking_difficulty)
                    lines.append(
                        f"- 评分点 {pr.point.tag}: 计算={pr.computation_difficulty}, "
                        f"思维={pr.thinking_difficulty}"
                    )
            if nr.child_reviews:
                _walk(nr.child_reviews)

    _walk(node_reviews)

    avg_comp = sum(comp_vals) / len(comp_vals) if comp_vals else 0
    avg_think = sum(think_vals) / len(think_vals) if think_vals else 0
    header = f"评分点数量: {len(comp_vals)}, 平均计算难度: {avg_comp:.1f}, 平均思维难度: {avg_think:.1f}\n"
    return header + "\n".join(lines)


def _collect_node_summaries(node_reviews: list[NodeReview], depth: int = 0) -> str:
    """收集所有节点的小结文字。"""
    parts: list[str] = []
    indent = "  " * depth
    for nr in node_reviews:
        parts.append(f"{indent}({nr.node.number}) [{nr.node.score}分]: {nr.summary}")
        if nr.child_reviews:
            parts.append(_collect_node_summaries(nr.child_reviews, depth + 1))
    return "\n".join(parts)


def comprehensive_eval(
    problem: Problem,
    review: ReviewResult,
    client: OpenRouterClient,
) -> EvalResult:
    """汇总评估。"""
    logger.info("开始汇总评估: %s", problem.title)

    node_summaries = _collect_node_summaries(review.node_reviews)
    difficulty_stats = _collect_difficulty_stats(review.node_reviews)
    num_sub = len(problem.children)

    messages = _pm.render(
        "comprehensive_eval",
        title=problem.title,
        total_score=str(problem.total_score),
        num_sub_questions=str(num_sub),
        statement=problem.statement,
        node_summaries=node_summaries,
        difficulty_stats=difficulty_stats,
    )

    resp = client.chat(messages)
    text = resp.content

    def _safe_int(tag_name: str, default: int = 5) -> int:
        val = _extract_tag_content(text, tag_name)
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    return EvalResult(
        computation_difficulty=_safe_int("computation_difficulty"),
        thinking_difficulty=_safe_int("thinking_difficulty"),
        overall_difficulty=_safe_int("overall_difficulty"),
        summary=_extract_tag_content(text, "summary"),
    )
