"""state.py 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.client.base import ChatResponse, UsageStats
from src.config import AppConfig, LLMConfig, init_config
from src.state import State, StateMachine


@pytest.fixture(autouse=True)
def _setup_config(tmp_path):
    """为每个测试初始化配置，输出目录指向 tmp_path。"""
    init_config(
        llm=LLMConfig(
            provider="openrouter",
            model="test-model",
            api_keys=["sk-test"],
            base_url="https://test.api",
        ),
        output_dir=str(tmp_path / "output"),
    )
    return tmp_path


class TestStateMachineInit:
    def test_initial_state(self):
        sm = StateMachine()
        assert sm.state == State.INIT

    def test_transition(self):
        sm = StateMachine()
        sm._transition(State.PREPROCESSING)
        assert sm.state == State.PREPROCESSING


class TestStateMachineRun:
    def _mock_all(self, mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, tmp_path):
        """统一配置所有 mock。"""
        from src.model import (
            EvalResult,
            Problem,
            ReviewResult,
        )

        problem = Problem(
            title="测试", total_score=10, statement="题干"
        )
        mock_parse.return_value = problem

        review = ReviewResult(problem=problem, node_reviews=[])
        mock_review.return_value = review

        eval_result = EvalResult(
            computation_difficulty=5,
            thinking_difficulty=5,
            overall_difficulty=5,
            summary="汇总",
        )
        mock_eval.return_value = eval_result

        mock_format.return_value = ("# Report", {"title": "测试"})

        client_instance = MagicMock()
        client_instance.usage = UsageStats()
        mock_client_cls.return_value = client_instance

    @patch("src.client.openrouter.OpenRouterClient")
    @patch("src.formatter.output.format_output")
    @patch("src.eval.evaluator.comprehensive_eval")
    @patch("src.review.reviewer.detailed_review")
    @patch("src.preprocess.parser.parse_tex")
    def test_happy_path(
        self, mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, _setup_config
    ):
        tmp_path = _setup_config
        self._mock_all(mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, tmp_path)

        sm = StateMachine()
        sm.run("test.tex")
        assert sm.state == State.DONE

        # 验证文件已写入
        out_dir = tmp_path / "output"
        assert (out_dir / "test.md").exists()
        assert (out_dir / "test.json").exists()

    @patch("src.client.openrouter.OpenRouterClient")
    @patch("src.formatter.output.format_output")
    @patch("src.eval.evaluator.comprehensive_eval")
    @patch("src.review.reviewer.detailed_review")
    @patch("src.preprocess.parser.parse_tex")
    def test_md_content(
        self, mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, _setup_config
    ):
        tmp_path = _setup_config
        self._mock_all(mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, tmp_path)

        sm = StateMachine()
        sm.run("test.tex")

        md_content = (tmp_path / "output" / "test.md").read_text(encoding="utf-8")
        assert md_content == "# Report"
        assert sm.report_markdown == "# Report"

    @patch("src.client.openrouter.OpenRouterClient")
    @patch("src.formatter.output.format_output")
    @patch("src.eval.evaluator.comprehensive_eval")
    @patch("src.review.reviewer.detailed_review")
    @patch("src.preprocess.parser.parse_tex")
    def test_json_content(
        self, mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, _setup_config
    ):
        tmp_path = _setup_config
        self._mock_all(mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, tmp_path)

        sm = StateMachine()
        sm.run("test.tex")

        json_content = json.loads(
            (tmp_path / "output" / "test.json").read_text(encoding="utf-8")
        )
        assert json_content["title"] == "测试"

    @patch("src.preprocess.parser.parse_tex")
    def test_error_state_on_exception(self, mock_parse, _setup_config):
        mock_parse.side_effect = ValueError("parse failed")

        sm = StateMachine()
        with pytest.raises(ValueError, match="parse failed"):
            sm.run("bad.tex")
        assert sm.state == State.ERROR

    @patch("src.client.openrouter.OpenRouterClient")
    @patch("src.formatter.output.format_output")
    @patch("src.eval.evaluator.comprehensive_eval")
    @patch("src.review.reviewer.detailed_review")
    @patch("src.preprocess.parser.parse_tex")
    def test_calls_in_order(
        self, mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, _setup_config
    ):
        tmp_path = _setup_config
        self._mock_all(mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, tmp_path)

        sm = StateMachine()
        sm.run("test.tex")

        mock_parse.assert_called_once_with("test.tex")
        mock_review.assert_called_once()
        mock_eval.assert_called_once()
        mock_format.assert_called_once()

    @patch("src.client.openrouter.OpenRouterClient")
    @patch("src.formatter.output.format_output")
    @patch("src.eval.evaluator.comprehensive_eval")
    @patch("src.review.reviewer.detailed_review")
    @patch("src.preprocess.parser.parse_tex")
    def test_task_id_in_output_filename(
        self, mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, _setup_config
    ):
        """task_id 应出现在输出文件名中。"""
        tmp_path = _setup_config
        self._mock_all(mock_parse, mock_review, mock_eval, mock_format, mock_client_cls, tmp_path)

        sm = StateMachine()
        sm.run("test.tex", task_id="orbit_abc12345")
        assert sm.state == State.DONE

        out_dir = tmp_path / "output"
        assert (out_dir / "orbit_abc12345.md").exists()
        assert (out_dir / "orbit_abc12345.json").exists()
        # 原文件名不应存在
        assert not (out_dir / "test.md").exists()
