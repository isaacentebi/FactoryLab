from factorylab.world.models import ModelRequest
from factorylab.world.openrouter import OpenRouterProvider


def test_reasoning_config_is_sent_verbatim_per_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []

    def transport(method, path, body):
        seen.append((method, path, body))
        return {
            "model": body["model"],
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.000001},
        }

    p = OpenRouterProvider(
        transport=transport,
        reasoning_config={"a/one": {"enabled": False}, "b/two": {"effort": "low"}},
    )
    p.complete(ModelRequest("a/one", "s", ({"role": "user", "content": "x"},), 10, "high"))
    p.complete(ModelRequest("b/two", "s", ({"role": "user", "content": "x"},), 10, "high"))
    p.complete(ModelRequest("c/three", "s", ({"role": "user", "content": "x"},), 10, "high"))
    assert seen[0][2]["reasoning"] == {"enabled": False}
    assert seen[1][2]["reasoning"] == {"effort": "low"}
    assert "reasoning" not in seen[2][2]


def test_reasoning_level_suffix_becomes_a_capability(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    seen = []

    def transport(method, path, body):
        seen.append(body)
        return {
            "model": body["model"],
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.000001},
        }

    p = OpenRouterProvider(transport=transport, reasoning_config={"a/one": {"effort": "low"}})
    p.complete(ModelRequest("a/one@high", "s", ({"role": "user", "content": "x"},), 10, "low"))
    p.complete(ModelRequest("a/one", "s", ({"role": "user", "content": "x"},), 10, "low"))
    p.complete(
        ModelRequest("a/one:online@none", "s", ({"role": "user", "content": "x"},), 10, "low")
    )
    assert seen[0]["model"] == "a/one" and seen[0]["reasoning"] == {"effort": "high"}
    assert seen[1]["model"] == "a/one" and seen[1]["reasoning"] == {"effort": "low"}
    assert seen[2]["model"] == "a/one:online" and seen[2]["reasoning"] == {"effort": "none"}
