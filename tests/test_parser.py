"""preprocess/parser.py 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.model import QuestionLevel, QuestionNode, ScoringPoint, ScoringPointKind, SolutionMethod
from src.preprocess.parser import (
    _build_tree_from_hits,
    _expand_multisol_nodes,
    _extract_scoring_points,
    _find_all_markers,
    _parse_methods,
    parse_tex,
    _STMT_MARKERS,
    _SOL_MARKERS,
)


# ---------------------------------------------------------------------------
# _extract_scoring_points
# ---------------------------------------------------------------------------


class TestExtractScoringPoints:
    def test_eqtagscore(self):
        tex = r"a = b \eqtagscore{1}{3} and c = d \eqtagscore{2}{2}"
        pts = _extract_scoring_points(tex)
        assert len(pts) == 2
        assert pts[0].tag == "1"
        assert pts[0].score == 3
        assert pts[0].kind == ScoringPointKind.EQUATION
        assert pts[1].tag == "2"
        assert pts[1].score == 2

    def test_addtext(self):
        tex = r"\addtext{说明月球摄动效应更强}{3}"
        pts = _extract_scoring_points(tex)
        assert len(pts) == 1
        assert pts[0].tag == "说明月球摄动效应更强"
        assert pts[0].score == 3
        assert pts[0].kind == ScoringPointKind.TEXT
        assert pts[0].content == "说明月球摄动效应更强"

    def test_mixed(self):
        tex = r"x = 1 \eqtagscore{A}{5} then \addtext{描述}{2} more"
        pts = _extract_scoring_points(tex)
        assert len(pts) == 2
        tags = {p.tag for p in pts}
        assert tags == {"A", "描述"}

    def test_empty(self):
        pts = _extract_scoring_points(r"\eqtag{1} no score here")
        assert len(pts) == 0


# ---------------------------------------------------------------------------
# _find_all_markers
# ---------------------------------------------------------------------------


class TestFindAllMarkers:
    def test_stmt_markers(self):
        text = r"\subq{1} some text \subsubq{1.1} more \subq{2}"
        hits = _find_all_markers(text, _STMT_MARKERS)
        assert len(hits) == 3
        assert hits[0][2] == "1"  # number
        assert hits[0][1] == QuestionLevel.SUBQ
        assert hits[1][2] == "1.1"
        assert hits[1][1] == QuestionLevel.SUBSUBQ
        assert hits[2][2] == "2"
        assert hits[2][1] == QuestionLevel.SUBQ

    def test_sol_markers_with_score(self):
        text = r"\solsubq{1}{10} ... \solsubsubq{1.1}{4}"
        hits = _find_all_markers(text, _SOL_MARKERS)
        assert len(hits) == 2
        assert hits[0][3] == 10  # score
        assert hits[1][3] == 4


# ---------------------------------------------------------------------------
# _parse_methods
# ---------------------------------------------------------------------------


class TestParseMethods:
    def test_single_method(self):
        tex = r"x = 1 \eqtagscore{1}{2} and y = 2 \eqtagscore{2}{3}"
        methods = _parse_methods(tex)
        assert len(methods) == 1
        assert methods[0].index == 0
        assert len(methods[0].scoring_points) == 2

    def test_multisol(self):
        tex = (
            r"前置 \eqtagscore{0}{1} "
            r"\begin{multisol} "
            r"\item 解法一 \eqtagscore{1}{2} "
            r"\item 解法二 \eqtagscore{2}{3} "
            r"\end{multisol} "
            r"后置 \eqtagscore{3}{1}"
        )
        methods = _parse_methods(tex)
        assert len(methods) == 2
        assert methods[0].label == "解法1"
        assert methods[1].label == "解法2"
        # 解法1 应包含前置评分点 + 自身 + 后置评分点
        m0_tags = [p.tag for p in methods[0].scoring_points]
        assert "0" in m0_tags
        assert "1" in m0_tags
        assert "3" in m0_tags
        # 解法2 只包含自身
        m1_tags = [p.tag for p in methods[1].scoring_points]
        assert "2" in m1_tags
        assert "0" not in m1_tags


# ---------------------------------------------------------------------------
# _build_tree_from_hits
# ---------------------------------------------------------------------------


class TestBuildTree:
    def test_flat_subqs(self):
        text = r"\subq{1} q1 text \subq{2} q2 text \subq{3} q3 text end"
        hits = _find_all_markers(text, _STMT_MARKERS)
        nodes = _build_tree_from_hits(hits, text, len(text))
        assert len(nodes) == 3
        assert all(n.level == QuestionLevel.SUBQ for n in nodes)
        assert [n.number for n in nodes] == ["1", "2", "3"]

    def test_nested_hierarchy(self):
        text = r"\subq{1} q1 \subsubq{1.1} q11 \subsubq{1.2} q12 \subq{2} q2"
        hits = _find_all_markers(text, _STMT_MARKERS)
        nodes = _build_tree_from_hits(hits, text, len(text))
        assert len(nodes) == 2
        assert nodes[0].number == "1"
        assert len(nodes[0].children) == 2
        assert nodes[0].children[0].number == "1.1"
        assert nodes[0].children[1].number == "1.2"
        assert nodes[1].number == "2"
        assert len(nodes[1].children) == 0


# ---------------------------------------------------------------------------
# _expand_multisol_nodes
# ---------------------------------------------------------------------------


class TestExpandMultisolNodes:
    def test_single_method_unchanged(self):
        """单解法节点不展开。"""
        pt = ScoringPoint(tag="1", score=2, kind=ScoringPointKind.EQUATION, content="x")
        method = SolutionMethod(index=0, scoring_points=[pt], raw_tex="...")
        node = QuestionNode(
            level=QuestionLevel.SUBQ, number="1", score=5, methods=[method]
        )
        result = _expand_multisol_nodes([node])
        assert len(result) == 1
        assert result[0].method_label == ""

    def test_multisol_expands(self):
        """多解法节点展开为多个兄弟节点。"""
        pt_a = ScoringPoint(tag="A", score=2, kind=ScoringPointKind.EQUATION, content="x")
        pt_b = ScoringPoint(tag="B", score=3, kind=ScoringPointKind.EQUATION, content="y")
        m0 = SolutionMethod(index=0, label="解法1", scoring_points=[pt_a], raw_tex="tex0")
        m1 = SolutionMethod(index=1, label="解法2", scoring_points=[pt_b], raw_tex="tex1")
        node = QuestionNode(
            level=QuestionLevel.SUBSUBQ, number="1.2", score=10,
            statement="题干", methods=[m0, m1], solution_tex="sol",
        )
        result = _expand_multisol_nodes([node])
        assert len(result) == 2
        # 两个节点共享 number 和 score
        assert all(n.number == "1.2" for n in result)
        assert all(n.score == 10 for n in result)
        # 每个节点只有 1 个 method
        assert all(len(n.methods) == 1 for n in result)
        # method_label 正确
        assert result[0].method_label == "解法1"
        assert result[1].method_label == "解法2"
        # 展开后 method.index 都是 0
        assert all(n.methods[0].index == 0 for n in result)
        # 评分点正确
        assert result[0].methods[0].scoring_points[0].tag == "A"
        assert result[1].methods[0].scoring_points[0].tag == "B"

    def test_recursive_expansion(self):
        """递归展开嵌套结构中的多解法节点。"""
        pt_a = ScoringPoint(tag="A", score=1, kind=ScoringPointKind.EQUATION, content="x")
        pt_b = ScoringPoint(tag="B", score=1, kind=ScoringPointKind.EQUATION, content="y")
        m0 = SolutionMethod(index=0, label="解法1", scoring_points=[pt_a], raw_tex="")
        m1 = SolutionMethod(index=1, label="解法2", scoring_points=[pt_b], raw_tex="")
        child = QuestionNode(
            level=QuestionLevel.SUBSUBQ, number="1.1", score=5, methods=[m0, m1],
        )
        parent = QuestionNode(
            level=QuestionLevel.SUBQ, number="1", score=10, children=[child],
        )
        result = _expand_multisol_nodes([parent])
        assert len(result) == 1  # parent unchanged
        assert len(result[0].children) == 2  # child expanded


# ---------------------------------------------------------------------------
# parse_tex — 真实文件
# ---------------------------------------------------------------------------


class TestParseTexOrbit:
    def test_title_and_score(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        assert p.title == "卫星绕地"
        assert p.total_score == 50

    def test_top_level_children(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        # orbit.tex 有 3 个顶层 subq: 1, 2, 3
        assert len(p.children) == 3
        numbers = [c.number for c in p.children]
        assert numbers == ["1", "2", "3"]

    def test_subq1_has_subsubqs(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        q1 = p.children[0]
        assert q1.score == 21
        # 1.1 + 1.2(解法1) + 1.2(解法2) = 3 个子节点
        assert len(q1.children) >= 3
        assert q1.children[0].number == "1.1"
        assert q1.children[1].number == "1.2"

    def test_subq1_1_scoring_points(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        q11 = p.children[0].children[0]
        assert q11.score == 4
        assert len(q11.methods) == 1
        pts = q11.methods[0].scoring_points
        tags = [pt.tag for pt in pts]
        assert "1" in tags
        assert "2" in tags
        assert "3" in tags
        assert "4" in tags

    def test_subq1_2_has_multisol(self, orbit_tex_path: Path):
        """multisol 展开后 1.2 变成多个兄弟节点，每个只有 1 个解法。"""
        p = parse_tex(orbit_tex_path)
        q1 = p.children[0]
        # 找到所有 number == "1.2" 的子节点
        q12_nodes = [c for c in q1.children if c.number == "1.2"]
        assert len(q12_nodes) >= 2
        # 每个展开节点只有 1 个 method
        for node in q12_nodes:
            assert len(node.methods) == 1
        # 每个展开节点都有 method_label
        labels = [n.method_label for n in q12_nodes]
        assert "解法1" in labels
        assert "解法2" in labels

    def test_subq2_has_subsubqs(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        q2 = p.children[1]
        assert q2.score == 20
        assert len(q2.children) == 2
        assert q2.children[0].number == "2.1"
        assert q2.children[1].number == "2.2"

    def test_subq2_1_has_addtext(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        q21 = p.children[1].children[0]
        assert q21.score == 4
        pts = q21.methods[0].scoring_points
        text_pts = [p for p in pts if p.kind == ScoringPointKind.TEXT]
        assert len(text_pts) >= 1
        assert text_pts[0].tag == "说明月球摄动效应更强"

    def test_subq3_leaf(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        q3 = p.children[2]
        assert q3.score == 9
        assert len(q3.children) == 0
        assert len(q3.methods) >= 1

    def test_statement_not_empty(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        assert len(p.statement) > 100
        assert "卫星" in p.statement or "轨道" in p.statement

    def test_solution_not_empty(self, orbit_tex_path: Path):
        p = parse_tex(orbit_tex_path)
        assert len(p.solution_tex) > 100


class TestParseTexPenning:
    def test_title_and_score(self, penning_tex_path: Path):
        p = parse_tex(penning_tex_path)
        assert p.title == "潘宁阱"
        assert p.total_score == 40

    def test_top_level_children(self, penning_tex_path: Path):
        p = parse_tex(penning_tex_path)
        # penning_trap.tex 有 3 个顶层 subq: 1, 2, 3
        assert len(p.children) == 3

    def test_subq2_has_deep_nesting(self, penning_tex_path: Path):
        p = parse_tex(penning_tex_path)
        q2 = p.children[1]
        assert q2.score == 27
        # subq 2 has subsubqs 2.1, 2.2, 2.3
        assert len(q2.children) == 3


class TestParseTexErrors:
    def test_missing_problem_env(self, tmp_path: Path):
        tex = tmp_path / "bad.tex"
        tex.write_text(
            r"\begin{document} no problem here \end{document}",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="未找到"):
            parse_tex(tex)

    def test_no_solution(self, tmp_path: Path):
        tex = tmp_path / "nosol.tex"
        tex.write_text(
            r"\begin{problem}[10]{测试}"
            r"\begin{problemstatement}\subq{1}题\end{problemstatement}"
            r"\end{problem}",
            encoding="utf-8",
        )
        p = parse_tex(tex)
        assert p.title == "测试"
        assert p.solution_tex == ""
