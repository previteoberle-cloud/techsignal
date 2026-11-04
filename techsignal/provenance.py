"""Run provenance: what actually happened in a run, from explicit metadata only (R3 / P0).

Nothing here is inferred from file or directory names. The inputs are:
  - how the CLI obtained the ChangesReport (live REST API call vs a file);
  - for a file, an optional capture record (run_record.json of the live run that fetched it), which
    is only accepted when its report_sha256 matches the file's content;
  - the `fictional` flag every thesis carries;
  - which provider was configured and whether any judgement actually came from it;
  - whether evidence-gathering tools ran.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import Basis, Thesis, canonical_json


class ProvenanceError(ValueError):
    pass


class RunProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_mode: Literal["live_cdio_api", "file"]
    # live_cdio_api | replay_of_captured_live (file + verified capture record) | file_unverified
    input_origin: Literal["live_cdio_api", "replay_of_captured_live", "file_unverified"]
    data_nature: Literal["fictional", "public", "mixed"]
    cdio_base_url: str | None = None
    cdio_version: str | None = None
    fetched_at_utc: str | None = None
    report_sha256: str | None = None
    model_layer: Literal["none", "mock_replay", "real_model"] = "none"
    model_name: str | None = None
    model_judgements: int = 0
    verification: Literal["off", "deterministic_policy"] = "off"
    verification_tool_calls: int = 0


def report_sha256(report: dict) -> str:
    return hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()


def data_nature(theses: list[Thesis]) -> str:
    flags = {t.fictional for t in theses}
    return "fictional" if flags == {True} else "public" if flags == {False} else "mixed"


def for_live(record: dict, theses: list[Thesis]) -> RunProvenance:
    return RunProvenance(input_mode="live_cdio_api", input_origin="live_cdio_api", data_nature=data_nature(theses),
                         cdio_base_url=record.get("cdio_base_url"), cdio_version=record.get("cdio_version"),
                         fetched_at_utc=record.get("fetched_at_utc"), report_sha256=record.get("report_sha256"))


def for_file(report_text: str, theses: list[Thesis], capture_record_text: str | None = None) -> RunProvenance:
    nature = data_nature(theses)
    if capture_record_text is None:
        return RunProvenance(input_mode="file", input_origin="file_unverified", data_nature=nature)
    try:
        record = json.loads(capture_record_text)
        report = json.loads(report_text)
    except json.JSONDecodeError as e:
        raise ProvenanceError(f"运行记录或输入不是合法 JSON：{e}") from None
    if record.get("input_mode") != "live_api":
        raise ProvenanceError("--input-record 不是在线拉取的运行记录（input_mode != live_api）")
    actual = report_sha256(report)
    if record.get("report_sha256") != actual:
        raise ProvenanceError(f"输入文件与运行记录不符：sha256 {actual[:12]}… ≠ 记录中的 {str(record.get('report_sha256'))[:12]}…")
    return RunProvenance(input_mode="file", input_origin="replay_of_captured_live", data_nature=nature,
                         cdio_base_url=record.get("cdio_base_url"), cdio_version=record.get("cdio_version"),
                         fetched_at_utc=record.get("fetched_at_utc"), report_sha256=actual)


def with_model_usage(p: RunProvenance, provider, assessments) -> RunProvenance:
    used = sum(a.basis in (Basis.MODEL, Basis.MOCK_MODEL) for a in assessments)
    if provider is None:
        layer, name = "none", None
    else:
        layer, name = ("mock_replay" if provider.is_mock else "real_model"), provider.name
    return p.model_copy(update={"model_layer": layer, "model_name": name, "model_judgements": used})


_NATURE_ZH = {"fictional": "虚构测试数据（研究判断均标注为虚构）", "public": "真实公开资料",
              "mixed": "混合：部分研究判断为虚构，部分基于公开资料"}


def disclaimers(p: RunProvenance) -> list[str]:
    out = ["本速览由 TechSignal 研究原型生成，不构成投资建议。研究结论不会因新信息自动改变，「削弱」只触发人工复核建议。"]
    if p.input_origin == "live_cdio_api":
        out.append(f"输入：TechSignal 于 {p.fetched_at_utc} 通过 REST API 在线拉取 changedetection.io {p.cdio_version}"
                   f"（{p.cdio_base_url}）的变化数据（GET /api/v1/changes）。")
    elif p.input_origin == "replay_of_captured_live":
        out.append(f"输入：离线回放。原始数据由 TechSignal 于 {p.fetched_at_utc} 从 changedetection.io {p.cdio_version} "
                   f"在线拉取，sha256 已与运行记录核对（{(p.report_sha256 or '')[:12]}…）；本次运行没有连接 changedetection.io。")
    elif p.data_nature == "fictional":
        out.append("输入：离线虚构 Fixture（changedetection.io ChangesReport 格式；没有在线运行记录，本次运行没有连接 changedetection.io）。")
    else:
        out.append("输入：离线文件（changedetection.io ChangesReport 格式），未提供运行记录，来源未经核验。")
    out.append(f"数据性质：{_NATURE_ZH[p.data_nature]}。")
    if p.model_layer == "none":
        out.append("模型层：未配置；本速览中的判断全部由确定性规则作出。")
    elif p.model_layer == "mock_replay":
        out.append(f"模型层：Mock 回放（{p.model_name}），共 {p.model_judgements} 条判断来自人工预置的回放数据，"
                   "不是真实大模型推理；标注「Mock 回放」的判断即是。")
    else:
        out.append(f"模型层：{p.model_name}，共 {p.model_judgements} 条判断来自该模型，标注「模型推断」。")
    if p.verification == "deterministic_policy":
        out.append(f"补证：由确定性策略触发并选择工具，共实际执行 {p.verification_tool_calls} 次工具调用"
                   "（HTTP 请求，地址与结果见 verification_trace.json）；没有使用大模型决策。")
    return out
