"""Command line entry point.

    python -m techsignal run --changes demo/changes_report.json --theses demo/theses.json \
        --sources demo/sources.json --provider mock --replay demo/mock_replay.json --out demo/output
    python -m techsignal validate --out demo/output --theses demo/theses.json
    python -m techsignal eval
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .models import Brief, canonical_json
from .providers import MockReplayProvider, OpenAICompatibleProvider
from .store import EvidenceLedger, JudgementHistory, SourceRegistry, load_theses
from .validate import validate_brief, validate_markdown


def _provider(args):
    if args.provider == "none":
        return None
    if args.provider == "mock":
        if not args.replay:
            sys.exit("--provider mock 需要 --replay")
        return MockReplayProvider.load(Path(args.replay))
    return OpenAICompatibleProvider.from_env()


# The ledger lands first and the human-facing brief.md last. Live runs add their captured input and
# run record in front of these.
COMMIT_ORDER = ("evidence.jsonl", "judgements.jsonl", "verification_trace.json", "decision_log.json",
                "brief.json", "brief.md")
LIVE_FILES = ("source_changes_report.json", "run_record.json")


def _commit_outputs(out: Path, files: dict[str, str]) -> None:
    """Stage every output inside `out`, then move each into place, rolling back on failure.

    - Any failure before the moves (bad input, validation, provider crash, a write error while
      staging) leaves the previous outputs exactly as they were.
    - Each move is atomic (os.replace on the same filesystem). If a move fails part-way, the files
      already moved are restored from a backup taken before the first move (R2), so the set stays
      consistent. Not covered: the process being killed between two moves (no journal is kept).
    """
    order = [n for n in LIVE_FILES if n in files] + [n for n in COMMIT_ORDER if n in files]
    out.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".techsignal-staging-", dir=out))
    backup = staging / ".backup"
    backup.mkdir()
    replaced: list[str] = []
    try:
        for name in order:
            (staging / name).write_text(files[name], encoding="utf-8")
        for name in order:
            if (out / name).exists():
                shutil.copy2(out / name, backup / name)
        try:
            for name in order:
                os.replace(staging / name, out / name)
                replaced.append(name)
        except BaseException:
            for name in reversed(replaced):
                if (backup / name).exists():
                    os.replace(backup / name, out / name)
                else:
                    (out / name).unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _fetch_live(args) -> tuple[dict, dict]:
    """Pull a ChangesReport from a running changedetection.io. The API key comes from the
    caller (main(..., api_key=...), used by the workbench and the live harness) or, on the command
    line, from the TECHSIGNAL_CDIO_API_KEY environment variable. It is never written anywhere."""
    import datetime as _dt
    import hashlib

    from . import __version__
    from .cdio_client import ChangeDetectionClient

    key = args.api_key if getattr(args, "api_key", None) is not None else os.getenv("TECHSIGNAL_CDIO_API_KEY")
    client = ChangeDetectionClient(args.cdio_url, api_key=key or None,
                                   timeout=args.timeout)
    info = client.system_info()
    params = {"from": args.from_ts, "to": args.to_ts, "tag": args.tag,
              "uuids": args.uuids.split(",") if args.uuids else None, "page_size": args.page_size,
              "max_lines_per_watch": args.max_lines}
    report = client.get_changes_report(from_ts=args.from_ts, to_ts=args.to_ts, tag=args.tag,
                                       uuids=params["uuids"], page_size=args.page_size,
                                       max_lines_per_watch=args.max_lines)
    record = {
        "input_mode": "live_api",
        "note": "输入由 TechSignal 通过 changedetection.io REST API 在线拉取；原始响应保存在 source_changes_report.json",
        "cdio_base_url": client.base_url,
        "cdio_version": info.get("version"),
        "request_params": params,
        "fetched_at_utc": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "http_requests": client.requests_made,
        "report_sha256": hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest(),
        "watches_in_report": len(report["watches"]),
        "incomplete": bool(report.get("incomplete")),
        "techsignal_version": __version__,
    }
    return report, record, client


def _build_verifier(args, client):
    """Evidence-gathering tools for this run (R3). Only with --verify; the policy file is mandatory."""
    from .verify.engine import Verifier
    from .verify.http import GuardedHTTP
    from .verify.policy import VerifyPolicy
    from .verify.tools import CdioSnapshotSearch, GitHubCommitInfo, GitHubFileSearch

    if not args.verify_policy:
        raise ValueError("--verify 需要同时给出 --verify-policy（补证白名单与限额）")
    policy = VerifyPolicy.load(Path(args.verify_policy))
    if client is not None and policy.allow_cdio_tool:
        policy = policy.with_cdio(client.base_url)
    http = GuardedHTTP(policy)
    tools = {"github_file_search": GitHubFileSearch(http), "github_commit_info": GitHubCommitInfo(http)}
    if client is not None and policy.allow_cdio_tool:
        tools["cdio_snapshot_search"] = CdioSnapshotSearch(client, policy)
    return Verifier(policy, tools)


def cmd_run(args) -> int:
    from .pipeline import run_pipeline

    out = Path(args.out)
    try:
        from . import provenance as prov
        theses = load_theses(Path(args.theses))
        if args.cdio_url:
            if args.from_ts is None:
                raise ValueError("--cdio-url 需要同时给出 --from")
            if args.input_record:
                raise ValueError("--input-record 只用于离线回放（--changes）")
            report, record, client = _fetch_live(args)
            source = report
            provenance = prov.for_live(record, theses)
        else:
            report = record = client = None
            source = Path(args.changes).read_text(encoding="utf-8")
            capture = Path(args.input_record).read_text(encoding="utf-8") if args.input_record else None
            provenance = prov.for_file(source, theses, capture)
        # Nothing under `out` is touched until the run has fully succeeded. Without --append-ledger
        # the ledger simply starts empty in memory; the old file is replaced only at commit time.
        keep_state = args.append_ledger or args.track_history
        ledger = EvidenceLedger(out / "evidence.jsonl" if keep_state else None)
        history = JudgementHistory(out / "judgements.jsonl") if args.track_history else None
        provider = _provider(args)
        result = run_pipeline(
            source, theses, SourceRegistry.load(Path(args.sources)),
            provider=provider, ledger=ledger, persist=False,
            history=history.records if history is not None else None, provenance=provenance,
            verifier=_build_verifier(args, client) if args.verify else None)
        files = {
            "evidence.jsonl": ledger.serialize(),
            "decision_log.json": canonical_json(result.decision_log()) + "\n",
            "brief.json": canonical_json(result.brief.model_dump(mode="json")) + "\n",
            "brief.md": result.markdown,
        }
        if history is not None:
            files["judgements.jsonl"] = history.serialize_with(result.new_records)
        if result.verify_run is not None:
            files["verification_trace.json"] = canonical_json(result.verification_trace()) + "\n"
        if record is not None:
            record.update(provider=result.provider_name, provider_is_mock=bool(provider and provider.is_mock),
                          brief_stats=result.brief.stats)
            files["source_changes_report.json"] = canonical_json(report) + "\n"
            files["run_record.json"] = canonical_json(record) + "\n"
        _commit_outputs(out, files)
    except Exception as e:  # noqa: BLE001 - CLI boundary: report, exit non-zero, outputs untouched
        print(f"失败（{type(e).__name__}）：{e}；已有输出未改动", file=args.stream or sys.stderr)
        return 2

    def say(*parts):
        print(*parts, file=args.stream or sys.stdout)

    s = result.brief.stats
    if record is not None:
        say(f"在线拉取：{record['cdio_base_url']}（changedetection.io {record['cdio_version']}），"
              f"{record['http_requests']} 次请求，{record['watches_in_report']} 个变化监测项")
    say(f"变化事件 {s['events_total']}，过滤 {s['events_filtered']}，评估 {s['assessments']}"
          f"（支持 {s['supports']} / 削弱 {s['weakens']} / 证据不足 {s['insufficient']} / 无关 {s['unrelated']}）")
    say(f"模型层调用 {result.stats.provider_calls} 次（Provider：{result.provider_name or '未配置'}），"
          f"拒绝 {result.stats.provider_rejections}，失败 {result.stats.provider_failures}")
    if result.verify_run is not None:
        vr = result.verify_run
        say(f"补证：{len(vr.summaries)} 项触发，{sum(c.status != 'skipped_duplicate' for c in vr.calls)} 次工具调用，"
              f"新增证据 {len(vr.new_evidence)} 条；" + "；".join(f"{v.assessment_id}→{v.outcome}" for v in vr.summaries))
    say(f"引用完整性校验：通过；输出目录 {out}")
    return 0


def cmd_validate(args) -> int:
    out = Path(args.out)
    brief = Brief.model_validate_json((out / "brief.json").read_text(encoding="utf-8"))
    md = (out / "brief.md").read_text(encoding="utf-8")
    ev_ids = EvidenceLedger(out / "evidence.jsonl").ids()
    th_ids = {t.thesis_id for t in load_theses(Path(args.theses))}
    issues = validate_brief(brief, ev_ids, th_ids) + validate_markdown(md, ev_ids, th_ids, brief.mock_used)
    if issues:
        for i in issues:
            print(i, file=sys.stderr)
        return 1
    n = len(brief.all_statements())
    print(f"引用完整性校验通过：{n} 条陈述，{len(ev_ids)} 条证据")
    return 0


def cmd_eval(args) -> int:
    from .evaluation import run_scenarios
    report = run_scenarios(Path(args.scenarios))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for r in report["results"]:
        if not r["passed"]:
            print(f"未通过 {r['id']}: {r['failures']}", file=sys.stderr)
    return 0 if report["summary"]["scenarios_failed"] == 0 else 1


def main(argv=None, api_key: str | None = None, stream=None) -> int:
    """api_key: passed explicitly by in-process callers (workbench, live harness) so that no shared
    process environment is touched; the command line uses TECHSIGNAL_CDIO_API_KEY instead.
    stream: where messages go (default: stdout/stderr) - lets the workbench capture one run's log
    without redirecting sys.stdout for the whole process."""
    p = argparse.ArgumentParser(prog="techsignal")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="处理一份 changedetection.io ChangesReport 并生成简报")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--changes", help="离线 ChangesReport JSON 文件")
    src.add_argument("--cdio-url", help="在线 changedetection.io 地址；API key 读取环境变量 TECHSIGNAL_CDIO_API_KEY")
    r.add_argument("--from", dest="from_ts", type=int, help="在线模式：起始时间（unix 秒）")
    r.add_argument("--to", dest="to_ts", type=int, help="在线模式：结束时间（unix 秒，默认当前）")
    r.add_argument("--tag", help="在线模式：只取某个分组")
    r.add_argument("--uuids", help="在线模式：逗号分隔的监测项 uuid")
    r.add_argument("--input-record", help="离线回放：在线拉取时生成的 run_record.json（会核对 sha256）")
    r.add_argument("--page-size", type=int, default=50)
    r.add_argument("--timeout", type=float, default=30.0)
    r.add_argument("--theses", required=True)
    r.add_argument("--sources", required=True)
    r.add_argument("--provider", choices=["none", "mock", "openai-compatible"], default="none")
    r.add_argument("--replay", help="Mock 回放文件（--provider mock）")
    r.add_argument("--out", required=True)
    r.add_argument("--append-ledger", action="store_true", help="保留已有证据台账（用于跨批次去重）")
    r.add_argument("--track-history", action="store_true",
                   help="跨批次模式：保留证据台账与研判历史 judgements.jsonl，并与历史判断比对")
    r.add_argument("--max-lines", type=int, default=200, help="在线模式：每个监测项最多返回的差异行数")
    r.add_argument("--verify", action="store_true",
                   help="对存在疑点的研判执行有界补证（确定性策略；需 --verify-policy）")
    r.add_argument("--verify-policy", help="补证白名单与限额（JSON）")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="校验已生成简报的引用完整性")
    v.add_argument("--out", required=True)
    v.add_argument("--theses", required=True)
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("eval", help="运行虚构场景评测并输出指标")
    e.add_argument("--scenarios", default=str(Path(__file__).resolve().parent.parent / "tests" / "scenarios"))
    e.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    args.api_key = api_key
    args.stream = stream
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
