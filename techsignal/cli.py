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


# Order matters only if the process dies between two os.replace() calls: the ledger lands first and
# the human-facing brief.md last, so a brief is never newer than the evidence it cites.
COMMIT_ORDER = ("evidence.jsonl", "decision_log.json", "brief.json", "brief.md")


def _commit_outputs(out: Path, files: dict[str, str]) -> None:
    """Write every output to a staging directory inside `out`, then move each into place.

    Any failure before the moves (bad input, validation, provider crash, a write error while
    staging) leaves the previous outputs exactly as they were. The moves themselves are atomic per
    file (os.replace on the same filesystem); the set of four is not atomic as a group.
    """
    out.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".techsignal-staging-", dir=out))
    try:
        for name in COMMIT_ORDER:
            (staging / name).write_text(files[name], encoding="utf-8")
        for name in COMMIT_ORDER:
            os.replace(staging / name, out / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def cmd_run(args) -> int:
    from .pipeline import run_pipeline

    out = Path(args.out)
    try:
        # Nothing under `out` is touched until the run has fully succeeded. Without --append-ledger
        # the ledger simply starts empty in memory; the old file is replaced only at commit time.
        ledger = EvidenceLedger(out / "evidence.jsonl" if args.append_ledger else None)
        result = run_pipeline(
            Path(args.changes).read_text(encoding="utf-8"),
            load_theses(Path(args.theses)), SourceRegistry.load(Path(args.sources)),
            provider=_provider(args), ledger=ledger, persist=False)
        _commit_outputs(out, {
            "evidence.jsonl": ledger.serialize(),
            "decision_log.json": canonical_json(result.decision_log()) + "\n",
            "brief.json": canonical_json(result.brief.model_dump(mode="json")) + "\n",
            "brief.md": result.markdown,
        })
    except Exception as e:  # noqa: BLE001 - CLI boundary: report, exit non-zero, outputs untouched
        print(f"失败（{type(e).__name__}）：{e}；已有输出未改动", file=sys.stderr)
        return 2

    s = result.brief.stats
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
    r.add_argument("--changes", required=True)
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
