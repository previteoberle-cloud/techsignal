"""Offline replay and the optional live connection, for the workbench. Both run the REAL pipeline
(techsignal.cli) in-process and write into a fresh temporary directory.

Offline replay: the API responses captured during the live run are re-run with their run records
(provenance check by sha256, so the brief says "离线回放"). Multi-batch cases are replayed in order
with --track-history. Nothing under the case directories is written.

Concurrency: the API key and the log stream are passed as arguments. No process-wide state (the
process environment, sys.stdout/sys.stderr) is modified, so several Streamlit sessions served by one
process cannot see or overwrite each other's key or log.
"""
from __future__ import annotations

import io
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..cli import main as cli_main
from .viewmodel import CaseRef, LoadError


@dataclass
class ReplayResult:
    workdir: Path
    run_dirs: list[Path]
    log: str                # what the CLI printed, for the "运行日志" expander
    verify: bool


def _last_line(buf: io.StringIO, default: str) -> str:
    lines = buf.getvalue().strip().splitlines()
    return lines[-1] if lines else default


def replay_case(case: CaseRef, verify: bool = False, workdir: Path | None = None) -> ReplayResult:
    """Replay every batch of `case`. With verify=True the evidence-gathering tools also run (they need
    network access to GitHub; without it they are recorded as failed and nothing else changes)."""
    workdir = Path(workdir or tempfile.mkdtemp(prefix="techsignal-replay-"))
    state = workdir / "state"
    buf = io.StringIO()
    run_dirs = []
    for b in case.batches:
        src = b.run_dir / "source_changes_report.json"
        rec = b.run_dir / "run_record.json"
        if not src.exists() or not rec.exists():
            raise LoadError(f"{b.label}：缺少已保存的在线数据（source_changes_report.json / run_record.json），无法回放")
        args = ["run", "--changes", str(src), "--input-record", str(rec), "--theses", str(case.theses_path),
                "--sources", str(case.sources_path), "--out", str(state)]
        if case.is_multi_batch:
            args.append("--track-history")
        if verify and case.verify_policy_path:
            args += ["--verify", "--verify-policy", str(case.verify_policy_path)]
        if cli_main(args, stream=buf) != 0:
            raise LoadError(f"{b.label} 回放失败：{_last_line(buf, '未知错误')}")
        snap = workdir / f"batch{b.number}"
        shutil.copytree(state, snap)
        run_dirs.append(snap)
    return ReplayResult(workdir, run_dirs, buf.getvalue(), verify)


def fetch_live(case: CaseRef, base_url: str, api_key: str, from_ts: int, workdir: Path | None = None) -> ReplayResult:
    """「在线连接」: pull changes from the user's own changedetection.io via the existing client and run
    the pipeline (techsignal.cli, live mode) into a temporary directory. The key is passed as an
    argument only and is never written to any output file."""
    from ..cdio_client import CdioError, ChangeDetectionClient
    if not base_url.startswith(("http://", "https://")):
        raise LoadError("地址必须以 http:// 或 https:// 开头")
    try:
        ChangeDetectionClient(base_url, api_key=api_key or None, timeout=5, retries=0).system_info()
    except CdioError as e:
        raise LoadError(f"无法连接 changedetection.io：{e}") from None
    workdir = Path(workdir or tempfile.mkdtemp(prefix="techsignal-live-"))
    out = workdir / "live"
    buf = io.StringIO()
    rc = cli_main(["run", "--cdio-url", base_url, "--from", str(int(from_ts)), "--theses", str(case.theses_path),
                   "--sources", str(case.sources_path), "--out", str(out)], api_key=api_key or "", stream=buf)
    if rc != 0:
        raise LoadError(_last_line(buf, "在线拉取失败"))
    return ReplayResult(workdir, [out], buf.getvalue(), False)
