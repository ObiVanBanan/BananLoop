from bananloop.envfiles import env_keys_from_repo


def test_env_inventory_exposes_variable_names_but_never_values(tmp_path):
    (tmp_path / ".env").write_text(
        "# QDRANT_API_KEY=commented-value\n"
        "QDRANT_API_KEY=do-not-return-this\n"
        "export QDRANT_URL=https://qdrant.invalid\n"
        "invalid-key=ignored\n",
        encoding="utf-8",
    )

    found = env_keys_from_repo(tmp_path)

    assert found == ["QDRANT_API_KEY", "QDRANT_URL"]
    assert "do-not-return-this" not in repr(found)
    assert "https://qdrant.invalid" not in repr(found)


def test_missing_env_file_has_no_variable_inventory(tmp_path):
    assert env_keys_from_repo(tmp_path) == []
