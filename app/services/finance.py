"""
AgriMesh V4.0 — Finance Service
Track expenses, revenues, compute P&L per crop cycle.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FinanceEntry
from app.utils.time import utc_now


async def log_expense(
    db: AsyncSession,
    farmer_id: str,
    crop_cycle_id: str,
    category: str,
    amount: float,
    description: str = "",
) -> dict:
    """Log a farming expense."""
    import uuid
    entry = FinanceEntry(
        id=str(uuid.uuid4()),
        farmer_id=farmer_id,
        crop_cycle_id=crop_cycle_id,
        entry_type="expense",
        category=category,
        amount=amount,
        description=description,
        recorded_at=utc_now(),
    )
    db.add(entry)
    await db.commit()
    return {
        "entry_id": entry.id,
        "type": "expense",
        "category": category,
        "amount": amount,
        "recorded_at": entry.recorded_at.isoformat(),
    }


async def log_revenue(
    db: AsyncSession,
    farmer_id: str,
    crop_cycle_id: str,
    category: str,
    amount: float,
    description: str = "",
) -> dict:
    """Log farming revenue (harvest sale, etc.)."""
    import uuid
    entry = FinanceEntry(
        id=str(uuid.uuid4()),
        farmer_id=farmer_id,
        crop_cycle_id=crop_cycle_id,
        entry_type="revenue",
        category=category,
        amount=amount,
        description=description,
        recorded_at=utc_now(),
    )
    db.add(entry)
    await db.commit()
    return {
        "entry_id": entry.id,
        "type": "revenue",
        "category": category,
        "amount": amount,
        "recorded_at": entry.recorded_at.isoformat(),
    }


async def compute_pnl(
    db: AsyncSession,
    farmer_id: str,
    crop_cycle_id: str,
) -> dict:
    """
    Compute profit/loss for a crop cycle.
    Returns total expenses, total revenue, net P&L, and cost breakdown.
    """
    # Total expenses
    expense_result = await db.execute(
        select(
            FinanceEntry.category,
            func.sum(FinanceEntry.amount).label("total"),
        )
        .where(
            FinanceEntry.farmer_id == farmer_id,
            FinanceEntry.crop_cycle_id == crop_cycle_id,
            FinanceEntry.entry_type == "expense",
        )
        .group_by(FinanceEntry.category)
    )
    expenses_by_category = {
        row.category: float(row.total) for row in expense_result.all()
    }

    # Total revenue
    revenue_result = await db.execute(
        select(func.sum(FinanceEntry.amount))
        .where(
            FinanceEntry.farmer_id == farmer_id,
            FinanceEntry.crop_cycle_id == crop_cycle_id,
            FinanceEntry.entry_type == "revenue",
        )
    )
    total_revenue = float(revenue_result.scalar() or 0)

    total_expenses = sum(expenses_by_category.values())
    net_pnl = total_revenue - total_expenses

    # Count entries
    count_result = await db.execute(
        select(func.count(FinanceEntry.id))
        .where(
            FinanceEntry.farmer_id == farmer_id,
            FinanceEntry.crop_cycle_id == crop_cycle_id,
        )
    )
    entry_count = count_result.scalar()

    return {
        "crop_cycle_id": crop_cycle_id,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_pnl": net_pnl,
        "profit_margin_pct": round((net_pnl / total_revenue * 100), 1) if total_revenue > 0 else 0,
        "expenses_by_category": expenses_by_category,
        "entry_count": entry_count,
        "generated_at": utc_now().isoformat(),
    }
