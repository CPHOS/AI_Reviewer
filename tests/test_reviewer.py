"""review/reviewer.py 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.client.base import ChatResponse
from src.model import (
    Correctness,
    NodeReview,
    PointReview,
    QuestionLevel,
    QuestionNode,
    Reasonableness,
    ReviewResult,
    ScoringPoint,
    ScoringPointKind,
    SolutionMethod,
    Problem,
)
from src.review.reviewer import (
    _extract_tag_content,
    _format_points_list,
    _format_prior_reviews,
    _group_leaf_batches,
    _parse_review_blocks,
    _review_leaf_batch,
    review_method,
    summarize_node,
    review_node,
    detailed_review,
)


def _tag_response(tag, correctness="correct", cc="ok", reasonableness="reasonable",
                   rc="ok", comp=3, think=3):
    """Helper to build a <review> tag block for tests."""
    return (
        f'<review tag="{tag}">\n'
        f'<correctness>{correctness}</correctness>\n'
        f'<correctness_comment>{cc}</correctness_comment>\n'
        f'<reasonableness>{reasonableness}</reasonableness>\n'
        f'<reasonableness_comment>{rc}</reasonableness_comment>\n'
        f'<computation_difficulty>{comp}</computation_difficulty>\n'
        f'<thinking_difficulty>{think}</thinking_difficulty>\n'
        f'</review>'
    )


# ---------------------------------------------------------------------------
# _extract_tag_content
# ---------------------------------------------------------------------------


class TestExtractTagContent:
    def test_simple(self):
        assert _extract_tag_content("<foo>bar</foo>", "foo") == "bar"

    def test_with_whitespace(self):
        assert _extract_tag_content("<foo>  bar  </foo>", "foo") == "bar"

    def test_multiline(self):
        text = "<summary>\n这是\n多行内容\n</summary>"
        assert "多行" in _extract_tag_content(text, "summary")

    def test_not_found(self):
        assert _extract_tag_content("<foo>bar</foo>", "baz") == ""

    def test_latex_content(self):
        text = r"<correctness_comment>公式 $\vec{R}=\nabla\Phi$ 正确</correctness_comment>"
        result = _extract_tag_content(text, "correctness_comment")
        assert r"\vec{R}" in result
        assert r"\nabla" in result


# ---------------------------------------------------------------------------
# _parse_review_blocks
# ---------------------------------------------------------------------------


class TestParseReviewBlocks:
    def test_single_block(self):
        text = """
<review tag="eq:1">
<correctness>correct</correctness>
<correctness_comment>推导正确</correctness_comment>
<reasonableness>reasonable</reasonableness>
<reasonableness_comment>模型合理</reasonableness_comment>
<computation_difficulty>3</computation_difficulty>
<thinking_difficulty>4</thinking_difficulty>
</review>
""".strip()
        blocks = _parse_review_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["tag"] == "eq:1"
        assert blocks[0]["correctness"] == "correct"
        assert blocks[0]["computation_difficulty"] == "3"

    def test_multiple_blocks(self):
        text = """
<review tag="A">
<correctness>correct</correctness>
<correctness_comment>ok</correctness_comment>
<reasonableness>reasonable</reasonableness>
<reasonableness_comment>ok</reasonableness_comment>
<computation_difficulty>2</computation_difficulty>
<thinking_difficulty>3</thinking_difficulty>
</review>

<review tag="B">
<correctness>wrong</correctness>
<correctness_comment>错误</correctness_comment>
<reasonableness>questionable</reasonableness>
<reasonableness_comment>可疑</reasonableness_comment>
<computation_difficulty>7</computation_difficulty>
<thinking_difficulty>8</thinking_difficulty>
</review>
""".strip()
        blocks = _parse_review_blocks(text)
        assert len(blocks) == 2
        assert blocks[0]["tag"] == "A"
        assert blocks[1]["tag"] == "B"
        assert blocks[1]["correctness"] == "wrong"

    def test_latex_in_comments(self):
        r"""LaTeX 公式不需要转义，直接出现在标签内容中。"""
        text = r"""
<review tag="27">
<correctness>correct</correctness>
<correctness_comment>公式 $\vec{R}=\nabla\Phi$ 正确</correctness_comment>
<reasonableness>reasonable</reasonableness>
<reasonableness_comment>使用 $\rho$ 和 $\theta$ 表示</reasonableness_comment>
<computation_difficulty>5</computation_difficulty>
<thinking_difficulty>6</thinking_difficulty>
</review>
""".strip()
        blocks = _parse_review_blocks(text)
        assert len(blocks) == 1
        assert r"\vec{R}" in blocks[0]["correctness_comment"]
        assert r"\rho" in blocks[0]["reasonableness_comment"]

    def test_surrounding_text(self):
        """LLM 在标签前后加了解释文字，应能正确提取。"""
        text = """以下是审核结果：

<review tag="1">
<correctness>correct</correctness>
<correctness_comment>ok</correctness_comment>
<reasonableness>reasonable</reasonableness>
<reasonableness_comment>ok</reasonableness_comment>
<computation_difficulty>2</computation_difficulty>
<thinking_difficulty>2</thinking_difficulty>
</review>

以上就是结果。"""
        blocks = _parse_review_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["tag"] == "1"

    def test_no_blocks(self):
        blocks = _parse_review_blocks("没有任何标签的纯文本回复")
        assert blocks == []


# ---------------------------------------------------------------------------
# _format_prior_reviews
# ---------------------------------------------------------------------------


class TestFormatPriorReviews:
    def test_empty_list(self):
        assert _format_prior_reviews([]) == ""

    def test_single_node_review(self):
        pt = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        node = QuestionNode(
            level=QuestionLevel.SUBQ, number="(1)", score=5,
            methods=[SolutionMethod(index=0, scoring_points=[pt], raw_tex="...")],
        )
        pr = PointReview(
            point=pt,
            correctness=Correctness.CORRECT,
            reasonableness=Reasonableness.REASONABLE,
            computation_difficulty=3,
            thinking_difficulty=4,
        )
        nr = NodeReview(node=node, point_reviews=[pr], summary="推导正确")
        text = _format_prior_reviews([nr])
        assert "(1)" in text
        assert "correct" in text
        assert "3" in text
        assert "推导正确" in text

    def test_parse_failed_review(self):
        pt = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        node = QuestionNode(
            level=QuestionLevel.SUBQ, number="(1)", score=5,
            methods=[SolutionMethod(index=0, scoring_points=[pt], raw_tex="...")],
        )
        pr = PointReview(point=pt, parse_failed=True, raw_response="bad")
        nr = NodeReview(node=node, point_reviews=[pr])
        text = _format_prior_reviews([nr])
        assert "解析失败" in text

    def test_multiple_node_reviews(self):
        pt1 = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        pt2 = ScoringPoint(tag="2", score=3, kind=ScoringPointKind.TEXT, content="y")
        node1 = QuestionNode(
            level=QuestionLevel.SUBQ, number="(1)", score=2,
            methods=[SolutionMethod(index=0, scoring_points=[pt1], raw_tex="...")],
        )
        node2 = QuestionNode(
            level=QuestionLevel.SUBQ, number="(2)", score=3,
            methods=[SolutionMethod(index=0, scoring_points=[pt2], raw_tex="...")],
        )
        pr1 = PointReview(point=pt1, correctness=Correctness.CORRECT,
                          reasonableness=Reasonableness.REASONABLE,
                          computation_difficulty=2, thinking_difficulty=2)
        pr2 = PointReview(point=pt2, correctness=Correctness.WRONG,
                          reasonableness=Reasonableness.UNREASONABLE,
                          computation_difficulty=7, thinking_difficulty=8)
        nr1 = NodeReview(node=node1, point_reviews=[pr1], summary="ok")
        nr2 = NodeReview(node=node2, point_reviews=[pr2], summary="有误")
        text = _format_prior_reviews([nr1, nr2])
        assert "(1)" in text
        assert "(2)" in text
        assert "wrong" in text


# ---------------------------------------------------------------------------
# _format_points_list
# ---------------------------------------------------------------------------


class TestFormatPointsList:
    def test_single_point(self):
        pt = ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x=1")
        text = _format_points_list([pt])
        assert "编号: A" in text
        assert "分值: 2分" in text
        assert "x=1" in text

    def test_multiple_points(self):
        pts = [
            ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x"),
            ScoringPoint(tag="B", score=3, kind=ScoringPointKind.TEXT, content="y"),
        ]
        text = _format_points_list(pts)
        assert "1. 编号: A" in text
        assert "2. 编号: B" in text


# ---------------------------------------------------------------------------
# review_method
# ---------------------------------------------------------------------------


class TestReviewMethod:
    def test_batch_reviews_all_points(self, mock_client):
        """批量审核：一次请求返回所有评分点结果。"""
        pts = [
            ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x"),
            ScoringPoint(tag="B", score=3, kind=ScoringPointKind.TEXT, content="y"),
        ]
        method = SolutionMethod(index=1, scoring_points=pts, raw_tex="...")
        mock_client.chat.return_value = ChatResponse(
            content="""
<review tag="A">
<correctness>correct</correctness>
<correctness_comment>ok</correctness_comment>
<reasonableness>reasonable</reasonableness>
<reasonableness_comment>ok</reasonableness_comment>
<computation_difficulty>3</computation_difficulty>
<thinking_difficulty>4</thinking_difficulty>
</review>

<review tag="B">
<correctness>wrong</correctness>
<correctness_comment>错误</correctness_comment>
<reasonableness>questionable</reasonableness>
<reasonableness_comment>不当</reasonableness_comment>
<computation_difficulty>7</computation_difficulty>
<thinking_difficulty>8</thinking_difficulty>
</review>
""",
        )
        results = review_method(method, "ctx", "stmt", "sub", mock_client)
        assert len(results) == 2
        assert all(r.method_index == 1 for r in results)
        # 只调一次 LLM
        assert mock_client.chat.call_count == 1
        # 验证各评分点结果正确映射
        assert results[0].correctness == Correctness.CORRECT
        assert results[1].correctness == Correctness.WRONG
        assert results[1].computation_difficulty == 7
        # raw_response 始终保存
        assert all(r.raw_response != "" for r in results)

    def test_single_point_fallback(self, mock_client):
        """单评分点，按顺序匹配。"""
        pts = [ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x")]
        method = SolutionMethod(index=0, scoring_points=pts, raw_tex="...")
        mock_client.chat.return_value = ChatResponse(
            content="""
<review tag="A">
<correctness>minor_issue</correctness>
<correctness_comment>小问题</correctness_comment>
<reasonableness>reasonable</reasonableness>
<reasonableness_comment>ok</reasonableness_comment>
<computation_difficulty>2</computation_difficulty>
<thinking_difficulty>3</thinking_difficulty>
</review>
""",
        )
        results = review_method(method, "ctx", "stmt", "sub", mock_client)
        assert len(results) == 1
        assert results[0].correctness == Correctness.MINOR_ISSUE
        mock_client.chat.call_count == 1

    def test_no_tags_marks_parse_failed(self, mock_client):
        """LLM 返回无标签文本时，重试后标记 parse_failed 并保存原始输出。"""
        pts = [
            ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x"),
            ScoringPoint(tag="B", score=3, kind=ScoringPointKind.TEXT, content="y"),
        ]
        method = SolutionMethod(index=0, scoring_points=pts, raw_tex="...")
        raw_text = "这是一段无法解析的自然语言回复，模型没有按要求输出格式。"
        mock_client.chat.return_value = ChatResponse(content=raw_text)
        results = review_method(method, "ctx", "stmt", "sub", mock_client)
        assert len(results) == 2
        assert all(r.parse_failed for r in results)
        assert all(r.raw_response == raw_text for r in results)
        assert all(r.correctness is None for r in results)
        assert all(r.computation_difficulty == 0 for r in results)
        # 默认 max_parse_retries=2，共 3 次请求
        assert mock_client.chat.call_count == 3

    def test_retry_then_succeed(self, mock_client):
        """首次解析失败、重试后成功，不标记 parse_failed。"""
        pts = [ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x")]
        method = SolutionMethod(index=0, scoring_points=pts, raw_tex="...")
        good_content = _tag_response("A", comp=5, think=6)
        mock_client.chat.side_effect = [
            ChatResponse(content="无法解析的内容"),
            ChatResponse(content=good_content),
        ]
        results = review_method(method, "ctx", "stmt", "sub", mock_client)
        assert len(results) == 1
        assert not results[0].parse_failed
        assert results[0].computation_difficulty == 5
        assert results[0].raw_response == good_content
        assert mock_client.chat.call_count == 2

    def test_empty_method(self, mock_client):
        method = SolutionMethod(index=0, scoring_points=[], raw_tex="...")
        results = review_method(method, "ctx", "stmt", "sub", mock_client)
        assert len(results) == 0
        mock_client.chat.assert_not_called()


# ---------------------------------------------------------------------------
# summarize_node
# ---------------------------------------------------------------------------


class TestSummarizeNode:
    def test_returns_stripped_content(
        self, sample_node, sample_point_review, mock_client
    ):
        mock_client.chat.return_value = ChatResponse(
            content="  该子题推导正确且物理思路清晰。  "
        )
        summary = summarize_node(
            sample_node, [sample_point_review], mock_client
        )
        assert summary == "该子题推导正确且物理思路清晰。"
        mock_client.chat.assert_called_once()


# ---------------------------------------------------------------------------
# review_node
# ---------------------------------------------------------------------------


class TestReviewNode:
    def test_leaf_node(self, mock_client):
        pt = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        method = SolutionMethod(index=0, scoring_points=[pt], raw_tex="...")
        node = QuestionNode(
            level=QuestionLevel.SUBSUBQ,
            number="1.1",
            score=4,
            methods=[method],
            solution_tex="...",
        )
        # 第一次调用 review_method（批量），第二次调用 summarize_node
        mock_client.chat.side_effect = [
            ChatResponse(
                content=_tag_response("1", comp=3, think=3)
            ),
            ChatResponse(content="小结文字"),
        ]
        nr = review_node(node, "ctx", "stmt", mock_client)
        assert len(nr.point_reviews) == 1
        assert nr.summary == "小结文字"
        assert nr.child_reviews == []

    def test_parent_node_recurses(self, mock_client):
        child_pt = ScoringPoint(
            tag="1", score=1, kind=ScoringPointKind.EQUATION, content="x"
        )
        child_method = SolutionMethod(index=0, scoring_points=[child_pt], raw_tex="...")
        child = QuestionNode(
            level=QuestionLevel.SUBSUBQ,
            number="1.1",
            score=4,
            methods=[child_method],
            solution_tex="...",
        )
        parent = QuestionNode(
            level=QuestionLevel.SUBQ,
            number="1",
            score=10,
            children=[child],
        )
        mock_client.chat.side_effect = [
            # review_method for child (batch)
            ChatResponse(
                content=_tag_response("1", comp=2, think=2)
            ),
            # summarize_node for child
            ChatResponse(content="子题小结"),
            # summarize_node for parent
            ChatResponse(content="父题小结"),
        ]
        nr = review_node(parent, "ctx", "stmt", mock_client)
        assert len(nr.child_reviews) == 1
        assert nr.child_reviews[0].summary == "子题小结"
        assert nr.summary == "父题小结"
        # parent 的 point_reviews 应包含子节点的
        assert len(nr.point_reviews) == 1


# ---------------------------------------------------------------------------
# detailed_review
# ---------------------------------------------------------------------------


class TestDetailedReview:
    def test_returns_review_result(self, sample_problem, mock_client):
        mock_client.chat.return_value = ChatResponse(
            content=_tag_response("eq:1", comp=5, think=5)
        )
        result = detailed_review(sample_problem, mock_client)
        assert isinstance(result, ReviewResult)
        assert result.problem is sample_problem
        assert len(result.node_reviews) == 1


# ---------------------------------------------------------------------------
# _group_leaf_batches
# ---------------------------------------------------------------------------


class TestGroupLeafBatches:
    def _leaf(self, number: str, n_points: int) -> QuestionNode:
        """创建一个带 n_points 个评分点的叶节点。"""
        pts = [
            ScoringPoint(tag=f"{number}-SP{i}", score=1,
                         kind=ScoringPointKind.EQUATION, content="x")
            for i in range(n_points)
        ]
        return QuestionNode(
            level=QuestionLevel.SUBQ, number=number, score=n_points,
            methods=[SolutionMethod(index=0, scoring_points=pts)],
        )

    def _parent(self, number: str) -> QuestionNode:
        """创建一个非叶节点（有子节点）。"""
        child = self._leaf(f"{number}.1", 1)
        return QuestionNode(
            level=QuestionLevel.SUBQ, number=number, score=5,
            children=[child],
        )

    def test_disabled_when_zero(self):
        children = [self._leaf("1", 1), self._leaf("2", 1)]
        groups = _group_leaf_batches(children, 0)
        assert len(groups) == 2
        assert all(len(g) == 1 for g in groups)

    def test_single_batch(self):
        children = [self._leaf("1", 1), self._leaf("2", 1), self._leaf("3", 1)]
        groups = _group_leaf_batches(children, 4)
        # 总共 3 个评分点 < 4，全部归入一组
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_splits_at_threshold(self):
        children = [self._leaf("1", 2), self._leaf("2", 2), self._leaf("3", 2)]
        groups = _group_leaf_batches(children, 4)
        # (1)+(2) = 4 ≥ 4 → 切分; (3) = 2 → 残余组
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1

    def test_non_leaf_breaks_batch(self):
        children = [self._leaf("1", 1), self._parent("2"), self._leaf("3", 1)]
        groups = _group_leaf_batches(children, 4)
        # (1) 叶 → 被非叶 (2) 切断 → 独立组; (2) 非叶独立; (3) 叶独立
        assert len(groups) == 3
        assert len(groups[0]) == 1  # leaf (1)
        assert len(groups[1]) == 1  # parent (2)
        assert len(groups[2]) == 1  # leaf (3)

    def test_large_leaf_alone(self):
        children = [self._leaf("1", 8)]
        groups = _group_leaf_batches(children, 4)
        # 单个大叶节点 ≥ 阈值，独立成组
        assert len(groups) == 1
        assert len(groups[0]) == 1


# ---------------------------------------------------------------------------
# _review_leaf_batch
# ---------------------------------------------------------------------------


class TestReviewLeafBatch:
    def test_batch_reviews_multiple_nodes(self, mock_client):
        """两个叶节点合并为一次 LLM 审核调用，小结仍各自生成。"""
        pt1 = ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x")
        pt2 = ScoringPoint(tag="B", score=3, kind=ScoringPointKind.TEXT, content="y")
        node1 = QuestionNode(
            level=QuestionLevel.SUBQ, number="(1)", score=2,
            methods=[SolutionMethod(index=0, scoring_points=[pt1])],
            solution_tex="sol1",
        )
        node2 = QuestionNode(
            level=QuestionLevel.SUBQ, number="(2)", score=3,
            methods=[SolutionMethod(index=0, scoring_points=[pt2])],
            solution_tex="sol2",
        )
        mock_client.chat.side_effect = [
            # 合并审核 LLM 调用
            ChatResponse(content=_tag_response("A") + "\n" + _tag_response("B", correctness="wrong")),
            # node1 小结
            ChatResponse(content="小结1"),
            # node2 小结
            ChatResponse(content="小结2"),
        ]
        results = _review_leaf_batch([node1, node2], "ctx", "stmt", mock_client)
        assert len(results) == 2
        assert results[0].node is node1
        assert results[1].node is node2
        assert len(results[0].point_reviews) == 1
        assert len(results[1].point_reviews) == 1
        assert results[0].point_reviews[0].correctness == Correctness.CORRECT
        assert results[1].point_reviews[0].correctness == Correctness.WRONG
        assert results[0].summary == "小结1"
        assert results[1].summary == "小结2"
        # 1 次审核 + 2 次小结 = 3 次 LLM 调用
        assert mock_client.chat.call_count == 3

    def test_empty_points_returns_empty_reviews(self, mock_client):
        """无评分点的节点直接返回空 NodeReview。"""
        node = QuestionNode(
            level=QuestionLevel.SUBQ, number="(1)", score=0,
            methods=[SolutionMethod(index=0, scoring_points=[])],
        )
        results = _review_leaf_batch([node], "ctx", "stmt", mock_client)
        assert len(results) == 1
        assert results[0].point_reviews == []
        mock_client.chat.assert_not_called()

    def test_parse_failure_marks_all(self, mock_client):
        """合并审核解析失败时，所有评分点标记 parse_failed。"""
        pt1 = ScoringPoint(tag="A", score=1, kind=ScoringPointKind.EQUATION, content="x")
        pt2 = ScoringPoint(tag="B", score=1, kind=ScoringPointKind.EQUATION, content="y")
        node1 = QuestionNode(
            level=QuestionLevel.SUBQ, number="(1)", score=1,
            methods=[SolutionMethod(index=0, scoring_points=[pt1])],
        )
        node2 = QuestionNode(
            level=QuestionLevel.SUBQ, number="(2)", score=1,
            methods=[SolutionMethod(index=0, scoring_points=[pt2])],
        )
        mock_client.chat.return_value = ChatResponse(content="无法解析的内容")
        results = _review_leaf_batch([node1, node2], "ctx", "stmt", mock_client)
        assert len(results) == 2
        assert all(pr.parse_failed for nr in results for pr in nr.point_reviews)
        # 默认 max_parse_retries=2，共 3 次审核请求 + 2 次小结 = 5
        assert mock_client.chat.call_count == 5


# ---------------------------------------------------------------------------
# review_node with batching
# ---------------------------------------------------------------------------


class TestReviewNodeBatching:
    def test_batching_reduces_llm_calls(self, mock_client):
        """batch_min_points > 0 时，相邻叶节点合并为一次审核调用。"""
        from unittest.mock import patch
        from src.config import AppConfig, LLMConfig

        pt1 = ScoringPoint(tag="A", score=1, kind=ScoringPointKind.EQUATION, content="x")
        pt2 = ScoringPoint(tag="B", score=1, kind=ScoringPointKind.EQUATION, content="y")
        child1 = QuestionNode(
            level=QuestionLevel.SUBSUBQ, number="1.1", score=1,
            methods=[SolutionMethod(index=0, scoring_points=[pt1])],
            solution_tex="sol1",
        )
        child2 = QuestionNode(
            level=QuestionLevel.SUBSUBQ, number="1.2", score=1,
            methods=[SolutionMethod(index=0, scoring_points=[pt2])],
            solution_tex="sol2",
        )
        parent = QuestionNode(
            level=QuestionLevel.SUBQ, number="1", score=10,
            children=[child1, child2],
        )

        cfg = AppConfig(llm=LLMConfig(batch_min_points=4))
        mock_client.chat.side_effect = [
            # 合并审核 (A + B)
            ChatResponse(content=_tag_response("A") + "\n" + _tag_response("B")),
            # child1 小结
            ChatResponse(content="小结1"),
            # child2 小结
            ChatResponse(content="小结2"),
            # parent 小结
            ChatResponse(content="父小结"),
        ]
        with patch("src.review.reviewer.get_config", return_value=cfg):
            nr = review_node(parent, "ctx", "stmt", mock_client)

        assert len(nr.child_reviews) == 2
        assert nr.child_reviews[0].summary == "小结1"
        assert nr.child_reviews[1].summary == "小结2"
        assert nr.summary == "父小结"
        # 1 合并审核 + 2 子小结 + 1 父小结 = 4 次调用
        assert mock_client.chat.call_count == 4
