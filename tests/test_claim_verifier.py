"""Tests for claim_verifier — #D50: fabricated-completion detector.

Verifies that agent messages claiming tool actions are cross-checked
against actual tool-call logs.
"""

import pytest
from unittest.mock import patch, AsyncMock

from src.tools.claim_verifier import extract_claims, verify_claims, check_and_flag


class TestExtractClaims:
    """Test claim extraction from agent messages."""

    def test_extract_git_commit_claim(self):
        """Detects 'I committed the changes' as a git_commit claim."""
        text = "I committed the changes to the branch."
        claims = extract_claims(text)
        assert len(claims) == 1
        assert claims[0]["claim_type"] == "git commit"
        assert claims[0]["tool_expected"] == "git_commit"

    def test_extract_git_push_claim(self):
        """Detects 'pushed to origin' as a git_push claim."""
        text = "I pushed the branch to origin."
        claims = extract_claims(text)
        assert any(c["claim_type"] == "git push" for c in claims)

    def test_extract_pr_claim(self):
        """Detects 'created a pull request' as PR claim."""
        text = "I created a pull request for the fix."
        claims = extract_claims(text)
        assert any(c["claim_type"] == "GitHub PR creation" for c in claims)

    def test_extract_tests_claim(self):
        """Detects 'tests pass' as run_tests claim."""
        text = "All tests pass now."
        claims = extract_claims(text)
        assert any(c["claim_type"] == "test run" for c in claims)

    def test_negated_claim_ignored(self):
        """Negated claims ('tests did not pass') are ignored."""
        text = "The tests did not pass."
        claims = extract_claims(text)
        assert len(claims) == 0

    def test_no_claims_in_plain_text(self):
        """Plain text without action verbs returns empty."""
        text = "Let me check the status of this repository."
        claims = extract_claims(text)
        assert len(claims) == 0

    def test_multiple_claims(self):
        """Multiple claims in one message are all extracted."""
        text = "I committed the fix, pushed to origin, and created a PR."
        claims = extract_claims(text)
        types = {c["claim_type"] for c in claims}
        assert "git commit" in types
        assert "git push" in types
        assert "GitHub PR creation" in types


class TestVerifyClaims:
    """Test claim verification against tool-call logs."""

    @pytest.mark.asyncio
    async def test_verified_claim_found(self):
        """Claim is verified when matching successful tool call exists."""
        with patch('src.tools.claim_verifier._recent_tool_calls') as mock_calls:
            mock_calls.return_value = [
                {
                    "tool_name": "git_commit",
                    "arguments": '{"project": "lucid-cove"}',
                    "result_preview": "OK",
                    "success": True,
                    "created_at": "2026-07-12T20:00:00Z",
                }
            ]

            results = await verify_claims(
                "I committed the changes.",
                agent_id="stuart",
            )

            assert len(results) == 1
            assert results[0]["verified"] is True
            assert "successful" in results[0]["detail"]

    @pytest.mark.asyncio
    async def test_unverified_claim_not_found(self):
        """Claim is flagged when no matching tool call exists."""
        with patch('src.tools.claim_verifier._recent_tool_calls') as mock_calls:
            mock_calls.return_value = []  # No tool calls

            results = await verify_claims(
                "I committed the changes.",
                agent_id="stuart",
            )

            assert len(results) == 1
            assert results[0]["verified"] is False
            assert "No git_commit tool calls found" in results[0]["detail"]

    @pytest.mark.asyncio
    async def test_failed_tool_call_detected(self):
        """Claim is flagged when tool calls exist but all failed."""
        with patch('src.tools.claim_verifier._recent_tool_calls') as mock_calls:
            mock_calls.return_value = [
                {
                    "tool_name": "git_push",
                    "arguments": '{"project": "lucid-cove"}',
                    "result_preview": "Error: authentication failed",
                    "success": False,
                    "created_at": "2026-07-12T20:00:00Z",
                }
            ]

            results = await verify_claims(
                "I pushed the branch.",
                agent_id="stuart",
            )

            assert len(results) == 1
            assert results[0]["verified"] is False
            assert "none succeeded" in results[0]["detail"]

    @pytest.mark.asyncio
    async def test_no_claims_returns_empty(self):
        """Plain text returns empty results."""
        results = await verify_claims(
            "Let me think about this.",
            agent_id="stuart",
        )
        assert len(results) == 0


class TestCheckAndFlag:
    """Test Attention card generation for fabrications."""

    @pytest.mark.asyncio
    async def test_fabrication_generates_card(self):
        """Unverified claims generate an Attention card."""
        with patch('src.tools.claim_verifier._recent_tool_calls') as mock_calls:
            mock_calls.return_value = []  # No tool calls = fabrication

            card = await check_and_flag(
                "I committed and pushed everything.",
                agent_id="stuart",
                channel="stuart-day",
            )

            assert card is not None
            assert card["severity"] == "warning"
            assert "stuart" in card["title"]
            assert len(card["claims"]) == 2  # commit + push

    @pytest.mark.asyncio
    async def test_verified_claim_no_card(self):
        """Verified claims don't generate a card."""
        with patch('src.tools.claim_verifier._recent_tool_calls') as mock_calls:
            mock_calls.return_value = [
                {
                    "tool_name": "git_commit",
                    "arguments": '{"project": "lucid-cove"}',
                    "result_preview": "OK",
                    "success": True,
                    "created_at": "2026-07-12T20:00:00Z",
                }
            ]

            card = await check_and_flag(
                "I committed the changes.",
                agent_id="stuart",
            )

            assert card is None

    @pytest.mark.asyncio
    async def test_no_claims_no_card(self):
        """Messages without claims don't generate a card."""
        card = await check_and_flag(
            "What do you think about this approach?",
            agent_id="stuart",
        )
        assert card is None
