# People can use whichever AI provider they have: a built-in name, any OpenAI-compatible
# address, or a local server without a key.
import pytest

from common import llm


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "_client", None)


def test_provider_name_fills_in_the_address(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "Groq")
    monkeypatch.setenv("LLM_API_KEY", "k")
    ep = llm.endpoint()
    assert ep["base_url"] == "https://api.groq.com/openai/v1" and ep["api_key"] == "k" and not ep["local"]


def test_own_address_wins_over_the_preset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://proxy.example.com/v1")
    assert llm.endpoint()["base_url"] == "https://proxy.example.com/v1"


def test_local_servers_need_no_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    ep = llm.endpoint()
    assert ep["local"] and ep["api_key"] and not llm.key_needed()
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    assert llm.endpoint()["local"]


def test_hosted_provider_without_key_is_reported(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert llm.key_needed()
    with pytest.raises(llm.LLMError, match="LLM_API_KEY"):
        llm._openai_client()


def test_unknown_provider_lists_the_choices(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "nope")
    with pytest.raises(llm.LLMError, match="openrouter"):
        llm.endpoint()


def test_azure_uses_its_own_client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "azure")
    monkeypatch.setenv("LLM_BASE_URL", "https://my-org.openai.azure.com")
    monkeypatch.setenv("LLM_API_KEY", "k")
    from openai import AzureOpenAI
    assert isinstance(llm._openai_client(), AzureOpenAI)
