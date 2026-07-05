"""
Finance Tools — Stripe agentic wallet, invoicing, payment monitoring.

ALL operations require APPROVE tier. No financial action runs without the operator's explicit OK.

STUB — implement when Stripe agentic wallet is set up.
"""

from typing import Optional

from langchain_core.tools import tool

from src.tools.approval import auto, approve


# =============================================================================
# Stripe Wallet
# =============================================================================

@auto
@tool
async def wallet_balance() -> str:
    """Check Stripe wallet balance. [NOT YET IMPLEMENTED]"""
    return "Stripe wallet not yet configured. Set STRIPE_API_KEY to enable."


@auto
@tool
async def list_transactions(limit: int = 20) -> str:
    """List recent transactions. [NOT YET IMPLEMENTED]

    Args:
        limit: Number of transactions to show
    """
    return "Stripe wallet not yet configured."


@approve
@tool
async def create_invoice(customer_email: str, amount_cents: int,
                         description: str, currency: str = "usd") -> str:
    """Create a Stripe invoice. Requires approval. [NOT YET IMPLEMENTED]

    Args:
        customer_email: Customer's email
        amount_cents: Amount in cents (e.g. 5000 = $50.00)
        description: Invoice line item description
        currency: Currency code (default: usd)
    """
    return "Stripe wallet not yet configured."


@approve
@tool
async def make_payment(to: str, amount_cents: int, description: str,
                       currency: str = "usd") -> str:
    """Make a payment from the Stripe wallet. Requires approval. [NOT YET IMPLEMENTED]

    Args:
        to: Recipient (Stripe account ID or email)
        amount_cents: Amount in cents
        description: What this payment is for
        currency: Currency code
    """
    return "Stripe wallet not yet configured."


@approve
@tool
async def create_subscription(customer_email: str, price_id: str,
                               description: str = "") -> str:
    """Create a subscription. Requires approval. [NOT YET IMPLEMENTED]

    Args:
        customer_email: Customer's email
        price_id: Stripe price ID
        description: Subscription description
    """
    return "Stripe wallet not yet configured."


# =============================================================================
# Tool Registry
# =============================================================================

ALL_FINANCE_TOOLS = [
    wallet_balance, list_transactions,
    create_invoice, make_payment, create_subscription,
]
TOOLS = ALL_FINANCE_TOOLS  # alias for cove-core channels.py loader
