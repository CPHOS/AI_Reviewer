r"""TeX 文件解析器。

负责将 CPHOS .tex 文件拆分为结构化的 ``Problem`` 对象。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from src.model import (
    Problem,
    QuestionLevel,
    QuestionNode,
    ScoringPoint,
    ScoringPointKind,
    SolutionMethod,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 正则常量
# ---------------------------------------------------------------------------

_RE_PROBLEM = re.compile(r"\\begin\{problem\}(?:\[(\d+)\])?\{(.+?)\}")
_RE_STMT = re.compile(
    r"\\begin\{problemstatement\}(.*?)\\end\{problemstatement\}", re.DOTALL
)
_RE_SOL = re.compile(r"\\begin\{solution\}(.*?)\\end\{solution\}", re.DOTALL)

# 题干层级标记
_STMT_MARKERS: list[tuple[re.Pattern, QuestionLevel]] = [
    (re.compile(r"\\pmark\{(.+?)\}"), QuestionLevel.PART),
    (re.compile(r"\\subq\{(.+?)\}"), QuestionLevel.SUBQ),
    (re.compile(r"\\subsubq\{(.+?)\}"), QuestionLevel.SUBSUBQ),
    (re.compile(r"\\subsubsubq\{(.+?)\}"), QuestionLevel.SUBSUBSUBQ),
]

# 解答层级标记（带分值）
_SOL_MARKERS: list[tuple[re.Pattern, QuestionLevel]] = [
    (re.compile(r"\\solPart\{(.+?)\}\{(\d+)\}"), QuestionLevel.PART),
    (re.compile(r"\\solsubq\{(.+?)\}\{(\d+)\}"), QuestionLevel.SUBQ),
    (re.compile(r"\\solsubsubq\{(.+?)\}\{(\d+)\}"), QuestionLevel.SUBSUBQ),
    (re.compile(r"\\solsubsubsubq\{(.+?)\}\{(\d+)\}"), QuestionLevel.SUBSUBSUBQ),
]

# 评分点
_RE_EQTAGSCORE = re.compile(r"\\eqtagscore\{(.+?)\}\{(\d+)\}")
_RE_ADDTEXT = re.compile(r"\\addtext\{(.+?)\}\{(\d+)\}")

# multisol
_RE_MULTISOL_BEGIN = re.compile(r"\\begin\{multisol\}(?:\[(.+?)\])?")
_RE_MULTISOL_END = re.compile(r"\\end\{multisol\}")


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

# 层级优先级：PART < SUBQ < SUBSUBQ < SUBSUBSUBQ
_LEVEL_ORDER = {
    QuestionLevel.PART: 0,
    QuestionLevel.SUBQ: 1,
    QuestionLevel.SUBSUBQ: 2,
    QuestionLevel.SUBSUBSUBQ: 3,
}


def _find_all_markers(text: str, markers: list[tuple[re.Pattern, QuestionLevel]]):
    """在 text 中查找所有层级标记，按出现位置排序。

    返回 list[(pos, level, number, score_or_None)]。
    """
    hits: list[tuple[int, QuestionLevel, str, int | None]] = []
    for pat, level in markers:
        for m in pat.finditer(text):
            number = m.group(1)
            score = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
            hits.append((m.start(), level, number, score))
    hits.sort(key=lambda h: h[0])
    return hits


def _extract_scoring_points(tex: str) -> list[ScoringPoint]:
    """从一段 LaTeX 片段中提取所有评分点（eqtagscore + addtext）。"""
    points: list[ScoringPoint] = []
    for m in _RE_EQTAGSCORE.finditer(tex):
        points.append(
            ScoringPoint(
                tag=m.group(1),
                score=int(m.group(2)),
                kind=ScoringPointKind.EQUATION,
                content=_surrounding_context(tex, m.start(), m.end()),
            )
        )
    for m in _RE_ADDTEXT.finditer(tex):
        points.append(
            ScoringPoint(
                tag=m.group(1),
                score=int(m.group(2)),
                kind=ScoringPointKind.TEXT,
                content=m.group(1),
            )
        )
    # 按出现位置排序
    return points


def _surrounding_context(text: str, start: int, end: int, radius: int = 200) -> str:
    """取评分点附近的上下文片段。"""
    ctx_start = max(0, start - radius)
    ctx_end = min(len(text), end + radius)
    return text[ctx_start:ctx_end].strip()


def _parse_methods(tex: str) -> list[SolutionMethod]:
    """解析一段解答文本中的解法列表。

    如果包含 multisol 环境，按 \\item 拆分为多个 SolutionMethod；
    否则整段作为唯一解法。
    """
    begin = _RE_MULTISOL_BEGIN.search(tex)
    if not begin:
        pts = _extract_scoring_points(tex)
        return [SolutionMethod(index=0, label="", scoring_points=pts, raw_tex=tex)]

    end = _RE_MULTISOL_END.search(tex, begin.end())
    if not end:
        pts = _extract_scoring_points(tex)
        return [SolutionMethod(index=0, label="", scoring_points=pts, raw_tex=tex)]

    # multisol 之前的部分也可能有评分点
    pre_tex = tex[: begin.start()]
    pre_pts = _extract_scoring_points(pre_tex)

    inner = tex[begin.end(): end.start()]
    # 按 \item 拆分
    items = re.split(r"\\item\b", inner)
    # 第一个 split 结果通常是空或前导文本
    items = [it for it in items if it.strip()]

    methods: list[SolutionMethod] = []
    for idx, item_tex in enumerate(items):
        pts = list(pre_pts) if idx == 0 else []
        pts.extend(_extract_scoring_points(item_tex))
        methods.append(
            SolutionMethod(
                index=idx,
                label=f"解法{idx + 1}",
                scoring_points=pts,
                raw_tex=item_tex.strip(),
            )
        )

    # multisol 之后的部分的评分点归入第一种解法
    post_tex = tex[end.end():]
    post_pts = _extract_scoring_points(post_tex)
    if post_pts and methods:
        methods[0].scoring_points.extend(post_pts)

    return methods or [SolutionMethod(index=0, scoring_points=pre_pts, raw_tex=tex)]


def _build_tree_from_hits(
    hits: list[tuple[int, QuestionLevel, str, int | None]],
    text: str,
    text_end: int,
) -> list[QuestionNode]:
    """根据排好序的标记构建层级树。

    每个标记的文本范围从自身位置到下一个同级或更高级标记（或末尾）。
    """
    if not hits:
        return []

    nodes: list[QuestionNode] = []
    for i, (pos, level, number, score) in enumerate(hits):
        # 确定该标记的文本结束位置
        end = text_end
        for j in range(i + 1, len(hits)):
            _, next_level, _, _ = hits[j]
            if _LEVEL_ORDER[next_level] <= _LEVEL_ORDER[level]:
                end = hits[j][0]
                break

        segment = text[pos:end]
        # 收集该标记范围内层级更深的子标记
        child_hits = [
            h for h in hits[i + 1:]
            if h[0] < end and _LEVEL_ORDER[h[1]] > _LEVEL_ORDER[level]
        ]

        node = QuestionNode(
            level=level,
            number=number,
            score=score or 0,
            statement=segment,
        )

        if child_hits:
            node.children = _build_tree_from_hits(child_hits, text, end)
        nodes.append(node)

    # 去重：只保留直属子节点（即跳过已被递归收纳的更深层级）
    # 只保留与第一个 hit 同级或比它更高级的节点
    if nodes:
        top_level = min(_LEVEL_ORDER[n.level] for n in nodes)
        nodes = [n for n in nodes if _LEVEL_ORDER[n.level] == top_level]

    return nodes


def _match_solution_to_nodes(
    sol_hits: list[tuple[int, QuestionLevel, str, int | None]],
    sol_text: str,
    sol_end: int,
    nodes: list[QuestionNode],
) -> None:
    """将解答文本按层级标记匹配到已有的题干树节点，填充 score / methods / solution_tex。"""
    if not sol_hits:
        # 没有解答层级标记 → 整段解答作为所有节点共享（不太常见）
        for node in nodes:
            node.methods = _parse_methods(sol_text)
            node.solution_tex = sol_text
        return

    # 只处理当前层级中最顶层的 hits，避免重复处理已被递归收纳的子 hits
    top_level_order = min(_LEVEL_ORDER[h[1]] for h in sol_hits)
    top_hits = [h for h in sol_hits if _LEVEL_ORDER[h[1]] == top_level_order]

    for idx, (pos, level, number, score) in enumerate(top_hits):
        # 确定该标记的文本结束位置
        if idx + 1 < len(top_hits):
            end = top_hits[idx + 1][0]
        else:
            end = sol_end

        segment = sol_text[pos:end]

        # 找到对应的题干节点
        target = _find_node(nodes, level, number)
        if target is None:
            # 题干中没有对应节点 → 创建新节点
            target = QuestionNode(level=level, number=number, score=score or 0)
            nodes.append(target)
        else:
            if score is not None:
                target.score = score

        # 收集该范围内的子层级 sol_hits
        child_sol_hits = [
            h for h in sol_hits
            if h[0] > pos and h[0] < end and _LEVEL_ORDER[h[1]] > top_level_order
        ]

        if child_sol_hits:
            _match_solution_to_nodes(child_sol_hits, sol_text, end, target.children)
        else:
            # 叶节点 → 解析解法和评分点
            target.methods = _parse_methods(segment)
            target.solution_tex = segment


def _find_node(
    nodes: list[QuestionNode], level: QuestionLevel, number: str
) -> QuestionNode | None:
    """在节点列表中查找指定层级和编号的节点。"""
    for n in nodes:
        if n.level == level and n.number == number:
            return n
    return None


def _expand_multisol_nodes(nodes: list[QuestionNode]) -> list[QuestionNode]:
    """递归展开多解法节点：将含多个 method 的叶节点拆分为同级兄弟节点。

    例如，一个 number="1.2" 的节点有 2 种解法，展开后变成：
      - number="1.2", method_label="解法1", methods=[method0]
      - number="1.2", method_label="解法2", methods=[method1]
    """
    result: list[QuestionNode] = []
    for node in nodes:
        if node.children:
            node.children = _expand_multisol_nodes(node.children)
            result.append(node)
        elif len(node.methods) > 1:
            for method in node.methods:
                expanded = QuestionNode(
                    level=node.level,
                    number=node.number,
                    score=node.score,
                    statement=node.statement,
                    methods=[SolutionMethod(
                        index=0,
                        label=method.label,
                        scoring_points=method.scoring_points,
                        raw_tex=method.raw_tex,
                    )],
                    solution_tex=node.solution_tex,
                    method_label=method.label or f"解法{method.index + 1}",
                )
                result.append(expanded)
        else:
            result.append(node)
    return result


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def parse_tex(tex_path: str | Path) -> Problem:
    """解析 .tex 文件，返回结构化的 Problem 对象。"""
    path = Path(tex_path)
    raw = path.read_text(encoding="utf-8")

    # 1. 提取 problem 环境头
    m_prob = _RE_PROBLEM.search(raw)
    if not m_prob:
        raise ValueError(f"未找到 \\begin{{problem}} 环境: {path}")
    total_score = int(m_prob.group(1)) if m_prob.group(1) else 0
    title = m_prob.group(2).strip()

    # 2. 提取 problemstatement
    m_stmt = _RE_STMT.search(raw)
    stmt_text = m_stmt.group(1) if m_stmt else ""

    # 3. 提取 solution
    m_sol = _RE_SOL.search(raw)
    sol_text = m_sol.group(1) if m_sol else ""

    # 4. 题干解析 → 层级树
    stmt_hits = _find_all_markers(stmt_text, _STMT_MARKERS)
    children = _build_tree_from_hits(stmt_hits, stmt_text, len(stmt_text))

    # 5. 解答解析 → 匹配到题干树
    sol_hits = _find_all_markers(sol_text, _SOL_MARKERS)
    _match_solution_to_nodes(sol_hits, sol_text, len(sol_text), children)

    # 6. 多解法展开：将含多种解法的叶节点拆分为独立兄弟节点
    children = _expand_multisol_nodes(children)

    problem = Problem(
        title=title,
        total_score=total_score,
        statement=stmt_text.strip(),
        solution_tex=sol_text.strip(),
        children=children,
        raw_tex=raw,
    )

    logger.info(
        "解析完成: %s (总分=%d, 顶层节点=%d)",
        title, total_score, len(children),
    )
    return problem
