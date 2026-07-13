from src.tools.approval import _result_is_success
from src.utils.watcher import looks_like_error


def test_failed_push_is_not_success():
    s = ("FAILED: Push reported success but branch 'x' not found on origin.\n"
         "Likely cause: no push credentials configured.")
    assert looks_like_error(s) is True
    assert _result_is_success(s) is False


def test_pr_error_is_not_success():
    s = "Error: no GitHub credentials found for this clone (remote URL, GH_TOKEN)."
    assert _result_is_success(s) is False


def test_refused_is_not_success():
    assert _result_is_success("REFUSED: No unpushed commits. Stage and commit first.") is False


def test_pr_created_json_is_success():
    s = ('{"status": "created", "pr_number": 42, '
         '"pr_url": "https://github.com/LucidPrinciples/lucid-cove/pull/42", '
         '"additions": 10, "deletions": 2, '
         '"message": "PR CREATED: #42 https://github.com/LucidPrinciples/lucid-cove/pull/42"}')
    assert _result_is_success(s) is True


def test_plain_push_output_is_success():
    s = ("To github.com:LucidPrinciples/lucid-cove.git\n"
         " * [new branch]  fix/x -> fix/x")
    assert _result_is_success(s) is True


def test_empty_result_is_success():
    assert _result_is_success("") is True
