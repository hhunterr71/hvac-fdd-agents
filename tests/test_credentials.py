import os

import pytest

from hvac_fdd_agents.claude import credentials


@pytest.fixture(autouse=True)
def no_key_in_env(monkeypatch):
    monkeypatch.delenv(credentials.KEY, raising=False)


def test_reads_key_from_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OTHER=1\nANTHROPIC_API_KEY=sk-ant-test\n")
    credentials.ensure_api_key(tmp_path, interactive=False)
    assert os.environ[credentials.KEY] == "sk-ant-test"


def test_non_interactive_without_key_fails_without_creating_files(tmp_path):
    with pytest.raises(SystemExit):
        credentials.ensure_api_key(tmp_path, interactive=False)
    assert not (tmp_path / ".env").exists()


def test_first_run_prompt_gitignores_before_saving(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text("data/\n")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda prompt: "sk-ant-new")
    credentials.ensure_api_key(tmp_path)
    assert ".env" in (tmp_path / ".gitignore").read_text().splitlines()
    assert "ANTHROPIC_API_KEY=sk-ant-new" in (tmp_path / ".env").read_text()
    assert os.environ[credentials.KEY] == "sk-ant-new"
