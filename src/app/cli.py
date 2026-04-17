"""CLI 入口模块。

支持两种运行模式：
  local  — 读取本地 .tex 文件并生成审核报告（原有功能）。
  server — 连接远程题库服务器，支持手动搜索审题和自动轮询新题。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.config import LLMConfig, QBConfig, init_config
from src.state import StateMachine


# ---------------------------------------------------------------------------
# CLI 参数解析
# ---------------------------------------------------------------------------


def _parse_datetime_arg(value: str) -> datetime:
    """解析 server 时间参数，统一转换为 UTC。"""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "时间格式无效，请使用 ISO 8601，例如 2026-04-11T08:00:00+08:00"
        ) from exc

    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        dt = dt.replace(tzinfo=local_tz)

    return dt.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-reviewer",
        description="CPHOS 题目 AI 审核工具",
    )
    sub = parser.add_subparsers(dest="mode", help="运行模式")

    # --- local 子命令 ---
    local_p = sub.add_parser("local", help="本地文件审核模式")
    local_p.add_argument(
        "tex_files", nargs="+",
        help="待审核的 .tex 文件路径（支持多个）",
    )
    local_p.add_argument(
        "-o", "--output-dir", default=None,
        help="报告输出目录（覆盖 .env 中的 OUTPUT_DIR）",
    )
    local_p.add_argument(
        "-j", "--jobs", type=int, default=1,
        help="并发审核数目（默认 1，即串行）",
    )

    # --- server 子命令 ---
    server_p = sub.add_parser("server", help="题库服务器模式")
    server_p.add_argument(
        "-o", "--output-dir", default=None,
        help="报告输出目录（覆盖 .env 中的 OUTPUT_DIR）",
    )
    server_p.add_argument(
        "--auto-on", action="store_true", default=False,
        help="启动后立即开启自动轮询模式",
    )
    server_p.add_argument(
        "--auto-updated-after", type=_parse_datetime_arg, default=None,
        help="auto 模式只检查该时间之后更新的题目（ISO 8601，默认使用服务启动时间）",
    )

    return parser


# ---------------------------------------------------------------------------
# local 模式辅助函数
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 通用设置
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """配置全局日志格式，输出到 stderr。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


def _load_llm_config() -> LLMConfig:
    """从环境变量构建 LLMConfig。"""
    api_keys_raw = os.getenv("OPENROUTER_API_KEY", "")
    api_keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()]
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "openrouter"),
        model=os.getenv("LLM_MODEL", ""),
        api_keys=api_keys,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4096")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
        retry_interval=float(os.getenv("LLM_RETRY_INTERVAL", "2.0")),
        batch_min_points=int(os.getenv("REVIEW_BATCH_MIN_POINTS", "0")),
    )


def _load_qb_config() -> QBConfig:
    """从环境变量构建 QBConfig。"""
    return QBConfig(
        url=os.getenv("QB_URL", ""),
        access_token=os.getenv("QB_ACCESS_TOKEN", ""),
        poll_interval=int(os.getenv("QB_POLL_INTERVAL", "600")),
        max_concurrent_reviews=max(
            1, int(os.getenv("QB_MAX_CONCURRENT_REVIEWS", "1"))
        ),
        auto_updated_after=None,
    )


# ---------------------------------------------------------------------------
# local 模式入口
# ---------------------------------------------------------------------------


def _main_local(args: argparse.Namespace) -> None:
    """local 子命令入口。"""
    logger = logging.getLogger(__name__)
    llm = _load_llm_config()
    output_dir = args.output_dir or os.getenv("OUTPUT_DIR", "output")
    config = init_config(llm=llm, output_dir=output_dir)

    logger.info("模型: %s | API Keys: %d 个 | 输出目录: %s",
                config.llm.model, len(config.llm.api_keys), config.output_dir)

    tex_files = list(dict.fromkeys(args.tex_files))
    tasks = [(f, _generate_task_id(f)) for f in tex_files]
    jobs = min(args.jobs, len(tasks))

    logger.info("待审核文件: %d 个 | 并发数: %d", len(tasks), jobs)
    for tex_path, task_id in tasks:
        logger.info("  [%s] %s", task_id, tex_path)

    if jobs <= 1:
        failed: list[tuple[str, str]] = []
        for tex_path, task_id in tasks:
            tid, err = _run_single(tex_path, task_id)
            if err:
                failed.append((tid, err))
    else:
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

    if failed:
        logger.error("以下任务失败:")
        for tid, err in failed:
            logger.error("  [%s] %s", tid, err)
        sys.exit(1)
    else:
        logger.info("全部 %d 个审核任务完成", len(tasks))


# ---------------------------------------------------------------------------
# server 模式入口
# ---------------------------------------------------------------------------


def _main_server(args: argparse.Namespace) -> None:
    """server 子命令入口。"""
    from src.app.server import ReviewServer

    llm = _load_llm_config()
    qb = _load_qb_config()
    qb.auto_updated_after = args.auto_updated_after
    output_dir = args.output_dir or os.getenv("OUTPUT_DIR", "output")
    config = init_config(llm=llm, qb=qb, output_dir=output_dir)

    logger = logging.getLogger(__name__)
    logger.info("模型: %s | 题库: %s | 输出目录: %s",
                config.llm.model, config.qb.url, config.output_dir)

    server = ReviewServer()
    server.run(auto_on=args.auto_on)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI 主入口。"""
    setup_logging()
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args(argv or sys.argv[1:])

    if args.mode == "local":
        _main_local(args)
    elif args.mode == "server":
        _main_server(args)
    else:
        parser.print_help()
        sys.exit(1)
