"""细致审核模块。

以小问（解法）为单元批量上传评分点进行 LLM 审核，对每个子题生成小结。
"""

from __future__ import annotations

import logging
import re

from src.client.openrouter import OpenRouterClient
from src.model import (
    Correctness,
    NodeReview,
    PointReview,
    Problem,
    QuestionNode,
    Reasonableness,
    ReviewResult,
    ScoringPoint,
    SolutionMethod,
)
from src.prompt.manager import PromptManager

logger = logging.getLogger(__name__)

_pm = PromptManager()


def _extract_tag_content(text: str, tag: str) -> str:
    """从文本中提取 <tag>...</tag> 之间的内容，返回空字符串如未找到。"""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_review_blocks(text: str) -> list[dict[str, str]]:
    """从 LLM 回复中解析所有 <review tag="...">...</review> 块。

    返回 dict 列表，每个 dict 包含 tag 及各字段的字符串值。
    """
    blocks: list[dict[str, str]] = []
    for m in re.finditer(
        r'<review\s+tag="([^"]*)">(.*?)</review>', text, re.DOTALL
    ):
        block_tag = m.group(1).strip()
        body = m.group(2)
        blocks.append({
            "tag": block_tag,
            "correctness": _extract_tag_content(body, "correctness"),
            "correctness_comment": _extract_tag_content(body, "correctness_comment"),
            "reasonableness": _extract_tag_content(body, "reasonableness"),
            "reasonableness_comment": _extract_tag_content(body, "reasonableness_comment"),
            "computation_difficulty": _extract_tag_content(body, "computation_difficulty"),
            "thinking_difficulty": _extract_tag_content(body, "thinking_difficulty"),
        })
    return blocks


def _format_points_list(points: list[ScoringPoint]) -> str:
    """将评分点列表格式化为文本，供 prompt 使用。"""
    parts: list[str] = []
    for i, pt in enumerate(points, 1):
        parts.append(
            f"{i}. 编号: {pt.tag}\n"
            f"   类型: {pt.kind.value}\n"
            f"   分值: {pt.score}分\n"
            f"   内容:\n   {pt.content}"
        )
    return "\n\n".join(parts)


def _format_prior_reviews(node_reviews: list[NodeReview]) -> str:
    """将已完成的兄弟节点审核结果格式化为上下文文本。"""
    if not node_reviews:
        return ""
    parts: list[str] = []
    for nr in node_reviews:
        lines = [f"### 子题 {nr.node.number}（{nr.node.score}分）"]
        for pr in nr.point_reviews:
            if pr.parse_failed:
                lines.append(f"- 评分点 {pr.point.tag}: 解析失败")
                continue
            c_val = pr.correctness.value if pr.correctness else "unknown"
            r_val = pr.reasonableness.value if pr.reasonableness else "unknown"
            lines.append(
                f"- 评分点 {pr.point.tag}: "
                f"正确性={c_val} 合理性={r_val} "
                f"计算难度={pr.computation_difficulty} 思维难度={pr.thinking_difficulty}"
            )
        if nr.summary:
            lines.append(f"小结: {nr.summary}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def review_method(
    method: SolutionMethod,
    context: str,
    statement: str,
    sub_statement: str,
    client: OpenRouterClient,
    prior_reviews: str = "",
) -> list[PointReview]:
    """批量审核一种解法下的所有评分点（单次 LLM 请求）。"""
    points = method.scoring_points
    if not points:
        return []

    tags = ", ".join(pt.tag for pt in points)
    logger.info("  批量审核评分点: [%s] (%d 个)", tags, len(points))

    points_list_text = _format_points_list(points)
    messages = _pm.render(
        "review_point",
        statement=statement,
        sub_statement=sub_statement,
        context=context,
        points_list=points_list_text,
        points_count=str(len(points)),
        prior_reviews=prior_reviews,
    )
    resp = client.chat(messages)

    raw_content = resp.content

    # 解析 <review> 标签块
    parse_failed = False
    data_list = _parse_review_blocks(raw_content)
    if not data_list:
        logger.warning(
            "解法 %d 的 LLM 回复中未找到 <review> 标签，标记为解析失败。\n原始回复前 500 字符: %s",
            method.index,
            raw_content[:500],
        )
        parse_failed = True

    # 将结果按 tag 映射回评分点
    data_by_tag: dict[str, dict] = {}
    for item in data_list:
        if item.get("tag"):
            data_by_tag[item["tag"]] = item

    reviews: list[PointReview] = []
    for i, pt in enumerate(points):
        if parse_failed:
            pr = PointReview(
                point=pt,
                parse_failed=True,
                raw_response=raw_content,
            )
            pr.method_index = method.index
            reviews.append(pr)
            continue

        # 优先按 tag 匹配，其次按顺序匹配
        data = data_by_tag.get(pt.tag)
        if data is None and i < len(data_list) and isinstance(data_list[i], dict):
            data = data_list[i]
        if data is None:
            data = {}

        try:
            correctness = Correctness(data.get("correctness", "correct"))
        except ValueError:
            correctness = Correctness.CORRECT
        try:
            reasonableness = Reasonableness(data.get("reasonableness", "reasonable"))
        except ValueError:
            reasonableness = Reasonableness.REASONABLE
        try:
            comp_diff = int(data.get("computation_difficulty", 5))
        except (ValueError, TypeError):
            comp_diff = 5
        try:
            think_diff = int(data.get("thinking_difficulty", 5))
        except (ValueError, TypeError):
            think_diff = 5

        pr = PointReview(
            point=pt,
            correctness=correctness,
            correctness_comment=data.get("correctness_comment", ""),
            reasonableness=reasonableness,
            reasonableness_comment=data.get("reasonableness_comment", ""),
            computation_difficulty=comp_diff,
            thinking_difficulty=think_diff,
        )
        pr.method_index = method.index
        reviews.append(pr)
    return reviews


def summarize_node(
    node: QuestionNode,
    point_reviews: list[PointReview],
    client: OpenRouterClient,
) -> str:
    """为一个子题节点生成审核小结。"""
    # 构建评分点审核结果文本
    pr_lines: list[str] = []
    for pr in point_reviews:
        c_icon = {"correct": "✅", "minor_issue": "⚠️", "wrong": "❌"}.get(
            pr.correctness.value if pr.correctness else "", "?"
        )
        r_icon = {"reasonable": "✅", "questionable": "⚠️", "unreasonable": "❌"}.get(
            pr.reasonableness.value if pr.reasonableness else "", "?"
        )
        pr_lines.append(
            f"- 评分点 {pr.point.tag} ({pr.point.kind.value}, {pr.point.score}分): "
            f"正确性{c_icon} {pr.correctness_comment} | "
            f"合理性{r_icon} {pr.reasonableness_comment} | "
            f"计算难度={pr.computation_difficulty} 思维难度={pr.thinking_difficulty}"
        )

    messages = _pm.render(
        "review_summary",
        number=node.number,
        score=str(node.score),
        sub_statement=node.statement,
        point_reviews="\n".join(pr_lines),
    )
    resp = client.chat(messages)
    return resp.content.strip()


def review_node(
    node: QuestionNode,
    parent_context: str,
    statement: str,
    client: OpenRouterClient,
    prior_reviews: str = "",
) -> NodeReview:
    """递归审核一个层级节点及其所有子节点。"""
    logger.info("审核节点: (%s) [%d分]", node.number, node.score)

    nr = NodeReview(node=node)

    if node.children:
        # 有子节点 → 递归审核子节点，逐步累积已审核上下文
        for child in node.children:
            child_review = review_node(
                child, parent_context, statement, client, prior_reviews
            )
            nr.child_reviews.append(child_review)
            nr.point_reviews.extend(child_review.point_reviews)
            # 把刚完成的子节点审核结果加入上下文，供后续兄弟使用
            prior_reviews = _format_prior_reviews(nr.child_reviews)
    else:
        # 叶节点 → 审核本节点的解法（展开后每节点最多 1 种解法）
        sol_context = node.solution_tex or parent_context
        for method in node.methods:
            prs = review_method(
                method, sol_context, statement, node.statement, client,
                prior_reviews=prior_reviews,
            )
            nr.point_reviews.extend(prs)

    # 生成小结
    if nr.point_reviews:
        logger.info("生成节点 (%s) 小结…", node.number)
        nr.summary = summarize_node(node, nr.point_reviews, client)

    return nr


def detailed_review(problem: Problem, client: OpenRouterClient) -> ReviewResult:
    """对整道题目执行细致审核。"""
    logger.info("开始细致审核: %s", problem.title)
    node_reviews: list[NodeReview] = []
    for child in problem.children:
        prior = _format_prior_reviews(node_reviews)
        nr = review_node(
            child, problem.solution_tex, problem.statement, client,
            prior_reviews=prior,
        )
        node_reviews.append(nr)
    return ReviewResult(problem=problem, node_reviews=node_reviews)
