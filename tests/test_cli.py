"""app/cli.py 单元测试。"""

from __future__ import annotations

from datetime import timezone

from src.app.cli import build_parser, _generate_task_id


class TestBuildParser:
    def test_single_file(self):
        parser = build_parser()
        args = parser.parse_args(["local", "a.tex"])
        assert args.mode == "local"
        assert args.tex_files == ["a.tex"]
        assert args.jobs == 1

    def test_multiple_files(self):
        parser = build_parser()
        args = parser.parse_args(["local", "a.tex", "b.tex", "c.tex"])
        assert args.tex_files == ["a.tex", "b.tex", "c.tex"]

    def test_jobs_flag(self):
        parser = build_parser()
        args = parser.parse_args(["local", "-j", "4", "a.tex", "b.tex"])
        assert args.jobs == 4
        assert args.tex_files == ["a.tex", "b.tex"]

    def test_output_dir(self):
        parser = build_parser()
        args = parser.parse_args(["local", "-o", "my_output", "a.tex"])
        assert args.output_dir == "my_output"

    def test_server_mode(self):
        parser = build_parser()
        args = parser.parse_args(["server"])
        assert args.mode == "server"

    def test_server_output_dir(self):
        parser = build_parser()
        args = parser.parse_args(["server", "-o", "reports"])
        assert args.mode == "server"
        assert args.output_dir == "reports"

    def test_server_auto_updated_after(self):
        parser = build_parser()
        args = parser.parse_args([
            "server",
            "--auto-updated-after",
            "2026-04-11T08:00:00+08:00",
        ])
        assert args.mode == "server"
        assert args.auto_updated_after.isoformat() == "2026-04-11T00:00:00+00:00"

    def test_server_auto_updated_after_z_suffix(self):
        parser = build_parser()
        args = parser.parse_args([
            "server",
            "--auto-updated-after",
            "2026-04-11T00:00:00Z",
        ])
        assert args.auto_updated_after.tzinfo == timezone.utc


class TestGenerateTaskId:
    def test_contains_stem(self):
        tid = _generate_task_id("path/to/orbit.tex")
        assert tid.startswith("orbit_")

    def test_unique(self):
        tid1 = _generate_task_id("a.tex")
        tid2 = _generate_task_id("a.tex")
        assert tid1 != tid2

    def test_length(self):
        tid = _generate_task_id("test.tex")
        # stem(test) + _ + 8 hex = 13
        assert len(tid) == len("test_") + 8
