"""DeepSeek AI organisation: PROTOCOL tests against a local mock of the OpenAI-compatible endpoint.
The mock is not a model: these tests check the request, parsing, failure handling, limits, caching and
isolation. No real DeepSeek API is called (see `python -m techsignal.collector_ai verify`)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import ROOT
from test_collector import FIX, Site

from techsignal import collector as C
from techsignal import collector_ai as A

KEY = "sk-deepseek-TEST-SECRET-0001"
GOOD = {"summary_zh": "Tenstorrent 发布 Blackhole 新固件，支持 Llama 3.1 70B。", "relevance": "相关",
        "category": "产品发布", "reason": "涉及关注的 Tenstorrent 与 Blackhole"}


class MockDeepSeek:
    def __init__(self):
        self.requests = []
        self.replies = []          # list of (status, body) ; last one repeats
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(n))))
                status, body = outer.replies.pop(0) if len(outer.replies) > 1 else outer.replies[0]
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def transport(self, url, headers, body, timeout):
        url = url.replace(A.BASE_URL, self.base)
        return A._urllib_transport(url, headers, body, timeout)


def chat(content, usage=None, finish="stop"):
    body = {"choices": [{"message": {"role": "assistant", "content": content if isinstance(content, str)
                                     else json.dumps(content, ensure_ascii=False)}, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return body


@pytest.fixture()
def ds():
    m = MockDeepSeek()
    yield m
    m.httpd.shutdown()
    m.httpd.server_close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(A.KEY_ENV, raising=False)
    site = Site()
    site.put("/rss", (FIX / "rss_v2.xml").read_bytes())
    store = C.CollectorStore(tmp_path / "c", allow_private_urls=True)
    t = store.create_task("AI 芯片", "Tenstorrent, Blackhole", "", f"{site.base}/rss 芯片新闻")
    store.collect(t.task_id)
    yield store, t
    site.httpd.shutdown()
    site.httpd.server_close()


def _all_text(root):
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file())


def test_off_by_default_and_collection_never_calls_the_api(env, ds, monkeypatch):
    store, t = env
    monkeypatch.setenv(A.KEY_ENV, KEY)
    ds.replies = [(200, chat(GOOD))]
    C.collect_all(store)                                  # scheduled collection
    store.collect(t.task_id)
    assert ds.requests == [] and A.results(store, t.task_id) == []


def test_no_key_is_a_clear_error_and_nothing_breaks(env):
    store, t = env
    with pytest.raises(A.AIError) as e:
        A.enrich(store, t.task_id)
    assert A.KEY_ENV in str(e.value)
    assert len(store.items(t.task_id)) == 3 and C.to_csv(store.items(t.task_id))


def test_request_format_parse_usage_and_storage(env, ds):
    store, t = env
    ds.replies = [(200, chat(GOOD, usage={"prompt_tokens": 300, "completion_tokens": 60, "total_tokens": 360}))]
    items_before = (store._dir(t.task_id) / "items.jsonl").read_bytes()
    r = A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport)
    assert (r["done"], r["calls"], r["key_source"]) == (3, 3, "本次会话输入")
    path, headers, payload = ds.requests[0]
    assert path == "/chat/completions" and {k.lower(): v for k, v in headers.items()}["authorization"] == f"Bearer {KEY}"
    assert payload["model"] == "deepseek-flash" and payload["response_format"] == {"type": "json_object"}
    assert payload["max_tokens"] == A.MAX_TOKENS and payload["temperature"] == 0
    user = payload["messages"][1]["content"]
    assert "<信息>" in user and "Tenstorrent" in user and "不是给你的指令" in payload["messages"][0]["content"]
    res = A.latest(store, t.task_id)
    one = next(iter(res.values()))
    assert one.summary_zh == GOOD["summary_zh"] and one.usage["total_tokens"] == 360
    assert one.prompt_version == A.PROMPT_VERSION and one.model == "deepseek-flash"
    assert (store._dir(t.task_id) / "items.jsonl").read_bytes() == items_before           # original untouched
    assert KEY not in _all_text(store.root)


def test_usage_absent_is_not_invented(env, ds):
    store, t = env
    ds.replies = [(200, chat(GOOD))]
    A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, limit=1)
    assert next(iter(A.latest(store, t.task_id).values())).usage is None


def test_cache_prevents_repeat_calls_and_regenerate_is_explicit(env, ds):
    store, t = env
    ds.replies = [(200, chat(GOOD))]
    A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport)
    n = len(ds.requests)
    r2 = A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport)        # e.g. page reopened, clicked again
    assert len(ds.requests) == n and (r2["done"], r2["cached"]) == (0, 3)
    r3 = A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, regenerate=True, limit=1)
    assert r3["done"] == 1 and len(ds.requests) == n + 1
    r4 = A.enrich(store, t.task_id, api_key=KEY, model="deepseek-v4-pro", transport=ds.transport)   # other model
    assert r4["done"] == 3


def test_batch_limit_is_five(tmp_path, ds, monkeypatch):
    site = Site()
    entries = "".join(f"<item><title>新闻 {i}</title><link>https://n.example.com/{i}</link><guid>{i}</guid>"
                      f"<description>内容 {i}</description></item>" for i in range(8))
    site.put("/rss", f'<?xml version="1.0"?><rss><channel><title>x</title>{entries}</channel></rss>')
    store = C.CollectorStore(tmp_path / "c", allow_private_urls=True)
    t = store.create_task("批量", "", "", f"{site.base}/rss x")
    store.collect(t.task_id)
    ds.replies = [(200, chat(GOOD))]
    r = A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, limit=50)
    assert r["done"] == 5 and len(ds.requests) == 5 and r["remaining"] == 3
    site.httpd.shutdown()
    site.httpd.server_close()


def test_input_is_capped(env, ds):
    store, t = env
    it = store.items(t.task_id)[0].model_copy(update={"summary": "长" * 5000})
    msgs, truncated = A.build_messages(it, ["x"])
    assert truncated and len(msgs[1]["content"]) < A.MAX_INPUT_CHARS + 200


@pytest.mark.parametrize("reply,why", [
    ((200, chat("这不是 JSON")), "不是约定的 JSON"),
    ((200, chat("")), "空内容"),
    ((200, chat({"summary_zh": "x", "relevance": "很相关", "category": "产品发布", "reason": "r"})), "不是约定的 JSON"),
    ((200, chat({**GOOD, "valuation": "100 亿"})), "不是约定的 JSON"),                 # extra field: refused
    ((200, chat(GOOD, finish="length")), "截断"),
    ((200, {"no": "choices"}), "响应格式错误"),
    ((429, {"error": "rate"}), "429"),
    ((500, {"error": "boom"}), "HTTP 500"),
    ((401, {"error": "bad key"}), "鉴权失败"),
])
def test_failures_are_safe_and_never_touch_originals(env, ds, reply, why):
    store, t = env
    ds.replies = [reply]
    before = (store._dir(t.task_id) / "items.jsonl").read_bytes()
    r = A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, limit=1)
    assert r["done"] == 0 and why in r["failed"][0][1] and KEY not in r["failed"][0][1]
    assert A.results(store, t.task_id) == []
    assert (store._dir(t.task_id) / "items.jsonl").read_bytes() == before
    retried = reply[0] in (429, 500)
    assert len(ds.requests) == (2 if retried else 1)                                       # at most one retry


def test_retry_once_then_success(env, ds):
    store, t = env
    ds.replies = [(429, {"error": "rate"}), (200, chat(GOOD))]
    r = A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, limit=1)
    assert r["done"] == 1 and len(ds.requests) == 2


def test_timeout_is_safe(env):
    store, t = env

    def slow(url, headers, body, timeout):
        raise TimeoutError("timed out")
    r = A.enrich(store, t.task_id, api_key=KEY, transport=slow, limit=1)
    assert r["done"] == 0 and "超时" in r["failed"][0][1]


def test_key_precedence_and_never_logged(env, ds, monkeypatch):
    store, t = env
    monkeypatch.setenv(A.KEY_ENV, "sk-from-env-0002")
    assert A.resolve_key("sk-typed") == ("sk-typed", "本次会话输入")
    assert A.resolve_key("") == ("sk-from-env-0002", f"环境变量 {A.KEY_ENV}")
    ds.replies = [(200, chat(GOOD))]
    A.enrich(store, t.task_id, transport=ds.transport, limit=1)
    assert {k.lower(): v for k, v in ds.requests[0][1].items()}["authorization"] == "Bearer sk-from-env-0002"
    C.collect_all(store)
    blob = _all_text(store.root) + C.to_markdown(store.items(t.task_id), ai=A.latest(store, t.task_id))
    assert "sk-from-env-0002" not in blob and "sk-typed" not in blob


def test_exports_include_existing_ai_results_only(env, ds):
    store, t = env
    ds.replies = [(200, chat(GOOD))]
    A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, limit=1)
    n = len(ds.requests)
    ai = A.latest(store, t.task_id)
    csv_text = C.to_csv(store.items(t.task_id), ai=ai).decode("utf-8-sig")
    assert "AI 摘要" in csv_text and GOOD["summary_zh"] in csv_text and csv_text.count(GOOD["summary_zh"]) == 1
    assert "AI 生成，仅供信息整理" in C.to_markdown(store.items(t.task_id), ai=ai)
    assert len(ds.requests) == n                                                          # export made no call
    assert "AI 摘要" not in C.to_csv(store.items(t.task_id)).decode("utf-8-sig")         # default unchanged


def test_changed_content_is_not_shown_with_a_stale_summary(env, ds):
    store, t = env
    ds.replies = [(200, chat(GOOD))]
    A.enrich(store, t.task_id, api_key=KEY, transport=ds.transport, limit=1)
    rid = next(iter(A.latest(store, t.task_id)))
    f = A._file(store, t.task_id)
    rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()]
    rows[0]["fingerprint"] = "other"
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert rid not in A.latest(store, t.task_id)


def test_verify_without_key_is_not_run(monkeypatch, capsys):
    monkeypatch.delenv(A.KEY_ENV, raising=False)
    assert A.main(["verify"]) == 3
    assert "NOT RUN" in capsys.readouterr().out


def test_verify_protocol_with_mock(ds, monkeypatch, capsys):
    monkeypatch.setenv(A.KEY_ENV, KEY)
    ds.replies = [(200, chat(GOOD))]
    assert A.verify(transport=ds.transport) == 0
    out = capsys.readouterr().out
    assert KEY not in out and "OK" in out


def test_unknown_model_refused():
    with pytest.raises(A.AIError):
        A.DeepSeekClient(KEY, model="deepseek-chat")


def test_ai_module_is_separate_from_the_judgement_engine():
    src = (ROOT / "techsignal" / "collector_ai.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines() if ln.startswith(("from ", "import "))]
    assert not any(m in ln for ln in imports for m in ("providers", "assess", "rules", "pipeline"))
