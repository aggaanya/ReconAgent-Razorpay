"""Finance Tools — controlled AI access to deterministic financial truth.

Layer position (docs/AI_TOOLS.md):

    Finance Intelligence Engine -> Structured Signals
        -> Finance Tools (this package) -> LangGraph -> LLM

Each tool wraps exactly one deterministic capability — a
``FinanceService`` method or the reconciliation engine — and returns that
engine's response verbatim inside a JSON-serializable envelope. Tools do
not calculate, aggregate, or transform financial values; they validate
typed inputs, enforce error/security boundaries, and guarantee LLM-safe
output.
"""

from .base import (
    FinanceEngineError,
    FinanceTool,
    FinanceToolError,
    InvalidToolInputError,
    UnknownToolError,
)
from .inputs import (
    DEFAULT_TOOL_CURRENCY,
    FinancialSummaryToolInput,
    PaymentPerformanceToolInput,
    RefundToolInput,
    SettlementToolInput,
    TrendToolInput,
    RevenueToolInput,
)
from .payments import PaymentPerformanceTool
from .reconciliation import ReconcileTransactionsTool, ReconcileToolInput
from .refunds import RefundTool
from .revenue import RevenueTool
from .settlements import SettlementTool
from .summary import FinancialSummaryTool
from .trends import TrendTool

# Shared singleton instances (stateless, so reuse is safe).
REVENUE_TOOL = RevenueTool()
PAYMENT_PERFORMANCE_TOOL = PaymentPerformanceTool()
REFUND_TOOL = RefundTool()
SETTLEMENT_TOOL = SettlementTool()
TREND_TOOL = TrendTool()
FINANCIAL_SUMMARY_TOOL = FinancialSummaryTool()
RECONCILE_TRANSACTION_TOOL = ReconcileTransactionsTool()

ALL_FINANCE_TOOLS: tuple[FinanceTool, ...] = (
    REVENUE_TOOL,
    PAYMENT_PERFORMANCE_TOOL,
    REFUND_TOOL,
    SETTLEMENT_TOOL,
    TREND_TOOL,
    FINANCIAL_SUMMARY_TOOL,
    RECONCILE_TRANSACTION_TOOL,
)

FINANCE_TOOLS: dict[str, FinanceTool] = {tool.name: tool for tool in ALL_FINANCE_TOOLS}


def get_finance_tool(name: str) -> FinanceTool:
    """Look up a registered tool by its stable name."""
    try:
        return FINANCE_TOOLS[name]
    except KeyError:
        raise UnknownToolError(
            f"Unknown finance tool {name!r}; available: "
            f"{', '.join(sorted(FINANCE_TOOLS))}",
            tool=name,
        ) from None


__all__ = [
    "ALL_FINANCE_TOOLS",
    "DEFAULT_TOOL_CURRENCY",
    "FINANCIAL_SUMMARY_TOOL",
    "FINANCE_TOOLS",
    "PAYMENT_PERFORMANCE_TOOL",
    "REFUND_TOOL",
    "RECONCILE_TRANSACTION_TOOL",
    "ReconcileToolInput",
    "ReconcileTransactionsTool",
    "RevenueToolInput",
    "PaymentPerformanceToolInput",
    "RefundToolInput",
    "SettlementToolInput",
    "TrendToolInput",
    "FinancialSummaryToolInput",
    "SETTLEMENT_TOOL",
    "REVENUE_TOOL",
    "TREND_TOOL",
    "FinanceEngineError",
    "FinanceTool",
    "FinanceToolError",
    "InvalidToolInputError",
    "UnknownToolError",
    "FinancialSummaryTool",
    "PaymentPerformanceTool",
    "RefundTool",
    "RevenueTool",
    "SettlementTool",
    "TrendTool",
    "get_finance_tool",
]
