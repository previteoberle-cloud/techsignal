"""Command line entry point.

    python -m techsignal run --changes demo/changes_report.json --theses demo/theses.json \
        --sources demo/sources.json --provider mock --replay demo/mock_replay.json --out demo/output
    python -m techsignal validate --out demo/output --theses demo/theses.json
    python -m techsignal eval
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import Brief, canonical_json
from .providers import MockReplayProvider, OpenAICompatibleProvider
from .store import EvidenceLedger, SourceRegistry, StoreError, load_theses
from .validate import CitationError, validate_brief, validate_markdown


def _provider(args):
    if args.provider == "none":
        return None
    if args.provider == "mock":
        if not args.replay:
            sys.exit("--provider mock 需要 --replay")
        return MockReplayProvider.load(Path(args.replay))
    return OpenAICompatibleProvider.from_env()


def cmd_run(args) -> int:
    from .adapters.cdio import AdapterError
    from .pipeline import run_pipeline

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ledger_path = out / "evidence.jsonl"
    if ledger_path.exists() and not args.append_ledger:
        ledger_path.unlink()  # fresh ledger -> reproducible output
    try:
        result = run_pipeline(
            Path(args.changes).read_text(encoding="utf-8"),
            load_theses(Path(args.theses)), SourceRegistry.load(Path(args.sources)),
            provider=_provider(args), ledger=EvidenceLedger(ledger_path))
    except (AdapterError, StoreError, CitationError) as e:
        print(f"失败：{e}", file=sys.stderr)
        return 2

    (out / "brief.md").write_text(result.markdown, encoding="utf-8")
    (out / "brief.json").write_text(canonical_json(result.brief.model_dump(mode="json")) + "\n", encoding="utf-8")
    (out / "decision_log.json").write_text(canonical_json(result.decision_log()) + "\n", encoding="utf-8")
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
