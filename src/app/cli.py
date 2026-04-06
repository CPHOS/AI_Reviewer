"""CLI 入口模块。

解析命令行参数，驱动审核工作流状态机。
终端仅输出日志，审核结果写入报告文件。
支持多文件并发审核。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from src.config import LLMConfig, init_config
from src.state import StateMachine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-reviewer",
        description="CPHOS 题目 AI 审核工具",
    )
    parser.add_argument(
        "tex_files", nargs="+",
        help="待审核的 .tex 文件路径（支持多个）",
    )
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="报告输出目录（覆盖 .env 中的 OUTPUT_DIR）",
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=1,
        help="并发审核数目（默认 1，即串行）",
    )
    return parser


def _generate_task_id(tex_path: str) -> str:
    """为每个审核任务生成唯一 task_id：文件名 + 短 UUID。"""
    stem = Path(tex_path).stem
    short_id = uuid.uuid4().hex[:8]
    return f"{stem}_{short_id}"


def _run_single(tex_path: str, task_id: str) -> tuple[str, str | None]:
    """执行单个审核任务，返回 (task_id, 错误信息 | None)。"""
    logger = logging.getLogger(__name__)
    logger.info("[%s] 开始审核: %s", task_id, tex_path)
    sm = StateMachine()
    try:
        sm.run(tex_path, task_id=task_id)
        return task_id, None
    except Exception as exc:
        logger.error("[%s] 审核失败: %s", task_id, exc)
        return task_id, str(exc)
    return parser


def setup_logging() -> None:
    """配置全局日志格式，输出到 stderr。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> None:
    """CLI 主入口。"""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv or sys.argv[1:])

    # 从 .env 加载配置并调用 init_config
    load_dotenv()

    api_keys_raw = os.getenv("OPENROUTER_API_KEY", "")
    api_keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()]

    llm = LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "openrouter"),
        model=os.getenv("LLM_MODEL", ""),
        api_keys=api_keys,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
        retry_interval=float(os.getenv("LLM_RETRY_INTERVAL", "2.0")),
    )

    output_dir = args.output_dir or os.getenv("OUTPUT_DIR", "output")
    config = init_config(llm=llm, output_dir=output_dir)

    logger = logging.getLogger(__name__)
    logger.info("模型: %s | API Keys: %d 个 | 输出目录: %s",
                config.llm.model, len(config.llm.api_keys), config.output_dir)

    # 去重并为每个文件分配 task_id
    tex_files = list(dict.fromkeys(args.tex_files))  # 保序去重
    tasks = [(f, _generate_task_id(f)) for f in tex_files]
    jobs = min(args.jobs, len(tasks))

    logger.info("待审核文件: %d 个 | 并发数: %d", len(tasks), jobs)
    for tex_path, task_id in tasks:
        logger.info("  [%s] %s", task_id, tex_path)

    if jobs <= 1:
        # 串行执行
        failed: list[tuple[str, str]] = []
        for tex_path, task_id in tasks:
            tid, err = _run_single(tex_path, task_id)
            if err:
                failed.append((tid, err))
    else:
        # 并发执行
        failed = []
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(_run_single, tex_path, task_id): task_id
                for tex_path, task_id in tasks
            }
            for future in as_completed(futures):
                tid, err = future.result()
                if err:
                    failed.append((tid, err))

    # 汇总结果
    if failed:
        logger.error("以下任务失败:")
        for tid, err in failed:
            logger.error("  [%s] %s", tid, err)
        sys.exit(1)
    else:
        logger.info("全部 %d 个审核任务完成", len(tasks))
