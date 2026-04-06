"""全局工作流状态机。

管理审核工作流的状态流转：
  INIT → PREPROCESSING → REVIEWING → EVALUATING → FORMATTING → DONE
                                                               ↗
  任意阶段出错 → ERROR ──────────────────────────────────────────

每个阶段转换均有日志记录；整个流程计时，最终写入 ReportMeta。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path

from src.config import get_config
from src.model import ReportMeta

logger = logging.getLogger(__name__)


class State(Enum):
    """工作流状态。"""

    INIT = auto()
    PREPROCESSING = auto()
    REVIEWING = auto()
    EVALUATING = auto()
    FORMATTING = auto()
    DONE = auto()
    ERROR = auto()


class StateMachine:
    """驱动审核工作流的状态机。"""

    def __init__(self) -> None:
        self._state: State = State.INIT
        self._start_time: float = 0.0

    @property
    def state(self) -> State:
        return self._state

    def _transition(self, to: State) -> None:
        """执行状态转移并记录日志。"""
        logger.info("状态转移: %s → %s", self._state.name, to.name)
        self._state = to

    def run(self, tex_path: str, task_id: str = "") -> None:
        """驱动完整的审核工作流。

        Args:
            tex_path: TeX 文件路径。
            task_id: 任务唯一标识，用于输出文件名。为空时使用文件名。
        """
        from src.client.openrouter import OpenRouterClient
        from src.eval.evaluator import comprehensive_eval
        from src.formatter.output import format_output
        from src.preprocess.parser import parse_tex
        from src.review.reviewer import detailed_review

        cfg = get_config()
        self._start_time = time.monotonic()
        file_stem = task_id or Path(tex_path).stem
        logger.info("[%s] 开始审核: %s", file_stem, tex_path)

        try:
            # ① 预处理
            self._transition(State.PREPROCESSING)
            logger.info("预处理 TeX 文件…")
            problem = parse_tex(tex_path)

            # ② 细致审核
            self._transition(State.REVIEWING)
            logger.info("执行细致审核…")
            client = OpenRouterClient()
            review_result = detailed_review(problem, client)

            # ③ 汇总评估
            self._transition(State.EVALUATING)
            logger.info("执行汇总评估…")
            eval_result = comprehensive_eval(problem, review_result, client)

            # ④ 格式化输出
            self._transition(State.FORMATTING)
            logger.info("生成报告…")
            elapsed = time.monotonic() - self._start_time
            meta = ReportMeta(
                model=cfg.llm.model,
                prompt_tokens=client.usage.prompt_tokens,
                completion_tokens=client.usage.completion_tokens,
                total_tokens=client.usage.total_tokens,
                timestamp=datetime.now(timezone.utc).isoformat(),
                elapsed_seconds=round(elapsed, 2),
            )
            md, js = format_output(problem, review_result, eval_result, meta)

            # 写入文件
            out_dir = Path(cfg.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            md_path = out_dir / f"{file_stem}.md"
            json_path = out_dir / f"{file_stem}.json"
            md_path.write_text(md, encoding="utf-8")
            json_path.write_text(
                json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info("报告已写入: %s, %s", md_path, json_path)

            self._transition(State.DONE)
            elapsed = time.monotonic() - self._start_time
            logger.info("审核完成，总耗时 %.2f 秒", elapsed)

        except Exception:
            self._transition(State.ERROR)
            elapsed = time.monotonic() - self._start_time
            logger.exception("审核流程出错 (已耗时 %.2f 秒)", elapsed)
            raise
