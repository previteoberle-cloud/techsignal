"""R6: the evaluation set against a REAL, operator-authorised model service.

BLOCKED unless all of these are set by whoever runs it (never by this repository or its CI):
  TECHSIGNAL_LLM_BASE_URL, TECHSIGNAL_LLM_MODEL, TECHSIGNAL_LLM_API_KEY  (OpenAI-compatible service)
  TECHSIGNAL_REAL_LLM_AUTHORIZED=1                                     (explicit authorisation)
No business code changes are needed to run it. It checks the protocol (every verdict validated or
conservatively degraded, no crash) and prints the table; it asserts no accuracy figure.
"""
import os

import pytest

from techsignal import eval_r6 as E

pytestmark = pytest.mark.skipif(E.real_model_status() is not None,
                                reason=E.real_model_status() or "")


def test_real_model_on_the_evaluation_set(tmp_path):
    rep = E.evaluate()
    assert "real_model" in rep["summary"] and "status" not in rep["summary"]["real_model"]
    allowed = {"rule", "model_validated", "model_rejected", "model_failed", "model_skipped", "no_model"}
    for r in rep["results"]:
        for row in r["real_model"]["rows"].values():
            assert set(row["sources"]) <= allowed
    out = os.getenv("TECHSIGNAL_REAL_LLM_REPORT") or str(tmp_path / "R6_EVAL_real.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(E.to_markdown(rep))
    print(E.to_markdown(rep))
