"""R6 Checkpoint 2: connect a research project to the user's own changedetection.io.

What happens, and only when the user clicks for it:
  1. connect():        read systeminfo and the existing watches (nothing is written);
  2. plan_watches():   which project URLs already have a watch (reuse it) and which need one;
  3. apply_plan():     after explicit confirmation, create the missing watches (only URLs that are in
                       the project and pass the public-URL check) and record the watch <-> project
                       links in monitor.json;
  4. check_now():      ask changedetection.io to re-check the linked watches once, wait for it, then
                       read the ChangesReport since the project's cursor. No new snapshot / no net
                       difference -> a "no change" record and NO assessment. Otherwise the real
                       pipeline runs (techsignal.cli live mode, --track-history) into the project's
                       state/ and a copy is kept in runs/<run id>/.

There is no scheduler: nothing runs in the background (continuous monitoring is R7).
The API key is passed as an argument and never stored; monitor.json keeps only the base URL.
project.json (the user's judgement) is never written here.
"""
from __future__ import annotations

import io
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import projects as P
from .cdio_client import CdioError, ChangeDetectionClient

DEFAULT_WAIT = 90.0


class MonitorError(P.ProjectError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WatchLink(_Strict):
    url: str                   # the project URL this watch follows
    watch_uuid: str
    created_by_techsignal: bool
    linked_at: str


class MonitorState(_Strict):
    cdio_url: str = ""         # base URL only; the API key is never stored
    cdio_version: str | None = None
    watches: list[WatchLink] = Field(default_factory=list)
    # Snapshot time (unix s) up to which changes were processed. Changes are read strictly after it.
    cursor: int | None = None


class CheckRecord(_Strict):
    check_id: str
    started_at: str
    finished_at: str
    outcome: Literal["assessed", "no_new_snapshot", "no_net_change", "failed"]
    message: str
    thesis_version: int
    from_ts: int | None = None
    to_ts: int | None = None
    rechecked: list[str] = Field(default_factory=list)
    new_snapshots: dict[str, int] = Field(default_factory=dict)
    watch_errors: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None


class PlanItem(_Strict):
    url: str
    action: Literal["already_linked", "link_existing", "create"]
    watch_uuid: str | None = None
    title: str = ""


class RunRef(_Strict):
    run_id: str
    check_id: str
    thesis_version: int
    created_at: str
    from_ts: int
    to_ts: int


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------

def _pdir(store: P.ProjectStore, project_id: str) -> Path:
    return store.project_dir(project_id)


def load_state(store: P.ProjectStore, project_id: str) -> MonitorState:
    f = _pdir(store, project_id) / "monitor.json"
    if not f.exists():
        return MonitorState()
    try:
        return MonitorState.model_validate_json(f.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as e:
        raise MonitorError(f"监测状态文件损坏：{e}") from None


def _save_state(store: P.ProjectStore, project_id: str, state: MonitorState) -> None:
    P.atomic_write(_pdir(store, project_id) / "monitor.json", state.model_dump_json(indent=2) + "\n")


def checks(store: P.ProjectStore, project_id: str) -> list[CheckRecord]:
    f = _pdir(store, project_id) / "checks.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(CheckRecord.model_validate_json(line))
            except ValidationError:
                continue
    return out


def _append_check(store: P.ProjectStore, project_id: str, rec: CheckRecord) -> None:
    f = _pdir(store, project_id) / "checks.jsonl"
    old = f.read_text(encoding="utf-8") if f.exists() else ""
    P.atomic_write(f, old + rec.model_dump_json() + "\n")


def runs(store: P.ProjectStore, project_id: str) -> list[RunRef]:
    d = _pdir(store, project_id) / "runs"
    out = []
    for f in d.glob("*/run_meta.json") if d.exists() else []:
        try:
            out.append(RunRef.model_validate_json(f.read_text(encoding="utf-8")))
        except (OSError, ValidationError):
            continue
    return sorted(out, key=lambda r: r.run_id)


def run_dir(store: P.ProjectStore, project_id: str, run_id: str) -> Path:
    if not run_id.replace("-", "").isalnum():
        raise MonitorError("运行编号无效")
    d = (_pdir(store, project_id) / "runs" / run_id).resolve()
    if d.parent != (_pdir(store, project_id) / "runs").resolve():
        raise MonitorError("运行编号无效")
    return d


# ---------------------------------------------------------------------------
# Connection and watches
# ---------------------------------------------------------------------------

def client_for(base_url: str, api_key: str | None, timeout: float = 15.0) -> ChangeDetectionClient:
    url = (base_url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise MonitorError("changedetection.io 地址必须以 http:// 或 https:// 开头")
    return ChangeDetectionClient(url, api_key=api_key or None, timeout=timeout, retries=1)


def connect(client: ChangeDetectionClient) -> dict:
    """{'version', 'watches': {uuid: {'url', 'title', 'last_checked'}}}. Read-only."""
    try:
        info = client.system_info()
        watches = client.list_watches()
    except CdioError as e:
        raise MonitorError(f"无法连接 changedetection.io：{e}") from None
    out = {}
    for uuid, w in watches.items():
        if isinstance(w, dict):
            out[uuid] = {"url": w.get("url", ""), "title": w.get("title") or "", "last_checked": w.get("last_checked")}
    return {"version": info.get("version"), "watches": out}


def _norm(url: str) -> str:
    try:
        return P._url_key(P.check_public_url(url, allow_private=True))
    except P.ProjectError:
        return url


def watch_title(project: P.Project, src: P.SourceInput) -> str:
    return f"TechSignal｜{project.spec.name}｜{src.label or src.url}"[:200]


def plan_watches(project: P.Project, state: MonitorState, existing: dict) -> list[PlanItem]:
    """For every project URL: already linked, an existing watch with the same URL to reuse, or create."""
    linked = {_norm(w.url): w for w in state.watches if w.watch_uuid in existing}
    by_url = {}
    for uuid, w in existing.items():
        by_url.setdefault(_norm(w.get("url", "")), uuid)
    plan = []
    for src in project.spec.sources:
        key = _norm(src.url)
        if key in linked:
            plan.append(PlanItem(url=src.url, action="already_linked", watch_uuid=linked[key].watch_uuid))
        elif key in by_url:
            plan.append(PlanItem(url=src.url, action="link_existing", watch_uuid=by_url[key],
                                 title=existing[by_url[key]].get("title", "")))
        else:
            plan.append(PlanItem(url=src.url, action="create", title=watch_title(project, src)))
    return plan


def apply_plan(store: P.ProjectStore, project_id: str, client: ChangeDetectionClient, plan: list[PlanItem],
               confirmed: bool) -> MonitorState:
    """Create/link watches. Refuses without confirmed=True. Only URLs of the project, re-checked."""
    if not confirmed:
        raise MonitorError("需要你确认后才会在 changedetection.io 中创建监测项")
    project = store.get(project_id)
    if project.status != "ready":
        raise MonitorError("草稿项目不能建立监测，请先完成创建")
    allowed = {_norm(s.url): s for s in project.spec.sources}
    with P.file_lock(_pdir(store, project_id) / ".monitor-lock"):
        state = load_state(store, project_id)
        if state.cdio_url and state.cdio_url != client.base_url:
            # a project follows one changedetection.io; links to another instance would not resolve
            raise MonitorError(f"该项目已关联 {state.cdio_url}，不能同时关联另一个 changedetection.io 实例")
        known = {w.watch_uuid for w in state.watches}
        if state.cursor is None:
            # Everything up to now is history, not news: later checks read changes after this moment.
            state.cursor = int(time.time())
        for item in plan:
            src = allowed.get(_norm(item.url))
            if src is None:
                raise MonitorError(f"网址不在本项目中，已拒绝：{item.url}")
            P.check_public_url(src.url, allow_private=store.allow_private_urls)
            try:
                if item.action == "create":
                    uuid = client.create_watch(src.url, title=watch_title(project, src))
                    created = True
                elif item.action == "link_existing" and item.watch_uuid:
                    uuid, created = item.watch_uuid, False
                else:
                    continue
            except CdioError as e:
                _save_state(store, project_id, state.model_copy(update={"cdio_url": client.base_url}))
                raise MonitorError(f"创建监测项失败（{src.url}）：{e}") from None
            if uuid not in known:
                state.watches.append(WatchLink(url=src.url, watch_uuid=uuid, created_by_techsignal=created,
                                               linked_at=P.now_iso()))
                known.add(uuid)
            state.cdio_url = client.base_url
            _save_state(store, project_id, state)      # each link is recorded as soon as it exists
        try:
            state.cdio_version = client.system_info().get("version")
        except CdioError:
            pass
        state.cdio_url = client.base_url
        _save_state(store, project_id, state)
    return state


# ---------------------------------------------------------------------------
# One manual check
# ---------------------------------------------------------------------------

def _snapshot_keys(client: ChangeDetectionClient, uuid: str) -> list[int]:
    try:
        return sorted(int(k) for k in client.history(uuid))
    except (ValueError, TypeError):
        return []


def _check_finished(before: dict, now: dict) -> bool:
    """changedetection.io sets last_checked when a check STARTS (worker.py, fetch_start_time) and
    increments check_count only after the snapshot was saved. So a finished check is a higher
    check_count; last_checked is only used when the server does not report check_count."""
    if isinstance(before.get("check_count"), int) and isinstance(now.get("check_count"), int):
        return now["check_count"] > before["check_count"]
    return (now.get("last_checked") or 0) > (before.get("last_checked") or 0)


def _wait_for_check(client: ChangeDetectionClient, uuid: str, before: dict, deadline: float,
                    sleep: Callable[[float], None]) -> dict:
    """Poll the watch until changedetection.io has finished the requested check."""
    while True:
        w = client.get_watch(uuid)
        if _check_finished(before, w) or time.monotonic() > deadline:
            return w
        sleep(1.0)


def model_configured() -> bool:
    """An OpenAI-compatible model service configured by the operator (environment, never the UI)."""
    import os
    return bool(os.getenv("TECHSIGNAL_LLM_BASE_URL") and os.getenv("TECHSIGNAL_LLM_MODEL"))


def check_now(store: P.ProjectStore, project_id: str, client: ChangeDetectionClient, wait: float = DEFAULT_WAIT,
              sleep: Callable[[float], None] = time.sleep, use_model: bool = False) -> CheckRecord:
    """Re-check the project's watches once and assess real new changes. Never fabricates a result:
    without a new snapshot or with no net difference, nothing is assessed and the record says so."""
    from .cli import main as cli_main

    project = store.get(project_id)
    if project.status != "ready":
        raise MonitorError("草稿项目不能执行监测，请先完成创建")
    started = P.now_iso()
    check_id = "c-" + started.replace("-", "").replace(":", "").replace("T", "").rstrip("Z") + "-" + P.secrets.token_hex(2)
    with P.file_lock(_pdir(store, project_id) / ".monitor-lock", timeout=2):
        state = load_state(store, project_id)
        if not state.watches:
            raise MonitorError("该项目还没有关联任何监测项")
        if state.cdio_url and state.cdio_url != client.base_url:
            raise MonitorError(f"该项目关联的是 {state.cdio_url}，与当前连接的地址不一致")
        uuids = [w.watch_uuid for w in state.watches]
        rec = dict(check_id=check_id, started_at=started, thesis_version=project.version, rechecked=[],
                   new_snapshots={}, watch_errors={})

        def finish(outcome, message, **kw) -> CheckRecord:
            r = CheckRecord(**{**rec, **kw}, outcome=outcome, message=message, finished_at=P.now_iso())
            _append_check(store, project_id, r)
            return r

        try:
            before = {u: client.get_watch(u) for u in uuids}
            keys_before = {u: _snapshot_keys(client, u) for u in uuids}
            for u in uuids:
                client.recheck(u)
                rec["rechecked"].append(u)
            deadline = time.monotonic() + wait
            after = {u: _wait_for_check(client, u, before[u], deadline, sleep) for u in uuids}
            keys_after = {u: _snapshot_keys(client, u) for u in uuids}
        except CdioError as e:
            return finish("failed", f"与 changedetection.io 通信失败：{e}")
        rec["new_snapshots"] = {u: len(set(keys_after[u]) - set(keys_before[u])) for u in uuids}
        rec["watch_errors"] = {u: str(after[u].get("last_error")) for u in uuids if after[u].get("last_error")}
        not_done = [u for u in uuids if not _check_finished(before[u], after[u])]
        cursor = state.cursor if state.cursor is not None else 0
        latest = max([k for ks in keys_after.values() for k in ks] or [cursor])
        note = ""
        if not_done:
            note += f"（{len(not_done)} 个监测项在 {int(wait)} 秒内未完成检查，其结果会在下次检查时读取）"
        if rec["watch_errors"]:
            note += "监测报错：" + "；".join(rec["watch_errors"].values())
        if latest <= cursor:
            return finish("no_new_snapshot", "本次没有新的快照：页面内容与上次相同，或尚未完成抓取。没有产生新的研判。" + note,
                          from_ts=cursor, to_ts=cursor)
        try:
            report = client.get_changes_report(from_ts=cursor, to_ts=latest, uuids=uuids, max_lines_per_watch=2000)
        except CdioError as e:
            return finish("failed", f"读取变化报告失败：{e}", from_ts=cursor, to_ts=latest)
        real = [w for w in report.get("watches", []) if not w.get("no_net_difference") and not w.get("error")]
        if not real:
            state.cursor = latest
            _save_state(store, project_id, state)
            return finish("no_net_change", "有新的快照，但与上次处理时相比没有实质差异（可能是基线首次抓取或内容来回变化）。没有产生新的研判。" + note,
                          from_ts=cursor, to_ts=latest)
        # Real new changes: run the real pipeline (live mode) into the project's cumulative state.
        state_dir = _pdir(store, project_id) / "state"
        buf = io.StringIO()
        api_key = client.api_key or ""
        rc = cli_main(["run", "--cdio-url", client.base_url, "--from", str(cursor), "--to", str(latest),
                       "--uuids", ",".join(uuids), "--max-lines", "2000", "--track-history",
                       "--theses", str(store.theses_path(project_id)), "--sources", str(store.sources_path(project_id)),
                       "--out", str(state_dir)]
                      + (["--provider", "openai-compatible"] if use_model and model_configured() else []),
                      api_key=api_key, stream=buf)
        if rc != 0:
            lines = buf.getvalue().strip().splitlines()
            return finish("failed", f"研判流水线失败：{lines[-1] if lines else '未知错误'}", from_ts=cursor, to_ts=latest)
        run_id = f"r-{latest}-{P.secrets.token_hex(2)}"
        dst = run_dir(store, project_id, run_id)
        shutil.copytree(state_dir, dst, ignore=shutil.ignore_patterns(".techsignal-staging-*", ".lock"))
        # keep the judgement exactly as it was for this run, so later edits do not rewrite its view
        shutil.copy2(store.theses_path(project_id), dst / "theses.json")
        meta = RunRef(run_id=run_id, check_id=check_id, thesis_version=project.version, created_at=P.now_iso(),
                      from_ts=cursor, to_ts=latest)
        P.atomic_write(dst / "run_meta.json", meta.model_dump_json(indent=2) + "\n")
        (dst / "run_log.txt").write_text(buf.getvalue(), encoding="utf-8")
        state.cursor = latest
        _save_state(store, project_id, state)
        n = len(real)
        return finish("assessed", f"发现 {n} 个监测项有实际变化，已用真实流水线完成研判。" + note,
                      from_ts=cursor, to_ts=latest, run_id=run_id)


def summary(store: P.ProjectStore, project_id: str) -> dict:
    st = load_state(store, project_id)
    cs = checks(store, project_id)
    return {"state": st, "checks": cs, "runs": runs(store, project_id),
            "last_check": cs[-1] if cs else None}


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
