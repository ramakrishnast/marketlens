"""Ask-AI chat: tool-call schema validity and the tool loop (with a fake OpenAI client, no network)."""
import json
from types import SimpleNamespace

import pytest

from app.services import chat, chat_tools, llm

ALLOWED_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum", "description"}


def _walk(schema, path="$"):
    assert set(schema) <= ALLOWED_KEYS, f"{path}: unsupported keys {set(schema) - ALLOWED_KEYS}"
    if schema.get("type") == "object" or "properties" in schema:
        props = schema.get("properties", {})
        assert schema.get("additionalProperties") is False, f"{path}: strict mode needs additionalProperties=false"
        assert sorted(schema.get("required", [])) == sorted(props), f"{path}: strict mode requires every property"
        for k, v in props.items():
            _walk(v, f"{path}.{k}")
    if "items" in schema:
        _walk(schema["items"], f"{path}[]")


def test_tool_schemas_are_valid_openai_strict_functions():
    tools = chat_tools.openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert len(names) == len(set(names))
    for t in tools:
        assert t["type"] == "function"
        f = t["function"]
        assert f["name"].replace("_", "").isalnum() and len(f["name"]) <= 64
        assert len(f["description"]) > 20 and f["strict"] is True
        assert f["parameters"]["type"] == "object"
        _walk(f["parameters"], f["name"])
        json.dumps(t)  # serializable


def test_every_schema_property_is_a_function_argument():
    import inspect

    for name, spec in chat_tools.TOOLS.items():
        params = set(inspect.signature(spec["fn"]).parameters)
        props = set(spec["parameters"]["properties"])
        assert props <= params, f"{name}: schema has {props - params} not accepted by the function"


SAMPLE_ARGS = {
    "get_quote": {"tickers": ["AAPL"]}, "get_price_history": {"ticker": "AAPL", "period": "1m", "start": None, "end": None},
    "get_technicals": {"ticker": "AAPL"}, "get_fundamentals": {"ticker": "AAPL"}, "compare_companies": {"tickers": ["NVDA", "AMD"]},
    "get_news": {"ticker": "AAPL", "days": 7}, "get_filings": {"ticker": "AAPL", "forms": None},
    "screen_watchlist": {"condition": "above_sma200", "watchlist": None}, "get_watchlists": {}, "get_outlook": {"ticker": "AAPL"},
    "get_crowd": {"ticker": "NVDA"}, "get_congress_trades": {"ticker": "NVDA", "member": None}, "get_macro": {},
    "render_chart": {"tickers": ["AAPL", "MSFT"], "period": "3m", "normalize": True, "title": None},
    "render_table": {"title": "t", "columns": ["a", "b"], "rows": [["x", 1]]},
}


@pytest.mark.parametrize("name", list(chat_tools.TOOLS))
def test_each_tool_runs_on_sample_data(fresh_db, name):
    from app.services import watchlists

    watchlists.create("Demo", ["AAPL", "NVDA", "SPY"])
    res = chat_tools.call(name, SAMPLE_ARGS[name])
    assert "error" not in res, res.get("error")
    assert "sources" in res and "as_of" in res
    json.dumps(res, default=str)


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls, refusal=None))])


def _call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def test_chat_loop_executes_tools_and_returns_citations(fresh_db, monkeypatch):
    monkeypatch.setattr(llm, "status", lambda: {"enabled": True, "model": "gpt-6-luna", "message": None})
    seen = []

    def fake_create(messages, tools=None, **kw):
        seen.append(messages)
        if len(seen) == 1:
            assert tools and any(t["function"]["name"] == "get_quote" for t in tools)
            return _msg(tool_calls=[_call("c1", "get_quote", {"tickers": ["AAPL"]}),
                                    _call("c2", "render_chart", {"tickers": ["AAPL"], "period": "1m", "normalize": True, "title": None})])
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert {m["tool_call_id"] for m in tool_msgs} == {"c1", "c2"}
        assert "price" in tool_msgs[0]["content"]
        return _msg(content="AAPL closed at X ([Yahoo](https://finance.yahoo.com/quote/AAPL)).")

    monkeypatch.setattr(llm, "create", fake_create)
    out = chat.answer([], "How is AAPL doing?")
    assert out["answer"].startswith("AAPL")
    assert [t["name"] for t in out["tool_calls"]] == ["get_quote", "render_chart"]
    assert out["sources"] and out["as_of"]
    assert out["visuals"][0]["type"] == "chart" and out["visuals"][0]["series"][0]["data"]
    assert "never give buy/sell/hold" in seen[0][0]["content"]


def test_chat_without_key_returns_message(fresh_db):
    out = chat.answer([], "hi")
    assert out["needs_key"] is True and "OPENAI_API_KEY" in out["message"]


def test_research_desk_without_key(client):
    r = client.post("/api/ticker/AAPL/research").json()
    assert r["needs_key"] is True
