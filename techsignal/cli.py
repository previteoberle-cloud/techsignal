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
from .store import EvidenceLedger, SourceRegistry, load_theses
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
COMMIT_ORDER = ("evidence.jsonl", "decision_log.json", "brief.json", "brief.md")
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
    TECHSIGNAL_CDIO_API_KEY environment variable only and is never written anywhere."""
    import datetime as _dt
    import hashlib

    from . import __version__
    from .cdio_client import ChangeDetectionClient

    client = ChangeDetectionClient(args.cdio_url, api_key=os.getenv("TECHSIGNAL_CDIO_API_KEY") or None,
                                   timeout=args.timeout)
    info = client.system_info()
    params = {"from": args.from_ts, "to": args.to_ts, "tag": args.tag,
              "uuids": args.uuids.split(",") if args.uuids else None, "page_size": args.page_size}
    report = client.get_changes_report(from_ts=args.from_ts, to_ts=args.to_ts, tag=args.tag,
                                       uuids=params["uuids"], page_size=args.page_size)
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
    return report, record


def cmd_run(args) -> int:
    from .pipeline import run_pipeline

    out = Path(args.out)
    try:
        if args.cdio_url:
            if args.from_ts is None:
                raise ValueError("--cdio-url 需要同时给出 --from")
            report, record = _fetch_live(args)
            source = report
        else:
            report = record = None
            source = Path(args.changes).read_text(encoding="utf-8")
        # Nothing under `out` is touched until the run has fully succeeded. Without --append-ledger
        # the ledger simply starts empty in memory; the old file is replaced only at commit time.
        ledger = EvidenceLedger(out / "evidence.jsonl" if args.append_ledger else None)
        provider = _provider(args)
        result = run_pipeline(
            source, load_theses(Path(args.theses)), SourceRegistry.load(Path(args.sources)),
            provider=provider, ledger=ledger, persist=False)
        files = {
            "evidence.jsonl": ledger.serialize(),
            "decision_log.json": canonical_json(result.decision_log()) + "\n",
            "brief.json": canonical_json(result.brief.model_dump(mode="json")) + "\n",
            "brief.md": result.markdown,
        }
        if record is not None:
            record.update(provider=result.provider_name, provider_is_mock=bool(provider and provider.is_mock),
                          brief_stats=result.brief.stats)
            files["source_changes_report.json"] = canonical_json(report) + "\n"
            files["run_record.json"] = canonical_json(record) + "\n"
        _commit_outputs(out, files)
    except Exception as e:  # noqa: BLE001 - CLI boundary: report, exit non-zero, outputs untouched
        print(f"失败（{type(e).__name__}）：{e}；已有输出未改动", file=sys.stderr)
        return 2

    s = result.brief.stats
    if record is not None:
        print(f"在线拉取：{record['cdio_base_url']}（changedetection.io {record['cdio_version']}），"
              f"{record['http_requests']} 次请求，{record['watches_in_report']} 个变化监测项")
    print(f"变化事件 {s['events_total']}，过滤 {s['events_filtered']}，评估 {s['assessments']}"
          f"（支持 {s['supports']} / 削弱 {s['weakens']} / 证据不足 {s['insufficient']} / 无关 {s['unrelated']}）")
    print(f"模型层调用 {result.stats.provider_calls} 次（Provider：{result.provider_name or '未配置'}），"
          f"拒绝 {result.stats.provider_rejections}，失败 {result.stats.provider_failures}")
    print(f"引用完整性校验：通过；输出目录 {out}")
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


def main(argv=None) -> int:
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
    r.add_argument("--page-size", type=int, default=50)
    r.add_argument("--timeout", type=float, default=30.0)
    r.add_argument("--theses", required=True)
    r.add_argument("--sources", required=True)
    r.add_argument("--provider", choices=["none", "mock", "openai-compatible"], default="none")
    r.add_argument("--replay", help="Mock 回放文件（--provider mock）")
    r.add_argument("--out", required=True)
    r.add_argument("--append-ledger", action="store_true", help="保留已有证据台账（用于跨批次去重）")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("validate", help="校验已生成简报的引用完整性")
    v.add_argument("--out", required=True)
    v.add_argument("--theses", required=True)
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("eval", help="运行虚构场景评测并输出指标")
    e.add_argument("--scenarios", default=str(Path(__file__).resolve().parent.parent / "tests" / "scenarios"))
    e.set_defaults(func=cmd_eval)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
