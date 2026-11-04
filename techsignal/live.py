"""Run TechSignal against a REAL changedetection.io instance started from this checkout.

    python -m techsignal.live --python /path/to/python-with-cdio-deps \\
        --case cases/tenstorrent_blackhole/case.json --out cases/tenstorrent_blackhole/output

What is real here:
  - changedetection.io itself (changedetection.py from this repository, temporary datastore),
  - its fetch of every URL in the case (through whatever network the machine has),
  - its snapshot history and diff, read back through the REST API by ChangeDetectionClient.

How versions are observed: changedetection.io snapshots a page when it checks it. A page's past
cannot be re-fetched from its live URL, so for historical versions the case lists immutable,
commit-pinned URLs (e.g. raw.githubusercontent.com/<org>/<repo>/<sha>/README.md). The watch is
pointed at version 1, checked, re-pointed at version 2 (PUT /api/v1/watch/{uuid}), checked again.
Each snapshot therefore holds exactly what that published version contained. Snapshot timestamps are
the time of fetching, not of publication; publication dates come from the case file and are kept
in observation_record.json.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .cdio_client import CdioError, ChangeDetectionClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TECHSIGNAL_ROOT = REPO_ROOT / "techsignal"


class LiveCdioError(RuntimeError):
    pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LiveCdio:
    """Context manager: a changedetection.io process on 127.0.0.1 with a throwaway datastore."""

    def __init__(self, python: str, allow_private_addresses: bool = False, startup_timeout: float = 120,
                 log_path: Path | None = None, keep_default_watches: bool = False):
        self.python = python
        self.allow_private = allow_private_addresses
        self.startup_timeout = startup_timeout
        self.log_path = log_path
        self.keep_default_watches = keep_default_watches
        self.proc: subprocess.Popen | None = None
        self.datastore: Path | None = None
        self.client: ChangeDetectionClient | None = None
        self.url = ""

    def __enter__(self) -> LiveCdio:
        entry = REPO_ROOT / "changedetection.py"
        if not entry.exists():
            raise LiveCdioError(f"找不到 {entry}：需要在 changedetection.io 仓库内运行")
        self.datastore = Path(tempfile.mkdtemp(prefix="techsignal-cdio-"))
        port = _free_port()
        self.url = f"http://127.0.0.1:{port}"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        if self.allow_private:
            # changedetection.io's own, documented operator opt-in for fetching local addresses
            env["ALLOW_IANA_RESTRICTED_ADDRESSES"] = "true"
        log = open(self.log_path, "w") if self.log_path else subprocess.DEVNULL  # noqa: SIM115
        self.proc = subprocess.Popen(
            [self.python, str(entry), "-d", str(self.datastore), "-p", str(port), "-h", "127.0.0.1", "-l", "INFO"],
            cwd=REPO_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            token = self._wait_for_token()
            self.client = ChangeDetectionClient(self.url, api_key=token, timeout=30)
            self._wait_for_api()
            if not self.keep_default_watches:
                for uuid in list(self.client.list_watches()):  # a fresh install adds example watches
                    self.client.delete_watch(uuid)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _wait_for_token(self) -> str:
        cfg = self.datastore / "changedetection.json"
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise LiveCdioError(f"changedetection.io 进程提前退出（code {self.proc.returncode}）")
            try:
                token = json.loads(cfg.read_text())["settings"]["application"]["api_access_token"]
                if token:
                    return token
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.5)
        raise LiveCdioError("等待 changedetection.io 生成 API token 超时")

    def _wait_for_api(self) -> None:
        deadline = time.monotonic() + self.startup_timeout
        last = None
        while time.monotonic() < deadline:
            try:
                self.client.system_info()
                return
            except CdioError as e:
                last = e
                time.sleep(0.5)
        raise LiveCdioError(f"changedetection.io API 未就绪：{last}")

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.datastore:
            shutil.rmtree(self.datastore, ignore_errors=True)

    # -- observing -------------------------------------------------------------------------

    def wait_for_snapshots(self, uuid: str, n: int, timeout: float = 90) -> list[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            keys = sorted(int(k) for k in self.client.history(uuid))
            if len(keys) >= n:
                return keys
            watch = self.client.get_watch(uuid)
            if watch.get("last_error"):
                raise LiveCdioError(f"changedetection.io 抓取失败：{watch.get('last_error')}")
            time.sleep(1)
        raise LiveCdioError(f"等待第 {n} 个快照超时（内容可能与上一版本相同，或网络不可达）")

    def observe_versions(self, title: str, urls: list[str]) -> dict:
        """Snapshot each URL in turn on ONE watch, so changedetection.io diffs consecutive versions."""
        uuid = self.client.create_watch(urls[0], title=title)  # creating a watch queues its first check
        snaps = self.wait_for_snapshots(uuid, 1)
        for i, url in enumerate(urls[1:], start=2):
            time.sleep(1.1)  # snapshots are keyed by whole seconds
            self.client.update_watch(uuid, url=url)
            self.client.recheck(uuid)
            snaps = self.wait_for_snapshots(uuid, i)
        return {"uuid": uuid, "snapshot_timestamps": snaps}


def _verify_args(case: dict, base: Path) -> list[str]:
    return ["--verify", "--verify-policy", str(base / case["verify_policy"])] if case.get("verify_policy") else []


def run_case(python: str, case_path: Path, out: Path, log_path: Path | None = None) -> int:
    """Observe a case's real versions in a live changedetection.io and run the full pipeline on them."""
    from .cli import main as cli_main

    case_path = Path(case_path)
    case = json.loads(case_path.read_text(encoding="utf-8"))
    base = case_path.parent
    with LiveCdio(python, log_path=log_path) as cdio:
        obs = cdio.observe_versions(case["watch_title"], [v["url"] for v in case["versions"]])
        os.environ["TECHSIGNAL_CDIO_API_KEY"] = cdio.client.api_key
        try:
            rc = cli_main(["run", "--cdio-url", cdio.url, "--from", str(obs["snapshot_timestamps"][0] - 1),
                           "--uuids", obs["uuid"], "--theses", str(base / case["theses"]),
                           "--sources", str(base / case["sources"]), "--out", str(out)] + _verify_args(case, base))
        finally:
            os.environ.pop("TECHSIGNAL_CDIO_API_KEY", None)
        cdio_version = cdio.client.system_info().get("version")
    if rc == 0:
        record = {
            "case_id": case["case_id"],
            "note": "真实 changedetection.io 实例依次抓取下列已发布版本（按提交固定的 URL）。"
                    "快照时间戳是抓取时间，发布日期取自 git 提交记录。",
            "changedetection_version": cdio_version,
            "watch_uuid": obs["uuid"],
            "observations": [dict(v, snapshot_timestamp=ts)
                             for v, ts in zip(case["versions"], obs["snapshot_timestamps"], strict=True)],
        }
        (Path(out) / "observation_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rc


def run_cross_batch_case(python: str, case_path: Path, out: Path, log_path: Path | None = None) -> int:
    """Like run_case, but the case lists several batches (pairs of version indexes). All versions are
    observed on one watch first; then each batch is processed in order with --track-history, so later
    batches are judged against the history of earlier ones. A copy of the state after each batch is
    kept in out/batch<N>/; the cumulative state is in out/state/."""
    from .cli import main as cli_main

    case_path = Path(case_path)
    case = json.loads(case_path.read_text(encoding="utf-8"))
    base = case_path.parent
    out = Path(out)
    state = out / "state"
    with LiveCdio(python, log_path=log_path) as cdio:
        obs = cdio.observe_versions(case["watch_title"], [v["url"] for v in case["versions"]])
        ts = obs["snapshot_timestamps"]
        os.environ["TECHSIGNAL_CDIO_API_KEY"] = cdio.client.api_key
        try:
            for n, (a, b) in enumerate(case["batches"], 1):
                rc = cli_main(["run", "--cdio-url", cdio.url, "--from", str(ts[a]), "--to", str(ts[b]),
                               "--uuids", obs["uuid"], "--max-lines", "2000", "--track-history",
                               "--theses", str(base / case["theses"]), "--sources", str(base / case["sources"]),
                               "--out", str(state)] + _verify_args(case, base))
                if rc != 0:
                    return rc
                snap = out / f"batch{n}"
                shutil.rmtree(snap, ignore_errors=True)
                shutil.copytree(state, snap)
        finally:
            os.environ.pop("TECHSIGNAL_CDIO_API_KEY", None)
        cdio_version = cdio.client.system_info().get("version")
    record = {
        "case_id": case["case_id"],
        "note": "真实 changedetection.io 实例依次抓取下列已发布版本；各批次按顺序以 --track-history 处理。"
                "快照时间戳是抓取时间，发布日期取自 git 提交记录。",
        "changedetection_version": cdio_version,
        "watch_uuid": obs["uuid"],
        "batches": [{"batch": n, "from_version": case["versions"][a]["commit"], "to_version": case["versions"][b]["commit"]}
                    for n, (a, b) in enumerate(case["batches"], 1)],
        "observations": [dict(v, snapshot_timestamp=t) for v, t in zip(case["versions"], ts, strict=True)],
    }
    (out / "observation_record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="techsignal.live")
    p.add_argument("--python", required=True, help="装有 changedetection.io 依赖的 Python 解释器")
    p.add_argument("--case", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--log", help="changedetection.io 进程日志输出路径")
    a = p.parse_args(argv)
    try:
        case = json.loads(Path(a.case).read_text(encoding="utf-8"))
        runner = run_cross_batch_case if case.get("batches") else run_case
        return runner(a.python, Path(a.case), Path(a.out), Path(a.log) if a.log else None)
    except (LiveCdioError, CdioError) as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
