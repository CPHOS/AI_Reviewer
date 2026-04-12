"""题库服务器模式。

连接远程题库服务器，支持：
  - 手动搜索题目并指定审题
  - 自动模式：定时轮询新增题目并自动审题
"""

from __future__ import annotations

import json
import logging
import shutil
import signal
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

from cphos_qdb import QBClient, QBError

from src.config import get_config
from src.state import StateMachine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 服务状态
# ---------------------------------------------------------------------------


class ServerStatus(Enum):
    """ReviewServer 运行状态。"""

    STOPPED = auto()
    CONNECTING = auto()
    IDLE = auto()
    REVIEWING = auto()
    AUTO_POLLING = auto()
    ERROR = auto()


# ---------------------------------------------------------------------------
# ReviewServer
# ---------------------------------------------------------------------------


class ReviewServer:
    """题库审核服务器。"""

    def __init__(self) -> None:
        self._status = ServerStatus.STOPPED
        self._qb: QBClient | None = None
        self._bot_username: str = ""
        self._bot_display_name: str = ""
        self._auto_mode = False
        self._auto_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._startup_time: datetime | None = None
        # 已审核的 question_id 集合（运行期内缓存，避免重复审核）
        self._reviewed_ids: set[str] = set()
        # 已提交或正在运行的 question_id，避免重复提交。
        self._inflight_ids: set[str] = set()
        # server 模式允许的最大并发审核任务数。
        max_reviews = max(1, get_config().qb.max_concurrent_reviews)
        self._review_slots = threading.Semaphore(max_reviews)
        self._max_concurrent_reviews = max_reviews
        self._active_review_count = 0
        self._review_threads: set[threading.Thread] = set()
        self._task_lock = threading.Lock()
        self._qb_lock = threading.Lock()

    @property
    def status(self) -> ServerStatus:
        return self._status

    def _qb_call(self, method_name: str, *args, **kwargs):
        """串行化访问共享 QBClient，避免多线程并发调用 SDK。"""
        with self._qb_lock:
            assert self._qb is not None
            method = getattr(self._qb, method_name)
            return method(*args, **kwargs)

    def _active_reviews(self) -> int:
        with self._task_lock:
            return self._active_review_count

    def _wait_for_review_slot(self, blocking: bool) -> bool:
        """获取审核槽位；blocking=True 时可被 stop_event 打断。"""
        if not blocking:
            return self._review_slots.acquire(blocking=False)

        while not self._stop_event.is_set():
            if self._review_slots.acquire(timeout=0.5):
                return True
        return False

    def _cleanup_finished_threads(self) -> None:
        """清理已结束的审核线程引用。"""
        with self._task_lock:
            self._review_threads = {t for t in self._review_threads if t.is_alive()}

    def _submit_review(self, question_id: str, description: str, *, blocking: bool) -> bool:
        """提交审核任务，受最大并发数限制。"""
        self._cleanup_finished_threads()

        with self._task_lock:
            if question_id in self._inflight_ids:
                logger.info("题目已在审核队列中，跳过重复提交: %s (%s)", description, question_id)
                print("该题目已在审核队列中")
                return False

        acquired = self._wait_for_review_slot(blocking)
        if not acquired:
            logger.warning("审核队列已满，跳过: %s", description)
            print(f"当前审核任务已达上限 ({self._max_concurrent_reviews})，请稍后再试")
            return False

        with self._task_lock:
            if question_id in self._inflight_ids:
                self._review_slots.release()
                logger.info("题目已在审核队列中，跳过重复提交: %s (%s)", description, question_id)
                print("该题目已在审核队列中")
                return False
            self._inflight_ids.add(question_id)
            self._active_review_count += 1
            self._status = ServerStatus.REVIEWING

        thread = threading.Thread(
            target=self._review_question_task,
            args=(question_id, description),
            daemon=True,
            name=f"review-{question_id[:8]}",
        )
        with self._task_lock:
            self._review_threads.add(thread)
        thread.start()
        logger.info(
            "已提交审核任务: %s (%s)，当前并发 %d/%d",
            description,
            question_id,
            self._active_reviews(),
            self._max_concurrent_reviews,
        )
        return True

    def _finish_review_task(self, question_id: str, succeeded: bool) -> None:
        """清理审核任务状态并释放槽位。"""
        with self._task_lock:
            self._inflight_ids.discard(question_id)
            if succeeded:
                self._reviewed_ids.add(question_id)
            self._active_review_count = max(0, self._active_review_count - 1)
            self._review_threads.discard(threading.current_thread())
            if self._active_review_count == 0 and self._status == ServerStatus.REVIEWING:
                self._status = ServerStatus.IDLE
        self._review_slots.release()

    def _wait_for_all_reviews(self) -> None:
        """等待所有已提交审核任务完成。"""
        while True:
            with self._task_lock:
                threads = list(self._review_threads)
            if not threads:
                return
            for thread in threads:
                thread.join(timeout=0.2)
            self._cleanup_finished_threads()

    # ------------------------------------------------------------------
    # 连接 / 断开
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """登录题库服务器。"""
        cfg = get_config().qb
        if not cfg.url or not cfg.username or not cfg.password:
            raise ValueError("题库服务器配置不完整，请检查 .env 中的 QB_URL / QB_USERNAME / QB_PASSWORD")

        self._status = ServerStatus.CONNECTING
        logger.info("正在连接题库服务器: %s", cfg.url)

        self._qb = QBClient(cfg.url)
        self._qb_call("login", cfg.username, cfg.password)

        # 获取 bot 用户信息
        profile = self._qb_call("me")
        self._bot_username = profile.username
        self._bot_display_name = profile.display_name
        self._startup_time = datetime.now(timezone.utc)

        logger.info("已登录: %s (%s), 角色: %s",
                     profile.display_name, profile.username, profile.role)
        self._status = ServerStatus.IDLE

    def _disconnect(self) -> None:
        """断开题库连接。"""
        with self._qb_lock:
            if self._qb is not None:
                try:
                    self._qb.logout()
                except QBError:
                    pass
                try:
                    self._qb.close()
                except Exception:
                    pass
                self._qb = None
        self._status = ServerStatus.STOPPED

    # ------------------------------------------------------------------
    # 题目下载 & 审核
    # ------------------------------------------------------------------

    def _download_and_extract(self, question_id: str, tmp_dir: Path) -> Path:
        """下载题目 bundle 并解压，返回 tex 文件路径。"""
        assert self._qb is not None
        bundle_path = self._qb_call(
            "download_question_bundle",
            [question_id], save_to=str(tmp_dir / "bundle.zip"),
        )

        extract_dir = tmp_dir / "extracted"
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(extract_dir)

        # 解析 manifest.json
        manifest_path = extract_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        for q_entry in manifest["questions"]:
            if q_entry["question_id"] == question_id:
                directory = q_entry["directory"]
                tex_filename = q_entry["metadata"]["source"]["tex"]
                tex_path = extract_dir / directory / tex_filename
                if tex_path.exists():
                    return tex_path
                raise FileNotFoundError(f"tex 文件不存在: {tex_path}")

        raise ValueError(f"manifest 中未找到题目: {question_id}")

    def _review_question_task(self, question_id: str, description: str) -> None:
        """审核单个题目的线程任务。"""
        logger.info("开始审核题目: %s (%s)", description, question_id)

        tmp_dir = Path(tempfile.mkdtemp(prefix="ai_reviewer_"))
        succeeded = False
        try:
            tex_path = self._download_and_extract(question_id, tmp_dir)

            # 使用 task_id 标识
            short_id = uuid.uuid4().hex[:8]
            task_id = f"{tex_path.stem}_{short_id}"

            # 运行状态机审核
            sm = StateMachine()
            sm.run(str(tex_path), task_id=task_id)

            # 读取 eval 结果用于回写
            eval_result = sm.eval_result
            if eval_result is not None:
                self._write_back(question_id, eval_result, sm.report_markdown)

            logger.info("题目审核完成: %s", description)
            succeeded = True

        except Exception:
            logger.exception("题目审核失败: %s (%s)", description, question_id)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._finish_review_task(question_id, succeeded)

    def _write_back(self, question_id: str, eval_result, markdown_notes: str = "") -> None:
        """将审核结果回写到题库。"""
        assert self._qb is not None
        from src.model import EvalResult

        if not isinstance(eval_result, EvalResult):
            return

        difficulty_tag = self._bot_username
        notes = markdown_notes if markdown_notes else eval_result.summary
        exists = False

        try:
            # v0.1.0 SDK: create 与 update 语义分离，按是否已存在做 upsert。
            detail = self._qb_call("get_question", question_id)
            exists = difficulty_tag in detail.difficulty

            if exists:
                self._qb_call(
                    "update_question_difficulty",
                    question_id,
                    difficulty_tag,
                    eval_result.overall_difficulty,
                    notes=notes,
                )
            else:
                self._qb_call(
                    "create_question_difficulty",
                    question_id,
                    difficulty_tag,
                    eval_result.overall_difficulty,
                    notes=notes,
                )

            # 并发情况下存在竞态：create 可能因已存在失败，update 可能因不存在失败。
            # 若出现 404/409，则反向再试一次。
        except QBError as e:
            if e.status_code in (404, 409):
                try:
                    if exists:
                        self._qb_call(
                            "create_question_difficulty",
                            question_id,
                            difficulty_tag,
                            eval_result.overall_difficulty,
                            notes=notes,
                        )
                    else:
                        self._qb_call(
                            "update_question_difficulty",
                            question_id,
                            difficulty_tag,
                            eval_result.overall_difficulty,
                            notes=notes,
                        )
                except QBError as e2:
                    logger.error("回写审核结果失败: %s", e2.message)
                    return
            else:
                logger.error("回写审核结果失败: %s", e.message)
                return

        try:
            self._qb_call("update_question_status", question_id, "reviewed")
            logger.info("已回写审核结果: difficulty[%s]=%d, notes_len=%d",
                        difficulty_tag, eval_result.overall_difficulty,
                        len(notes))
        except QBError as e:
            logger.error("回写审核结果失败: %s", e.message)

        # 将 bot 显示名称添加到审题人列表（保留已有审题人）
        if self._bot_display_name:
            try:
                current_reviewers = list(detail.reviewers)
                if self._bot_display_name not in current_reviewers:
                    current_reviewers.append(self._bot_display_name)
                    self._qb_call(
                        "update_question_reviewer_names",
                        question_id,
                        current_reviewers,
                    )
                    logger.info("已将 %s 添加到审题人列表", self._bot_display_name)
            except QBError as e:
                logger.error("添加审题人失败: %s", e.message)

    # ------------------------------------------------------------------
    # 搜索题目
    # ------------------------------------------------------------------

    def _search_questions(self, keyword: str) -> list[dict]:
        """搜索题目，返回简要信息列表。"""
        assert self._qb is not None
        result = self._qb_call("list_questions", q=keyword, limit=20)
        items = []
        for q in result.items:
            items.append({
                "question_id": q.question_id,
                "description": q.description,
                "category": q.category,
                "status": q.status,
                "score": q.score,
                "author": q.author,
                "created_at": q.created_at.isoformat(),
            })
        logger.info("搜索 '%s': 找到 %d 条 (共 %d 条)",
                     keyword, len(items), result.total)
        return items

    # ------------------------------------------------------------------
    # 自动模式
    # ------------------------------------------------------------------

    def _poll_new_questions(self) -> list:
        """查询需要审核的新题目（status=none 且 updated_at 超过设定时间线）。"""
        assert self._qb is not None
        assert self._startup_time is not None

        auto_updated_after = get_config().qb.auto_updated_after
        baseline = auto_updated_after or self._startup_time
        updated_after = baseline.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with self._task_lock:
            inflight_ids = set(self._inflight_ids)
            reviewed_ids = set(self._reviewed_ids)
        result = self._qb_call(
            "list_questions",
            updated_after=updated_after,
            limit=100,
        )
        new_questions = [
            q for q in result.items
            if q.status == "none"
            and q.question_id not in reviewed_ids
            and q.question_id not in inflight_ids
        ]
        return new_questions

    def _auto_poll_loop(self) -> None:
        """自动轮询线程主循环。"""
        cfg = get_config().qb
        interval = cfg.poll_interval
        logger.info("自动模式已启动，轮询间隔: %d 秒", interval)

        while not self._stop_event.is_set():
            try:
                self._status = ServerStatus.AUTO_POLLING
                new_qs = self._poll_new_questions()
                if new_qs:
                    logger.info("发现 %d 道新题目", len(new_qs))
                    for q in new_qs:
                        if self._stop_event.is_set():
                            break
                        self._submit_review(q.question_id, q.description, blocking=True)
                else:
                    logger.debug("未发现新题目")
                self._status = (
                    ServerStatus.REVIEWING if self._active_reviews() > 0
                    else ServerStatus.IDLE
                )
            except QBError as e:
                logger.error("自动轮询出错: %s", e.message)
                self._status = ServerStatus.IDLE
            except Exception:
                logger.exception("自动轮询出错")
                self._status = ServerStatus.IDLE

            # 等待间隔或被停止
            self._stop_event.wait(interval)

        logger.info("自动模式已停止")

    def _start_auto(self) -> None:
        """启动自动轮询。"""
        if self._auto_mode:
            print("自动模式已在运行中")
            return
        self._auto_mode = True
        self._stop_event.clear()
        self._auto_thread = threading.Thread(
            target=self._auto_poll_loop, daemon=True,
        )
        self._auto_thread.start()
        print("自动模式已开启")

    def _stop_auto(self) -> None:
        """停止自动轮询。"""
        if not self._auto_mode:
            print("自动模式未在运行")
            return
        self._auto_mode = False
        self._stop_event.set()
        if self._auto_thread is not None:
            self._auto_thread.join(timeout=5)
            self._auto_thread = None
        self._status = ServerStatus.IDLE
        print("自动模式已关闭")

    # ------------------------------------------------------------------
    # 交互式命令循环
    # ------------------------------------------------------------------

    def _print_help(self) -> None:
        print(
            "\n可用命令:\n"
            "  search <关键词>       搜索题目\n"
            "  review <序号|UUID>    审核指定题目（序号为最近搜索结果中的编号）\n"
            "  auto on               开启自动轮询模式\n"
            "  auto off              关闭自动轮询模式\n"
            "  启动参数 --auto-updated-after 可覆盖 auto 的检查起点\n"
            "  status                查看当前服务状态\n"
            "  help                  显示此帮助\n"
            "  quit / exit           退出服务\n"
        )

    def _cmd_loop(self) -> None:
        """交互式命令循环。"""
        last_search: list[dict] = []
        self._print_help()

        while True:
            try:
                line = input("\n[ai-reviewer] > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("quit", "exit"):
                break

            elif cmd == "help":
                self._print_help()

            elif cmd == "status":
                auto_updated_after = get_config().qb.auto_updated_after
                baseline = auto_updated_after or self._startup_time
                print(f"服务状态: {self._status.name}")
                print(f"自动模式: {'开启' if self._auto_mode else '关闭'}")
                print(f"审核并发: {self._active_reviews()}/{self._max_concurrent_reviews}")
                if baseline is not None:
                    print(f"auto 检查起点: {baseline.isoformat()}")
                print(f"已审核题目数: {len(self._reviewed_ids)}")

            elif cmd == "search":
                if not arg:
                    print("用法: search <关键词>")
                    continue
                try:
                    last_search = self._search_questions(arg)
                    if not last_search:
                        print("未找到匹配的题目")
                    else:
                        for i, q in enumerate(last_search, 1):
                            print(f"  {i:>3}. [{q['category']}] {q['description']}"
                                  f"  (分值:{q['score']}, 状态:{q['status']},"
                                  f" 作者:{q['author']})")
                            print(f"       ID: {q['question_id']}")
                except QBError as e:
                    print(f"搜索失败: {e.message}")

            elif cmd == "review":
                if not arg:
                    print("用法: review <序号|UUID>")
                    continue
                # 尝试按序号解析
                try:
                    idx = int(arg) - 1
                    if 0 <= idx < len(last_search):
                        q = last_search[idx]
                        self._submit_review(q["question_id"], q["description"], blocking=False)
                    else:
                        print(f"序号超出范围 (1-{len(last_search)})")
                    continue
                except ValueError:
                    pass
                # 按 UUID 处理
                try:
                    detail = self._qb_call("get_question", arg)
                    self._submit_review(detail.question_id, detail.description, blocking=False)
                except QBError as e:
                    print(f"获取题目失败: {e.message}")

            elif cmd == "auto":
                if arg == "on":
                    self._start_auto()
                elif arg == "off":
                    self._stop_auto()
                else:
                    print("用法: auto on|off")

            else:
                print(f"未知命令: {cmd}，输入 help 查看帮助")

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, *, auto_on: bool = False) -> None:
        """启动服务器。

        Args:
            auto_on: 为 True 时连接成功后立即开启自动轮询。
        """
        def _handle_signal(signum, frame):
            logger.info("收到信号 %s，正在停止服务…", signum)
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        try:
            self._connect()
            if auto_on:
                self._start_auto()
            if sys.stdin.isatty():
                self._cmd_loop()
            else:
                # 非交互模式（如 Docker）：阻塞等待停止信号
                logger.info("非交互模式，按 Ctrl+C 或发送 SIGTERM 停止服务")
                self._stop_event.wait()
        except KeyboardInterrupt:
            print("\n收到中断信号")
        except QBError as e:
            logger.error("题库服务器错误: %s", e.message)
            self._status = ServerStatus.ERROR
        except Exception:
            logger.exception("服务器异常")
            self._status = ServerStatus.ERROR
        finally:
            if self._auto_mode:
                self._stop_auto()
            self._wait_for_all_reviews()
            self._disconnect()
            logger.info("服务已停止")
