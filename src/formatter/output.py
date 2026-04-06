"""格式化输出模块。

生成 Markdown + JSON 双格式报告。
"""

from __future__ import annotations

from typing import Any

from src.model import (
    EvalResult,
    NodeReview,
    PointReview,
    Problem,
    ReportMeta,
    ReviewResult,
)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

_CORRECTNESS_ICON = {"correct": "✅", "minor_issue": "⚠️", "wrong": "❌"}
_REASONABLENESS_ICON = {"reasonable": "✅", "questionable": "⚠️", "unreasonable": "❌"}


def _collect_all_point_reviews(node_reviews: list[NodeReview]) -> list[PointReview]:
    """扁平化收集所有 PointReview（不重复）。"""
    result: list[PointReview] = []
    seen_tags: set[str] = set()
    for nr in node_reviews:
        for pr in nr.point_reviews:
            key = f"{pr.point.tag}_{pr.method_index}"
            if key not in seen_tags:
                seen_tags.add(key)
                result.append(pr)
        if nr.child_reviews:
            for pr in _collect_all_point_reviews(nr.child_reviews):
                key = f"{pr.point.tag}_{pr.method_index}"
                if key not in seen_tags:
                    seen_tags.add(key)
                    result.append(pr)
    return result


def _c(pr: PointReview) -> str:
    return _CORRECTNESS_ICON.get(pr.correctness.value if pr.correctness else "", "?")


def _r(pr: PointReview) -> str:
    return _REASONABLENESS_ICON.get(pr.reasonableness.value if pr.reasonableness else "", "?")


def _render_node_md(nr: NodeReview, depth: int = 3) -> str:
    """递归渲染一个层级节点的 Markdown。"""
    hdr = "#" * depth
    parts: list[str] = []
    method_tag = f" [{nr.node.method_label}]" if nr.node.method_label else ""
    parts.append(f"{hdr} （{nr.node.number}）[{nr.node.score}分]{method_tag}\n")

    if nr.child_reviews:
        for child in nr.child_reviews:
            parts.append(_render_node_md(child, depth + 1))
    else:
        for pr in nr.point_reviews:
            if pr.parse_failed:
                parts.append(
                    f"{hdr}# 评分点 {pr.point.tag} "
                    f"({pr.point.kind.value}, {pr.point.score}分)"
                    f" — ⛔ 解析失败\n"
                )
                parts.append(f"- **状态**: LLM 回复无法解析为有效 JSON\n")
                parts.append(f"- **原始输出**:\n")
                parts.append(f"<details>\n<summary>点击展开 LLM 原始回复</summary>\n\n```\n{pr.raw_response}\n```\n\n</details>\n")
            else:
                parts.append(
                    f"{hdr}# 评分点 {pr.point.tag} "
                    f"({pr.point.kind.value}, {pr.point.score}分)"
                    f" — {_c(pr)}正确 {_r(pr)}合理\n"
                )
                parts.append(f"- **正确性**: {pr.correctness_comment}\n")
                parts.append(f"- **合理性**: {pr.reasonableness_comment}\n")
                parts.append(
                    f"- 计算难度: {pr.computation_difficulty} / "
                    f"思维难度: {pr.thinking_difficulty}\n"
                )

    if nr.summary:
        parts.append(f"{hdr}# 小结\n")
        parts.append(f"{nr.summary}\n")

    return "\n".join(parts)


def format_markdown(
    problem: Problem,
    review: ReviewResult,
    evaluation: EvalResult,
    meta: ReportMeta,
) -> str:
    """将审核结果格式化为 Markdown 文本。"""
    lines: list[str] = []

    # 标题
    lines.append(f"# 题目审核报告：{problem.title}\n")

    # 概览表
    lines.append("## 概览\n")
    lines.append("| 项目 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| 题目 | {problem.title} |")
    lines.append(f"| 总分 | {problem.total_score} |")
    lines.append(f"| 计算难度 | {evaluation.computation_difficulty}/10 |")
    lines.append(f"| 思维难度 | {evaluation.thinking_difficulty}/10 |")
    lines.append(f"| 综合难度 | {evaluation.overall_difficulty}/10 |")
    lines.append("")

    # 评分点总览表
    all_prs = _collect_all_point_reviews(review.node_reviews)
    lines.append("## 评分点总览\n")
    lines.append("| 编号 | 类型 | 分值 | 正确性 | 合理性 | 计算难度 | 思维难度 |")
    lines.append("|------|------|------|--------|--------|----------|----------|")
    for pr in all_prs:
        if pr.parse_failed:
            lines.append(
                f"| {pr.point.tag} | {pr.point.kind.value} | {pr.point.score} | "
                f"⛔ | ⛔ | — | — |"
            )
        else:
            lines.append(
                f"| {pr.point.tag} | {pr.point.kind.value} | {pr.point.score} | "
                f"{_c(pr)} | {_r(pr)} | {pr.computation_difficulty} | {pr.thinking_difficulty} |"
            )
    lines.append("")

    # 细致审核
    lines.append("## 细致审核\n")
    for nr in review.node_reviews:
        lines.append(_render_node_md(nr, depth=3))

    # 综合评估
    lines.append("## 综合评估\n")
    lines.append(f"{evaluation.summary}\n")

    # 报告元信息
    lines.append("## 报告元信息\n")
    lines.append("| 项目 | 值 |")
    lines.append("|------|------|")
    lines.append(f"| 模型 | {meta.model} |")
    lines.append(f"| Prompt Tokens | {meta.prompt_tokens} |")
    lines.append(f"| Completion Tokens | {meta.completion_tokens} |")
    lines.append(f"| Total Tokens | {meta.total_tokens} |")
    lines.append(f"| 生成时间 | {meta.timestamp} |")
    lines.append(f"| 总耗时 | {meta.elapsed_seconds:.1f} 秒 |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _node_review_to_dict(nr: NodeReview) -> dict[str, Any]:
    """将 NodeReview 转为可序列化的字典。"""
    return {
        "level": nr.node.level.value,
        "number": nr.node.number,
        "score": nr.node.score,
        "method_label": nr.node.method_label,
        "summary": nr.summary,
        "point_reviews": [
            {
                "tag": pr.point.tag,
                "kind": pr.point.kind.value,
                "score": pr.point.score,
                "method_index": pr.method_index,
                "parse_failed": pr.parse_failed,
                **({
                    "correctness": pr.correctness.value if pr.correctness else None,
                    "correctness_comment": pr.correctness_comment,
                    "reasonableness": pr.reasonableness.value if pr.reasonableness else None,
                    "reasonableness_comment": pr.reasonableness_comment,
                    "computation_difficulty": pr.computation_difficulty,
                    "thinking_difficulty": pr.thinking_difficulty,
                } if not pr.parse_failed else {
                    "raw_response": pr.raw_response,
                }),
            }
            for pr in nr.point_reviews
        ],
        "children": [_node_review_to_dict(c) for c in nr.child_reviews],
    }


def format_json(
    problem: Problem,
    review: ReviewResult,
    evaluation: EvalResult,
    meta: ReportMeta,
) -> dict[str, Any]:
    """将审核结果格式化为可序列化的字典。"""
    all_prs = _collect_all_point_reviews(review.node_reviews)

    return {
        "meta": {
            "model": meta.model,
            "prompt_tokens": meta.prompt_tokens,
            "completion_tokens": meta.completion_tokens,
            "total_tokens": meta.total_tokens,
            "timestamp": meta.timestamp,
            "elapsed_seconds": meta.elapsed_seconds,
        },
        "title": problem.title,
        "total_score": problem.total_score,
        "difficulty": {
            "computation": evaluation.computation_difficulty,
            "thinking": evaluation.thinking_difficulty,
            "overall": evaluation.overall_difficulty,
        },
        "summary": evaluation.summary,
        "scoring_points": [
            {
                "tag": pr.point.tag,
                "kind": pr.point.kind.value,
                "score": pr.point.score,
                "method_index": pr.method_index,
                "parse_failed": pr.parse_failed,
                **({
                    "correctness": pr.correctness.value if pr.correctness else None,
                    "correctness_comment": pr.correctness_comment,
                    "reasonableness": pr.reasonableness.value if pr.reasonableness else None,
                    "reasonableness_comment": pr.reasonableness_comment,
                    "computation_difficulty": pr.computation_difficulty,
                    "thinking_difficulty": pr.thinking_difficulty,
                } if not pr.parse_failed else {
                    "raw_response": pr.raw_response,
                }),
            }
            for pr in all_prs
        ],
        "node_reviews": [_node_review_to_dict(nr) for nr in review.node_reviews],
    }


# ---------------------------------------------------------------------------
# 双格式输出
# ---------------------------------------------------------------------------


def format_output(
    problem: Problem,
    review: ReviewResult,
    evaluation: EvalResult,
    meta: ReportMeta,
) -> tuple[str, dict[str, Any]]:
    """将审核结果格式化为 Markdown + JSON 双格式输出。"""
    md = format_markdown(problem, review, evaluation, meta)
    js = format_json(problem, review, evaluation, meta)
    return md, js
