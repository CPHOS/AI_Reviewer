"""formatter/output.py 单元测试。"""

from __future__ import annotations

import json

import pytest

from src.formatter.output import (
    _collect_all_point_reviews,
    format_json,
    format_markdown,
    format_output,
)
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
)


# ---------------------------------------------------------------------------
# _collect_all_point_reviews
# ---------------------------------------------------------------------------


class TestCollectAllPointReviews:
    def test_flat(self, sample_node_review):
        prs = _collect_all_point_reviews([sample_node_review])
        assert len(prs) == 1
        assert prs[0].point.tag == "1"

    def test_dedup(self, sample_node_review):
        # 同一个 review 出现两次不应重复
        parent_node = QuestionNode(
            level=QuestionLevel.SUBQ, number="1", score=10
        )
        parent_nr = NodeReview(
            node=parent_node,
            point_reviews=sample_node_review.point_reviews,
            child_reviews=[sample_node_review],
        )
        prs = _collect_all_point_reviews([parent_nr])
        assert len(prs) == 1

    def test_different_methods(self):
        pt = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        pr0 = PointReview(
            point=pt,
            method_index=0,
            correctness=Correctness.CORRECT,
            reasonableness=Reasonableness.REASONABLE,
        )
        pr1 = PointReview(
            point=pt,
            method_index=1,
            correctness=Correctness.WRONG,
            reasonableness=Reasonableness.UNREASONABLE,
        )
        node = QuestionNode(level=QuestionLevel.SUBQ, number="1", score=5)
        nr = NodeReview(node=node, point_reviews=[pr0, pr1])
        prs = _collect_all_point_reviews([nr])
        assert len(prs) == 2


# ---------------------------------------------------------------------------
# format_markdown
# ---------------------------------------------------------------------------


class TestFormatMarkdown:
    def test_contains_title(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert "# 题目审核报告：测试题目" in md

    def test_contains_overview_table(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert "| 总分 | 50 |" in md
        assert "| 计算难度 | 6/10 |" in md
        assert "| 思维难度 | 7/10 |" in md

    def test_contains_scoring_table(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert "## 评分点总览" in md
        assert "| 编号 | 类型 | 分值 |" in md

    def test_contains_meta(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert "google/gemini-2.5-pro" in md
        assert "1000" in md
        assert "30.5" in md

    def test_contains_summary(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert "## 综合评估" in md
        assert "计算与思维兼具" in md

    def test_contains_detail_section(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert "## 细致审核" in md


# ---------------------------------------------------------------------------
# format_json
# ---------------------------------------------------------------------------


class TestFormatJson:
    def test_structure(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert js["title"] == "测试题目"
        assert js["total_score"] == 50
        assert js["difficulty"]["computation"] == 6
        assert js["difficulty"]["thinking"] == 7
        assert js["difficulty"]["overall"] == 7

    def test_meta(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert js["meta"]["model"] == "google/gemini-2.5-pro"
        assert js["meta"]["prompt_tokens"] == 1000
        assert js["meta"]["elapsed_seconds"] == 30.5

    def test_scoring_points_list(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert isinstance(js["scoring_points"], list)
        assert len(js["scoring_points"]) >= 1
        sp = js["scoring_points"][0]
        assert "tag" in sp
        assert "correctness" in sp

    def test_node_reviews_list(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert isinstance(js["node_reviews"], list)
        assert len(js["node_reviews"]) == 1
        nr = js["node_reviews"][0]
        assert nr["number"] == "1"
        assert "children" in nr

    def test_serializable(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        # 确保能序列化为 JSON 字符串
        text = json.dumps(js, ensure_ascii=False)
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# format_output
# ---------------------------------------------------------------------------


class TestFormatOutput:
    def test_returns_tuple(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        md, js = format_output(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        assert isinstance(md, str)
        assert isinstance(js, dict)
        assert "# 题目审核报告" in md
        assert "title" in js


# ---------------------------------------------------------------------------
# parse_failed 场景
# ---------------------------------------------------------------------------


class TestParseFailed:
    @pytest.fixture()
    def failed_review_result(self, sample_problem):
        pt = ScoringPoint(tag="eq1", score=3, kind=ScoringPointKind.EQUATION, content="F=ma")
        pr = PointReview(
            point=pt,
            parse_failed=True,
            raw_response="这是一段无法解析的LLM原始输出。",
        )
        node = QuestionNode(level=QuestionLevel.SUBQ, number="1", score=10)
        nr = NodeReview(node=node, point_reviews=[pr], summary="小结")
        return ReviewResult(problem=sample_problem, node_reviews=[nr])

    def test_md_shows_parse_failed(
        self, sample_problem, failed_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, failed_review_result, sample_eval_result, sample_meta
        )
        assert "解析失败" in md
        assert "⛔" in md
        assert "LLM 原始回复" in md
        assert "这是一段无法解析的LLM原始输出" in md

    def test_md_table_shows_failed(
        self, sample_problem, failed_review_result, sample_eval_result, sample_meta
    ):
        md = format_markdown(
            sample_problem, failed_review_result, sample_eval_result, sample_meta
        )
        assert "⛔ | ⛔" in md

    def test_json_shows_parse_failed(
        self, sample_problem, failed_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, failed_review_result, sample_eval_result, sample_meta
        )
        sp = js["scoring_points"][0]
        assert sp["parse_failed"] is True
        assert "raw_response" in sp
        assert sp["raw_response"] == "这是一段无法解析的LLM原始输出。"
        assert "correctness" not in sp

    def test_json_normal_has_no_raw(
        self, sample_problem, sample_review_result, sample_eval_result, sample_meta
    ):
        js = format_json(
            sample_problem, sample_review_result, sample_eval_result, sample_meta
        )
        sp = js["scoring_points"][0]
        assert sp["parse_failed"] is False
        assert "raw_response" not in sp
        assert "correctness" in sp
