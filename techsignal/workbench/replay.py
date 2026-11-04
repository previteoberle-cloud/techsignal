"""Offline replay from the workbench: re-run the REAL pipeline (techsignal.cli) on the API responses
that were captured during the live run, into a fresh temporary directory.

Nothing under techsignal/cases is written. Each batch's captured source_changes_report.json is
replayed together with its run_record.json, so the provenance check (sha256) runs as usual and the
resulting brief says "离线回放". Multi-batch cases are replayed in order with --track-history.
"""
from __future__ import annotations

import contextlib
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
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = cli_main(args)
        if rc != 0:
            raise LoadError(f"{b.label} 回放失败：{buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else '未知错误'}")
        snap = workdir / f"batch{b.number}"
        shutil.copytree(state, snap)
        run_dirs.append(snap)
    return ReplayResult(workdir, run_dirs, buf.getvalue(), verify)


def fetch_live(case: CaseRef, base_url: str, api_key: str, from_ts: int, workdir: Path | None = None) -> ReplayResult:
    """「在线连接」: pull changes from the user's own changedetection.io via the existing client and run
    the pipeline (techsignal.cli, live mode) into a temporary directory. The API key is passed via
    the environment of this call only and is never written anywhere."""
    import os

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
    old = os.environ.get("TECHSIGNAL_CDIO_API_KEY")
    os.environ["TECHSIGNAL_CDIO_API_KEY"] = api_key or ""
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = cli_main(["run", "--cdio-url", base_url, "--from", str(int(from_ts)), "--theses", str(case.theses_path),
                           "--sources", str(case.sources_path), "--out", str(out)])
    finally:
        if old is None:
            os.environ.pop("TECHSIGNAL_CDIO_API_KEY", None)
        else:
            os.environ["TECHSIGNAL_CDIO_API_KEY"] = old
    if rc != 0:
        lines = buf.getvalue().strip().splitlines()
        raise LoadError(lines[-1] if lines else "在线拉取失败")
    return ReplayResult(workdir, [out], buf.getvalue(), False)
