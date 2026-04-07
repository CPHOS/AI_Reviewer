"""eval/evaluator.py 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.client.base import ChatResponse
from src.eval.evaluator import (
    _collect_difficulty_stats,
    _collect_node_summaries,
    comprehensive_eval,
)
from src.model import (
    Correctness,
    EvalResult,
    NodeReview,
    PointReview,
    QuestionLevel,
    QuestionNode,
    Reasonableness,
    ScoringPoint,
    ScoringPointKind,
)


# ---------------------------------------------------------------------------
# _collect_difficulty_stats
# ---------------------------------------------------------------------------


class TestCollectDifficultyStats:
    def test_basic(self, sample_node_review):
        stats = _collect_difficulty_stats([sample_node_review])
        assert "评分点数量: 1" in stats
        assert "计算=3" in stats
        assert "思维=4" in stats

    def test_empty(self):
        node = QuestionNode(level=QuestionLevel.SUBQ, number="1", score=0)
        nr = NodeReview(node=node)
        stats = _collect_difficulty_stats([nr])
        assert "评分点数量: 0" in stats

    def test_nested(self):
        pt = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        pr = PointReview(
            point=pt,
            correctness=Correctness.CORRECT,
            reasonableness=Reasonableness.REASONABLE,
            computation_difficulty=6,
            thinking_difficulty=8,
        )
        child_node = QuestionNode(
            level=QuestionLevel.SUBSUBQ, number="1.1", score=4
        )
        child_nr = NodeReview(node=child_node, point_reviews=[pr])
        parent_node = QuestionNode(
            level=QuestionLevel.SUBQ, number="1", score=10
        )
        parent_nr = NodeReview(
            node=parent_node, child_reviews=[child_nr]
        )
        stats = _collect_difficulty_stats([parent_nr])
        assert "计算=6" in stats
        assert "思维=8" in stats


# ---------------------------------------------------------------------------
# _collect_node_summaries
# ---------------------------------------------------------------------------


class TestCollectNodeSummaries:
    def test_single_level(self, sample_node_review):
        text = _collect_node_summaries([sample_node_review])
        assert "(1.1)" in text
        assert "推导正确" in text

    def test_nested(self):
        child_node = QuestionNode(
            level=QuestionLevel.SUBSUBQ, number="1.1", score=4
        )
        child_nr = NodeReview(node=child_node, summary="子题很好")
        parent_node = QuestionNode(
            level=QuestionLevel.SUBQ, number="1", score=10
        )
        parent_nr = NodeReview(
            node=parent_node, child_reviews=[child_nr], summary="父题很好"
        )
        text = _collect_node_summaries([parent_nr])
        assert "(1)" in text
        assert "(1.1)" in text
        assert "子题很好" in text
        assert "父题很好" in text


# ---------------------------------------------------------------------------
# comprehensive_eval
# ---------------------------------------------------------------------------


class TestComprehensiveEval:
    def test_successful_eval(
        self, sample_problem, sample_review_result, mock_client
    ):
        mock_client.chat.return_value = ChatResponse(
            content=(
                "<computation_difficulty>7</computation_difficulty>\n"
                "<thinking_difficulty>8</thinking_difficulty>\n"
                "<overall_difficulty>8</overall_difficulty>\n"
                "<summary>一道好题</summary>"
            )
        )
        result = comprehensive_eval(
            sample_problem, sample_review_result, mock_client
        )
        assert isinstance(result, EvalResult)
        assert result.computation_difficulty == 7
        assert result.thinking_difficulty == 8
        assert result.overall_difficulty == 8
        assert result.summary == "一道好题"

    def test_no_tags_fallback(
        self, sample_problem, sample_review_result, mock_client
    ):
        mock_client.chat.return_value = ChatResponse(content="无法解析")
        result = comprehensive_eval(
            sample_problem, sample_review_result, mock_client
        )
        assert result.computation_difficulty == 5
        assert result.thinking_difficulty == 5
        assert result.overall_difficulty == 5
        assert result.summary == ""
        # 默认 max_parse_retries=2，共 3 次请求
        assert mock_client.chat.call_count == 3

    def test_retry_then_succeed(
        self, sample_problem, sample_review_result, mock_client
    ):
        good_content = (
            "<computation_difficulty>6</computation_difficulty>\n"
            "<thinking_difficulty>7</thinking_difficulty>\n"
            "<overall_difficulty>7</overall_difficulty>\n"
            "<summary>重试后成功</summary>"
        )
        mock_client.chat.side_effect = [
            ChatResponse(content="无效内容"),
            ChatResponse(content=good_content),
        ]
        result = comprehensive_eval(
            sample_problem, sample_review_result, mock_client
        )
        assert result.computation_difficulty == 6
        assert result.summary == "重试后成功"
        assert mock_client.chat.call_count == 2

    def test_partial_tags(
        self, sample_problem, sample_review_result, mock_client
    ):
        mock_client.chat.return_value = ChatResponse(
            content=(
                "<computation_difficulty>3</computation_difficulty>\n"
                "<summary>简单题</summary>"
            )
        )
        result = comprehensive_eval(
            sample_problem, sample_review_result, mock_client
        )
        assert result.computation_difficulty == 3
        assert result.thinking_difficulty == 5  # 默认值
        assert result.summary == "简单题"
