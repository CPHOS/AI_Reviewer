"""通用数据模型。

层级关系（题干 / 解答共用同一棵树）::

    Problem
    ├── Part  (\\pmark / \\solPart)          ← 可选层级
    │   ├── SubQ  (\\subq / \\solsubq)       ← 一级小问
    │   │   ├── SubSubQ  (\\subsubq / \\solsubsubq)
    │   │   │   └── SubSubSubQ  (\\subsubsubq / \\solsubsubsubq)
    │   │   └── ...
    │   └── ...
    └── SubQ  (当题目不含 Part 时直接挂载)

评分标记：
    - \\eqtagscore{编号}{分值}  方程评分点
    - \\eqtag{编号}             仅编号，不计分
    - \\addtext{描述}{分值}     文字评分点

多解法：
    multisol 环境，每个 \\item 为一种解法。
    第一种解法的分值计入总分，后续解法仅展示、不重复累加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# 评分点层级
# ---------------------------------------------------------------------------


class ScoringPointKind(Enum):
    """评分点类型。"""

    EQUATION = "equation"
    """方程评分点 (\\eqtagscore)。"""
    TEXT = "text"
    """文字评分点 (\\addtext)。"""


@dataclass
class ScoringPoint:
    """单个评分点。"""

    tag: str
    """评分点编号标签（\\eqtagscore / \\eqtag 的第一个参数，或 \\addtext 的描述）。"""
    score: int
    """分值（\\eqtag 时为 0）。"""
    kind: ScoringPointKind
    """评分点类型。"""
    content: str
    """评分点所在完整 LaTeX 片段（含公式环境或文字描述）。"""
    label: str = ""
    """\\label 标签（如有）。"""


# ---------------------------------------------------------------------------
# 多解法
# ---------------------------------------------------------------------------


@dataclass
class SolutionMethod:
    """一种解法（对应 multisol 中的一个 \\item，或默认唯一解法）。

    当题目不含 multisol 时，每个层级节点恰好有一个 ``SolutionMethod``。
    """

    index: int = 0
    """解法序号（从 0 开始；0 表示第一种 / 默认解法）。"""
    label: str = ""
    """解法标签，如 '解法一'。"""
    scoring_points: list[ScoringPoint] = field(default_factory=list)
    """该解法下的全部评分点。"""
    raw_tex: str = ""
    """该解法对应的原始 LaTeX 片段。"""


# ---------------------------------------------------------------------------
# 题目层级结构（四级小问）
# ---------------------------------------------------------------------------


class QuestionLevel(Enum):
    """小问层级。"""

    PART = "part"
    """\\pmark / \\solPart — Part 级别。"""
    SUBQ = "subq"
    """\\subq / \\solsubq — 一级小问 (1), (2), ...。"""
    SUBSUBQ = "subsubq"
    """\\subsubq / \\solsubsubq — 二级小问 (i), (ii), ...。"""
    SUBSUBSUBQ = "subsubsubq"
    """\\subsubsubq / \\solsubsubsubq — 三级小问 (a), (b), ...。"""


@dataclass
class QuestionNode:
    """题目树的一个层级节点。

    同时承载题干信息和解答信息，通过 ``level`` 区分层级。
    叶节点的 ``children`` 为空列表。
    """

    level: QuestionLevel
    """层级类型。"""
    number: str
    """编号参数（原样保留），如 'A', '1', '2.1', 'i', 'a'。"""
    score: int = 0
    """该节点声明的分值（\\solsubq 等的第二个参数）。"""

    # 题干侧
    statement: str = ""
    """题干文本（problemstatement 中对应片段）。"""

    # 解答侧
    methods: list[SolutionMethod] = field(default_factory=list)
    """解法列表。无 multisol 时长度为 1。"""
    solution_tex: str = ""
    """解答原始 LaTeX 片段。"""
    method_label: str = ""
    """多解法拆分后的解法标签，如 '解法1'。空字符串表示非多解法节点。"""

    # 子层级
    children: list[QuestionNode] = field(default_factory=list)
    """下一级小问列表。"""


# ---------------------------------------------------------------------------
# 题目顶层
# ---------------------------------------------------------------------------


@dataclass
class Problem:
    """一道完整的题目（对应一个 problem 环境）。"""

    title: str
    """题目标题（\\begin{problem} 的必需参数）。"""
    total_score: int
    """总分（\\begin{problem} 的可选参数，未声明时为 0）。"""

    statement: str
    """题干完整 LaTeX 文本（problemstatement 环境内容）。"""
    solution_tex: str = ""
    """解答完整 LaTeX 文本（solution 环境内容）。"""

    children: list[QuestionNode] = field(default_factory=list)
    """顶层小问 / Part 列表。"""

    raw_tex: str = ""
    """原始 .tex 文件完整内容。"""


# ---------------------------------------------------------------------------
# 审核结果
# ---------------------------------------------------------------------------


class Correctness(Enum):
    """数学正确性判定。"""

    CORRECT = "correct"
    MINOR_ISSUE = "minor_issue"
    WRONG = "wrong"


class Reasonableness(Enum):
    """物理模型合理性判定。"""

    REASONABLE = "reasonable"
    QUESTIONABLE = "questionable"
    UNREASONABLE = "unreasonable"


@dataclass
class PointReview:
    """单个评分点的审核结果。"""

    point: ScoringPoint
    method_index: int = 0
    """所属解法序号（对应 SolutionMethod.index）。"""
    correctness: Correctness | None = None
    correctness_comment: str = ""
    reasonableness: Reasonableness | None = None
    reasonableness_comment: str = ""
    computation_difficulty: int = 0
    """计算难度 1-10。"""
    thinking_difficulty: int = 0
    """思维难度 1-10。"""
    parse_failed: bool = False
    """LLM 回复是否解析失败。"""
    raw_response: str = ""
    """解析失败时保存的 LLM 原始输出。"""


@dataclass
class NodeReview:
    """一个层级节点（小问）的审核结果。"""

    node: QuestionNode
    point_reviews: list[PointReview] = field(default_factory=list)
    """该节点所有解法的评分点审核结果。"""
    child_reviews: list[NodeReview] = field(default_factory=list)
    """子层级的审核结果（递归）。"""
    summary: str = ""
    """该子题的审核小结文字。"""


@dataclass
class ReviewResult:
    """整道题目的细致审核结果。"""

    problem: Problem
    node_reviews: list[NodeReview] = field(default_factory=list)
    """顶层节点审核结果列表。"""


@dataclass
class EvalResult:
    """汇总评估结果。"""

    computation_difficulty: int
    """整体计算难度 1-10。"""
    thinking_difficulty: int
    """整体思维难度 1-10。"""
    overall_difficulty: int
    """综合难度 1-10。"""
    summary: str
    """总结性评估文字。"""


# ---------------------------------------------------------------------------
# 报告元数据
# ---------------------------------------------------------------------------


@dataclass
class ReportMeta:
    """报告元参数，随输出一起记录。"""

    model: str = ""
    """使用的模型（服务商-模型 格式，如 openrouter/anthropic/claude-sonnet-4）。"""
    prompt_tokens: int = 0
    """总 prompt token 用量。"""
    completion_tokens: int = 0
    """总 completion token 用量。"""
    total_tokens: int = 0
    """总 token 用量。"""
    timestamp: str = ""
    """报告生成的 ISO 8601 时间戳。"""
    elapsed_seconds: float = 0.0
    """审核流程总耗时（秒）。"""
