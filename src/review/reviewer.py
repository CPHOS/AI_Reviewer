"""细致审核模块。

以小问（解法）为单元批量上传评分点进行 LLM 审核，对每个子题生成小结。
支持将相邻的小规模叶节点合并为一次 LLM 请求，减少调用次数。
"""

from __future__ import annotations

import logging
import re

from src.client.openrouter import OpenRouterClient
from src.config import get_config
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
    max_parse_retries: int = 2,
) -> list[PointReview]:
    """批量审核一种解法下的所有评分点（单次 LLM 请求）。

    解析失败时最多重试 max_parse_retries 次。
    """
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

    # 尝试请求并解析，解析失败时重试
    raw_content = ""
    data_list: list[dict[str, str]] = []
    parse_failed = False
    for attempt in range(1 + max_parse_retries):
        resp = client.chat(messages)
        raw_content = resp.content
        data_list = _parse_review_blocks(raw_content)
        if data_list:
            parse_failed = False
            break
        parse_failed = True
        if attempt < max_parse_retries:
            logger.warning(
                "解法 %d 的 LLM 回复中未找到 <review> 标签（第 %d/%d 次尝试），重试…\n"
                "原始回复前 500 字符: %s",
                method.index, attempt + 1, 1 + max_parse_retries,
                raw_content[:500],
            )
        else:
            logger.warning(
                "解法 %d 的 LLM 回复解析失败，已达最大重试次数 (%d 次)，标记为解析失败。\n"
                "原始回复前 500 字符: %s",
                method.index, 1 + max_parse_retries,
                raw_content[:500],
            )

    # 将结果按 tag 映射回评分点
    return _parse_point_reviews(
        points, [method.index] * len(points),
        raw_content, data_list, parse_failed,
    )


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


def _count_points(node: QuestionNode) -> int:
    """统计叶节点所有解法的评分点总数。"""
    return sum(len(m.scoring_points) for m in node.methods)


def _group_leaf_batches(
    children: list[QuestionNode], min_points: int
) -> list[list[QuestionNode]]:
    """将相邻的小规模叶节点分组，累积评分点达到阈值后切分。

    非叶节点或 ``min_points <= 0`` 时不合并，每个节点独立一组。
    """
    if min_points <= 0:
        return [[c] for c in children]

    groups: list[list[QuestionNode]] = []
    current_batch: list[QuestionNode] = []
    current_count = 0

    for child in children:
        if child.children:
            # 非叶节点：先把已积攒的批次刷出，再独立成组
            if current_batch:
                groups.append(current_batch)
                current_batch = []
                current_count = 0
            groups.append([child])
            continue

        n_pts = _count_points(child)
        current_batch.append(child)
        current_count += n_pts

        if current_count >= min_points:
            groups.append(current_batch)
            current_batch = []
            current_count = 0

    if current_batch:
        groups.append(current_batch)

    return groups


def _parse_point_reviews(
    points: list[ScoringPoint],
    method_indices: list[int],
    raw_content: str,
    data_list: list[dict[str, str]],
    parse_failed: bool,
) -> list[PointReview]:
    """将 LLM 回复解析为 PointReview 列表（review_method 和 _review_leaf_batch 共用）。"""
    data_by_tag: dict[str, dict] = {}
    for item in data_list:
        if item.get("tag"):
            data_by_tag[item["tag"]] = item

    reviews: list[PointReview] = []
    for i, pt in enumerate(points):
        mi = method_indices[i]
        if parse_failed:
            pr = PointReview(point=pt, parse_failed=True, raw_response=raw_content)
            pr.method_index = mi
            reviews.append(pr)
            continue

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
            raw_response=raw_content,
        )
        pr.method_index = mi
        reviews.append(pr)
    return reviews


def _review_leaf_batch(
    nodes: list[QuestionNode],
    parent_context: str,
    statement: str,
    client: OpenRouterClient,
    prior_reviews: str = "",
    max_parse_retries: int = 2,
) -> list[NodeReview]:
    """将多个叶节点的评分点合并为一次 LLM 调用，再分别生成小结。"""
    # 收集所有评分点并记录归属
    all_points: list[ScoringPoint] = []
    method_indices: list[int] = []
    owner_indices: list[int] = []  # 每个评分点属于 nodes 的第几个

    for ni, node in enumerate(nodes):
        for method in node.methods:
            for pt in method.scoring_points:
                all_points.append(pt)
                method_indices.append(method.index)
                owner_indices.append(ni)

    if not all_points:
        return [NodeReview(node=n) for n in nodes]

    # 拼接子题信息
    sub_parts: list[str] = []
    ctx_parts: list[str] = []
    for node in nodes:
        sub_parts.append(f"#### 子题 {node.number}（{node.score}分）\n{node.statement}")
        sol_ctx = node.solution_tex or parent_context
        ctx_parts.append(f"--- 子题 {node.number} 的解答 ---\n{sol_ctx}")
    combined_sub = "\n\n".join(sub_parts)
    combined_ctx = "\n\n".join(ctx_parts)

    tags = ", ".join(pt.tag for pt in all_points)
    logger.info(
        "  合并审核 %d 个子题，评分点: [%s] (%d 个)",
        len(nodes), tags, len(all_points),
    )

    points_list_text = _format_points_list(all_points)
    messages = _pm.render(
        "review_point",
        statement=statement,
        sub_statement=combined_sub,
        context=combined_ctx,
        points_list=points_list_text,
        points_count=str(len(all_points)),
        prior_reviews=prior_reviews,
    )

    # LLM 调用（含重试）
    raw_content = ""
    data_list: list[dict[str, str]] = []
    parse_failed = False
    for attempt in range(1 + max_parse_retries):
        resp = client.chat(messages)
        raw_content = resp.content
        data_list = _parse_review_blocks(raw_content)
        if data_list:
            parse_failed = False
            break
        parse_failed = True
        if attempt < max_parse_retries:
            logger.warning(
                "合并审核 LLM 回复中未找到 <review> 标签"
                "（第 %d/%d 次尝试），重试…",
                attempt + 1, 1 + max_parse_retries,
            )
        else:
            logger.warning(
                "合并审核 LLM 回复解析失败，已达最大重试次数 (%d 次)。",
                1 + max_parse_retries,
            )

    # 解析结果
    all_reviews = _parse_point_reviews(
        all_points, method_indices, raw_content, data_list, parse_failed,
    )

    # 按节点拆分并生成小结
    node_reviews: list[NodeReview] = []
    for ni, node in enumerate(nodes):
        nr = NodeReview(node=node)
        nr.point_reviews = [
            all_reviews[i] for i, oi in enumerate(owner_indices) if oi == ni
        ]
        if nr.point_reviews:
            logger.info("生成节点 (%s) 小结…", node.number)
            nr.summary = summarize_node(node, nr.point_reviews, client)
        node_reviews.append(nr)

    return node_reviews


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
        # 有子节点 → 按阈值将相邻叶节点分组，减少 LLM 调用
        min_pts = get_config().llm.batch_min_points
        batches = _group_leaf_batches(node.children, min_pts)

        for batch in batches:
            if len(batch) == 1:
                # 单节点（叶或非叶）：沿用原有递归审核
                child_review = review_node(
                    batch[0], parent_context, statement, client, prior_reviews
                )
                nr.child_reviews.append(child_review)
                nr.point_reviews.extend(child_review.point_reviews)
            else:
                # 多个叶节点合并审核
                batch_reviews = _review_leaf_batch(
                    batch, parent_context, statement, client,
                    prior_reviews=prior_reviews,
                )
                for br in batch_reviews:
                    nr.child_reviews.append(br)
                    nr.point_reviews.extend(br.point_reviews)
            # 把已完成的子节点审核结果加入上下文，供后续批次使用
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
