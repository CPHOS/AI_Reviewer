"""app/server.py 单元测试。"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config import LLMConfig, QBConfig, init_config
from src.app.server import ReviewServer, ServerStatus


@pytest.fixture
def setup_server_config(tmp_path):
    init_config(
        llm=LLMConfig(model="test-model"),
        qb=QBConfig(
            url="http://localhost:8080",
            username="bot",
            password="pass",
            poll_interval=1,
            max_concurrent_reviews=2,
        ),
        output_dir=str(tmp_path / "output"),
    )
    return tmp_path


class DummyStateMachine:
    """可阻塞的假状态机，用于占住审核槽位。"""

    gate = threading.Event()

    def __init__(self):
        self.eval_result = None
        self.report_markdown = ""

    def run(self, tex_path: str, task_id: str = "") -> None:
        self.gate.wait(timeout=2)


class TestReviewConcurrency:
    @patch("src.app.server.StateMachine", DummyStateMachine)
    def test_manual_review_rejected_when_slots_full(self, setup_server_config, monkeypatch):
        server = ReviewServer()
        server._qb = MagicMock()
        monkeypatch.setattr(server, "_download_and_extract", lambda qid, tmp_dir: Path("dummy.tex"))

        DummyStateMachine.gate.clear()
        assert server._submit_review("q1", "题1", blocking=False)
        assert server._submit_review("q2", "题2", blocking=False)
        assert not server._submit_review("q3", "题3", blocking=False)
        assert server.status == ServerStatus.REVIEWING
        assert server._active_reviews() == 2

        DummyStateMachine.gate.set()
        server._wait_for_all_reviews()
        assert server._active_reviews() == 0

    @patch("src.app.server.StateMachine", DummyStateMachine)
    def test_blocking_submit_waits_for_free_slot(self, tmp_path, monkeypatch):
        init_config(
            llm=LLMConfig(model="test-model"),
            qb=QBConfig(
                url="http://localhost:8080",
                username="bot",
                password="pass",
                poll_interval=1,
                max_concurrent_reviews=1,
            ),
            output_dir=str(tmp_path / "output"),
        )
        server = ReviewServer()
        server._qb = MagicMock()
        monkeypatch.setattr(server, "_download_and_extract", lambda qid, tmp_dir: Path("dummy.tex"))

        DummyStateMachine.gate.clear()
        assert server._submit_review("q1", "题1", blocking=False)

        result: dict[str, bool] = {}

        def submit_later():
            result["ok"] = server._submit_review("q2", "题2", blocking=True)

        thread = threading.Thread(target=submit_later)
        thread.start()
        time.sleep(0.2)
        assert "ok" not in result

        DummyStateMachine.gate.set()
        thread.join(timeout=2)
        server._wait_for_all_reviews()
        assert result["ok"] is True
        assert server._active_reviews() == 0


class TestAutoPollingFilter:
    def test_poll_new_questions_excludes_reviewed_and_inflight(self, setup_server_config):
        server = ReviewServer()
        server._qb = MagicMock()
        server._startup_time = SimpleNamespace(strftime=lambda fmt: "2026-04-11T00:00:00.000Z")
        server._qb_call = MagicMock(return_value=SimpleNamespace(items=[
            SimpleNamespace(question_id="q1", status="none", description="d1"),
            SimpleNamespace(question_id="q2", status="none", description="d2"),
            SimpleNamespace(question_id="q3", status="reviewed", description="d3"),
        ]))

        with server._task_lock:
            server._reviewed_ids.add("q1")
            server._inflight_ids.add("q2")

        new_qs = server._poll_new_questions()
        assert len(new_qs) == 0

    def test_poll_new_questions_uses_configured_auto_updated_after(self, setup_server_config):
        init_config(
            llm=LLMConfig(model="test-model"),
            qb=QBConfig(
                url="http://localhost:8080",
                username="bot",
                password="pass",
                poll_interval=1,
                max_concurrent_reviews=2,
                auto_updated_after=datetime(2026, 4, 10, 16, 0, 0, tzinfo=timezone.utc),
            ),
            output_dir=str(setup_server_config / "output"),
        )
        server = ReviewServer()
        server._qb = MagicMock()
        server._startup_time = datetime(2026, 4, 11, 0, 0, 0, tzinfo=timezone.utc)

        captured: dict[str, str] = {}

        def fake_qb_call(method_name: str, **kwargs):
            captured["updated_after"] = kwargs["updated_after"]
            return SimpleNamespace(items=[])

        server._qb_call = MagicMock(side_effect=fake_qb_call)

        server._poll_new_questions()
        assert captured["updated_after"] == "2026-04-10T16:00:00.000Z"


class TestWriteBackReviewerName:
    """审核回写后自动将 bot 显示名称添加到审题人列表。"""

    def _make_eval_result(self):
        from src.model import EvalResult
        return EvalResult(
            overall_difficulty=5,
            computation_difficulty=4,
            thinking_difficulty=6,
            summary="测试摘要",
        )

    def test_adds_bot_display_name_to_reviewers(self, setup_server_config):
        """bot 不在审题人列表时，应追加到列表尾部。"""
        server = ReviewServer()
        server._qb = MagicMock()
        server._bot_username = "bot"
        server._bot_display_name = "AI审题机器人"

        detail = SimpleNamespace(
            difficulty={},
            reviewers=["张三", "李四"],
        )

        calls: list[tuple] = []

        def fake_qb_call(method_name, *args, **kwargs):
            calls.append((method_name, args, kwargs))
            if method_name == "get_question":
                return detail
            return detail

        server._qb_call = MagicMock(side_effect=fake_qb_call)

        eval_result = self._make_eval_result()
        server._write_back("q1", eval_result, "# Report")

        reviewer_calls = [c for c in calls if c[0] == "update_question_reviewer_names"]
        assert len(reviewer_calls) == 1
        _, args, _ = reviewer_calls[0]
        assert args == ("q1", ["张三", "李四", "AI审题机器人"])

    def test_skips_if_bot_already_in_reviewers(self, setup_server_config):
        """bot 已在审题人列表时，不应再次调用更新。"""
        server = ReviewServer()
        server._qb = MagicMock()
        server._bot_username = "bot"
        server._bot_display_name = "AI审题机器人"

        detail = SimpleNamespace(
            difficulty={},
            reviewers=["张三", "AI审题机器人"],
        )

        calls: list[tuple] = []

        def fake_qb_call(method_name, *args, **kwargs):
            calls.append((method_name, args, kwargs))
            if method_name == "get_question":
                return detail
            return detail

        server._qb_call = MagicMock(side_effect=fake_qb_call)

        eval_result = self._make_eval_result()
        server._write_back("q1", eval_result, "# Report")

        reviewer_calls = [c for c in calls if c[0] == "update_question_reviewer_names"]
        assert len(reviewer_calls) == 0

    def test_preserves_existing_reviewers(self, setup_server_config):
        """更新审题人列表时不能丢失已有审题人。"""
        server = ReviewServer()
        server._qb = MagicMock()
        server._bot_username = "bot"
        server._bot_display_name = "Bot"

        detail = SimpleNamespace(
            difficulty={"bot": SimpleNamespace(score=3)},
            reviewers=["Alpha", "Beta"],
        )

        calls: list[tuple] = []

        def fake_qb_call(method_name, *args, **kwargs):
            calls.append((method_name, args, kwargs))
            if method_name == "get_question":
                return detail
            return detail

        server._qb_call = MagicMock(side_effect=fake_qb_call)

        eval_result = self._make_eval_result()
        server._write_back("q1", eval_result, "# Report")

        reviewer_calls = [c for c in calls if c[0] == "update_question_reviewer_names"]
        assert len(reviewer_calls) == 1
        _, args, _ = reviewer_calls[0]
        # 必须保留 Alpha 和 Beta，并追加 Bot
        assert args[1] == ["Alpha", "Beta", "Bot"]
