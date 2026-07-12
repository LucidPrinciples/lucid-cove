"""Companion B — credential redaction (the 07-11 PAT leak: run_shell catted
~/.git-credentials and printed the push PAT into chat). Any tool that returns
command output or file contents masks known credential shapes first.
"""
from src.tools.system_tools import redact_credentials as R


def test_redacts_github_pat():
    s = "token is github_pat_11ABCDEFG0aQwErTyUiOp_abcdefghijklmnopqrstuvwxyz123456"
    out = R(s)
    assert "github_pat_" not in out
    assert "«REDACTED»" in out


def test_redacts_classic_gh_token():
    for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"):
        s = f"export GH={prefix}{'A'*36}"
        assert prefix + "A"*36 not in R(s)


def test_redacts_git_credentials_url():
    s = "https://cove-agent:ghp_SECRETTOKENvalue1234567890abcdef@github.com/LucidPrinciples/lucid-cove.git"
    out = R(s)
    assert "ghp_SECRETTOKENvalue1234567890abcdef" not in out
    assert "@github.com" in out           # structure preserved
    assert "cove-agent" in out            # username is not a secret


def test_redacts_token_assignment():
    assert "supersecret" not in R("GH_TOKEN=supersecret")
    assert "hunter2" not in R("export DATABASE_PASSWORD=hunter2")
    assert "k-abc123" not in R("MOONSHOT_API_KEY: k-abc123")


def test_redacts_authorization_header():
    out = R("Authorization: Bearer abc.def.ghi_token_value")
    assert "abc.def.ghi_token_value" not in out
    assert "Authorization" in out


def test_redacts_aws_key():
    assert "AKIAIOSFODNN7EXAMPLE" not in R("aws_key = AKIAIOSFODNN7EXAMPLE")


def test_preserves_key_name_for_readability():
    out = R("GH_TOKEN=ghp_" + "Z"*36)
    assert out.startswith("GH_TOKEN=")
    assert "«REDACTED»" in out


def test_leaves_ordinary_text_untouched():
    s = "SHELL [exit: 0]: ls -la\n\ntotal 8\ndrwxr-xr-x 2 root root 4096 config.py"
    assert R(s) == s


def test_empty_and_none_safe():
    assert R("") == ""
    assert R(None) is None


def test_idempotent():
    once = R("GH_TOKEN=ghp_" + "Q"*36)
    assert R(once) == once
