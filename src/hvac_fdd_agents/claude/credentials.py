"""Find the Anthropic API key without ever letting it near git.

Order: ANTHROPIC_API_KEY in the environment, then the project's .env file, then
(first run only) a hidden prompt that saves the key to .env. .env is added to
.gitignore before the key is written, and the key is never printed or logged.
"""

import getpass
import os
import sys
from pathlib import Path

KEY = "ANTHROPIC_API_KEY"


def project_root(start: Path | None = None) -> Path:
    """The nearest directory at or above `start` that holds pyproject.toml."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / "pyproject.toml").exists():
            return d
    return here


def _read_env_file(env: Path) -> str | None:
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY:
            return value.strip().strip('"').strip("'") or None
    return None


def _ensure_gitignored(root: Path) -> None:
    gitignore = root / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if ".env" not in (line.strip() for line in lines):
        with gitignore.open("a", encoding="utf-8") as f:
            f.write("\n# Secrets: the API key lives here and must never be committed\n.env\n")


def ensure_api_key(root: Path | None = None, interactive: bool = True) -> None:
    """Make ANTHROPIC_API_KEY available to the SDK, prompting once if needed."""
    if os.environ.get(KEY):
        return
    root = root or project_root()
    env = root / ".env"
    key = _read_env_file(env)
    if key:
        os.environ[KEY] = key
        return
    if not (interactive and sys.stdin.isatty()):
        raise SystemExit(
            f"No API key found. Set {KEY}, or run this command once in a terminal to be "
            f"prompted for it (it is saved to {env}, which git ignores)."
        )
    _ensure_gitignored(root)
    print(f"No Anthropic API key found. It will be saved to {env} (git-ignored).")
    key = getpass.getpass("Paste your API key (input hidden): ").strip()
    if not key:
        raise SystemExit("No key entered.")
    if not key.startswith("sk-ant-"):
        print("Warning: that doesn't look like an Anthropic key (expected 'sk-ant-...'). Saving anyway.")
    with env.open("a", encoding="utf-8") as f:
        f.write(f"{KEY}={key}\n")
    try:
        env.chmod(0o600)
    except OSError:
        pass  # Windows ignores POSIX modes; the file is still git-ignored
    os.environ[KEY] = key
    print("Saved.")
