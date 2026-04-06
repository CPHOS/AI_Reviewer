"""conftest — 测试公共 fixture。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.client.base import ChatResponse, UsageStats
from src.model import (
    Correctness,
    EvalResult,
    NodeReview,
    PointReview,
    Problem,
    QuestionLevel,
    QuestionNode,
    Reasonableness,
    ReportMeta,
    ReviewResult,
    ScoringPoint,
    ScoringPointKind,
    SolutionMethod,
)

CASES_DIR = Path(__file__).parent / "cases"


@pytest.fixture
def orbit_tex_path() -> Path:
    return CASES_DIR / "顿翔文-卫星绕地" / "orbit.tex"


@pytest.fixture
def penning_tex_path() -> Path:
    return CASES_DIR / "常皓凌-潘宁阱" / "penning_trap.tex"


@pytest.fixture
def sample_scoring_point() -> ScoringPoint:
    return ScoringPoint(
        tag="1",
        score=2,
        kind=ScoringPointKind.EQUATION,
        content=r"a=-\frac{GMm}{2E} \eqtagscore{1}{2}",
    )


@pytest.fixture
def sample_node(sample_scoring_point: ScoringPoint) -> QuestionNode:
    method = SolutionMethod(
        index=0,
        scoring_points=[sample_scoring_point],
        raw_tex=r"\solsubsubq{1.1}{4} ...",
    )
    return QuestionNode(
        level=QuestionLevel.SUBSUBQ,
        number="1.1",
        score=4,
        statement=r"\subsubq{1.1}给出前四个轨道根数与能量$E$，角动量$\vec L$的关系。",
        methods=[method],
        solution_tex=r"\solsubsubq{1.1}{4} ...",
    )


@pytest.fixture
def sample_problem(sample_node: QuestionNode) -> Problem:
    parent = QuestionNode(
        level=QuestionLevel.SUBQ,
        number="1",
        score=21,
        statement=r"\subq{1} 轨道根数...",
        children=[sample_node],
    )
    return Problem(
        title="测试题目",
        total_score=50,
        statement="这是一道测试题目的题干。",
        solution_tex="这是解答文本。",
        children=[parent],
    )


@pytest.fixture
def sample_point_review(sample_scoring_point: ScoringPoint) -> PointReview:
    return PointReview(
        point=sample_scoring_point,
        method_index=0,
        correctness=Correctness.CORRECT,
        correctness_comment="推导正确",
        reasonableness=Reasonableness.REASONABLE,
        reasonableness_comment="物理模型合理",
        computation_difficulty=3,
        thinking_difficulty=4,
    )


@pytest.fixture
def sample_node_review(
    sample_node: QuestionNode, sample_point_review: PointReview
) -> NodeReview:
    return NodeReview(
        node=sample_node,
        point_reviews=[sample_point_review],
        summary="该子题推导正确，物理思路清晰。",
    )


@pytest.fixture
def sample_review_result(
    sample_problem: Problem, sample_node_review: NodeReview
) -> ReviewResult:
    parent_review = NodeReview(
        node=sample_problem.children[0],
        point_reviews=[sample_node_review.point_reviews[0]],
        child_reviews=[sample_node_review],
        summary="第一大题整体正确。",
    )
    return ReviewResult(problem=sample_problem, node_reviews=[parent_review])


@pytest.fixture
def sample_eval_result() -> EvalResult:
    return EvalResult(
        computation_difficulty=6,
        thinking_difficulty=7,
        overall_difficulty=7,
        summary="这是一道计算与思维兼具的竞赛题。",
    )


@pytest.fixture
def sample_meta() -> ReportMeta:
    return ReportMeta(
        model="google/gemini-2.5-pro",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        timestamp="2026-04-06T12:00:00+00:00",
        elapsed_seconds=30.5,
    )


@pytest.fixture
def mock_client() -> MagicMock:
    """构造一个模拟的 OpenRouterClient。"""
    client = MagicMock()
    client.usage = UsageStats()
    return client
