from bananloop.bootstrap import _missing_named_credentials


def test_missing_credentials_reported_without_values(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    needs = [
        "Provide OPENAI_API_KEY for embedding",
        "DEEPSEEK_API_KEY is required for inference",
    ]
    assert _missing_named_credentials(needs) == [
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ]
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    assert _missing_named_credentials(needs) == ["DEEPSEEK_API_KEY"]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "another-secret")
    assert _missing_named_credentials(needs) == []


def test_unrelated_operator_inputs_are_not_treated_as_credentials(monkeypatch):
    monkeypatch.delenv("EXAMPLE_API_KEY", raising=False)
    assert _missing_named_credentials([
        "Missing reference dataset and Python dependency",
        "No API keys were named explicitly",
    ]) == []
