"""
FinTrack - finance.py

All money calculations live here, in Python, so every page uses the SAME rules.

THE FINANCIAL MODEL (kept simple and explicit)
----------------------------------------------
* Total Income      = sum of every income transaction
* Total Expenses    = sum of every expense transaction
* Current Balance   = Opening balance (Settings) + Total Income - Total Expenses
* Monthly Income    = income dated inside the chosen month
* Monthly Expenses  = expenses dated inside the chosen month
* Saved This Month  = Monthly Income - Monthly Expenses   (unspent money this month)
* Budget Remaining  = category limit - expenses in that category in that month
* Savings goals     = money you MANUALLY set aside; FinTrack never moves it for you.

"Saved This Month" is not the same as "Current Balance": the balance carries over
from earlier months, while "Saved This Month" only looks at the current month.
"""

import calendar
from datetime import date, timedelta
from math import floor

from formatting import inr


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def month_range(year, month):
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def parse_month(text, today):
    """'2026-10' -> (2026, 10). Falls back to the current month."""
    try:
        y, m = text.split("-")
        y, m = int(y), int(m)
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    except (AttributeError, ValueError):
        pass
    return today.year, today.month


def ym_key(year, month):
    return f"{year}-{month:02d}"


def month_label(year, month, short=False):
    d = date(year, month, 1)
    return d.strftime("%b %Y") if short else d.strftime("%B %Y")


def days_left_in_month(today):
    """Days remaining including today."""
    return calendar.monthrange(today.year, today.month)[1] - today.day + 1


# ---------------------------------------------------------------------------
# Basic sums
# ---------------------------------------------------------------------------
def sum_range(db, kind, start=None, end=None, category=None):
    sql = "SELECT COALESCE(ROUND(SUM(amount), 2), 0) FROM transactions WHERE type = ?"
    params = [kind]
    if start is not None:
        sql += " AND tx_date >= ?"
        params.append(start.isoformat())
    if end is not None:
        sql += " AND tx_date <= ?"
        params.append(end.isoformat())
    if category is not None:
        sql += " AND category = ?"
        params.append(category)
    return float(db.execute(sql, params).fetchone()[0])


def count_range(db, kind=None, start=None, end=None):
    sql = "SELECT COUNT(*) FROM transactions WHERE 1 = 1"
    params = []
    if kind:
        sql += " AND type = ?"
        params.append(kind)
    if start is not None:
        sql += " AND tx_date >= ?"
        params.append(start.isoformat())
    if end is not None:
        sql += " AND tx_date <= ?"
        params.append(end.isoformat())
    return int(db.execute(sql, params).fetchone()[0])


def current_balance(db, settings):
    return round(
        settings["opening_balance"] + sum_range(db, "income") - sum_range(db, "expense"), 2
    )


def change_pct(current, previous):
    """% change, or None when there is nothing to compare against."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def month_summary(db, year, month):
    first, last = month_range(year, month)
    income = sum_range(db, "income", first, last)
    expense = sum_range(db, "expense", first, last)
    return {
        "income": income,
        "expense": expense,
        "saved": round(income - expense, 2),
        "count": count_range(db, None, first, last),
    }


def same_days_last_month(today):
    """Range covering day 1..today.day of last month (capped to its length)."""
    py, pm = shift_month(today.year, today.month, -1)
    first, last = month_range(py, pm)
    end = date(py, pm, min(today.day, last.day))
    return first, end


# ---------------------------------------------------------------------------
# Category breakdowns
# ---------------------------------------------------------------------------
def category_breakdown(db, kind, start=None, end=None):
    sql = """
        SELECT t.category AS category,
               COALESCE(c.icon, '🏷️')    AS icon,
               COALESCE(c.color, '#7B8782') AS color,
               ROUND(SUM(t.amount), 2)     AS total,
               COUNT(*)                    AS count
        FROM transactions t
        LEFT JOIN categories c ON c.name = t.category AND c.kind = t.type
        WHERE t.type = ?
    """
    params = [kind]
    if start is not None:
        sql += " AND t.tx_date >= ?"
        params.append(start.isoformat())
    if end is not None:
        sql += " AND t.tx_date <= ?"
        params.append(end.isoformat())
    sql += " GROUP BY t.category ORDER BY total DESC"
    rows = [dict(r) for r in db.execute(sql, params)]
    grand = sum(r["total"] for r in rows)
    for r in rows:
        r["pct"] = round(r["total"] / grand * 100, 1) if grand else 0
        r["avg"] = round(r["total"] / r["count"], 2) if r["count"] else 0
    return rows


def monthly_series(db, today, n=6):
    labels, expenses, incomes = [], [], []
    for back in range(n - 1, -1, -1):
        y, m = shift_month(today.year, today.month, -back)
        first, last = month_range(y, m)
        labels.append(date(y, m, 1).strftime("%b"))
        expenses.append(sum_range(db, "expense", first, last))
        incomes.append(sum_range(db, "income", first, last))
    return {"labels": labels, "expenses": expenses, "incomes": incomes}


def daily_totals(db, year, month):
    """{'2026-10-09': {'income': 0, 'expense': 120, 'count': 2}} for one month."""
    first, last = month_range(year, month)
    rows = db.execute(
        """SELECT tx_date, type, ROUND(SUM(amount), 2) AS total, COUNT(*) AS n
           FROM transactions WHERE tx_date >= ? AND tx_date <= ?
           GROUP BY tx_date, type""",
        (first.isoformat(), last.isoformat()),
    )
    out = {}
    for r in rows:
        day = out.setdefault(r["tx_date"], {"income": 0.0, "expense": 0.0, "count": 0})
        day[r["type"]] = r["total"]
        day["count"] += r["n"]
    return out


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
def _budget_row(category, icon, color, limit_amount, spent, warn):
    pct_raw = spent / limit_amount * 100 if limit_amount else 0
    over = round(max(0.0, spent - limit_amount), 2)
    remaining = round(max(0.0, limit_amount - spent), 2)

    if spent > limit_amount:
        state, label = "over", "Over budget"
    elif spent == limit_amount:
        state, label = "warn", "Budget fully used"
    elif pct_raw >= warn:
        state, label = "warn", "Nearly there"
    else:
        state, label = "ok", "On track"

    # Never show 100% unless the budget is genuinely used up
    shown = floor(pct_raw + 0.5)
    if spent < limit_amount:
        shown = min(shown, 99)
    return {
        "category": category,
        "icon": icon,
        "color": color,
        "limit": round(limit_amount, 2),
        "spent": round(spent, 2),
        "remaining": remaining,
        "over": over,
        "pct_raw": round(pct_raw, 1),
        "pct": max(0, shown),
        "bar": max(0, min(100, pct_raw)),   # bar width is clamped to 0-100
        "state": state,
        "label": label,
    }


def budget_rows(db, year, month, warn=80):
    first, last = month_range(year, month)
    limits = db.execute(
        """SELECT b.category, b.limit_amount, COALESCE(c.icon, '🏷️') AS icon,
                  COALESCE(c.color, '#7B8782') AS color
           FROM budgets b
           LEFT JOIN categories c ON c.name = b.category AND c.kind = 'expense'
           WHERE b.period = 'monthly'
           ORDER BY COALESCE(c.id, 9999)"""
    ).fetchall()
    return [
        _budget_row(
            r["category"], r["icon"], r["color"], r["limit_amount"],
            sum_range(db, "expense", first, last, r["category"]), warn,
        )
        for r in limits
    ]


def budget_percent(db, category, year, month):
    """% of the budget used for one category/month, or None when it has no budget."""
    row = db.execute(
        "SELECT limit_amount FROM budgets WHERE category = ? AND period = 'monthly'", (category,)
    ).fetchone()
    if not row:
        return None
    first, last = month_range(year, month)
    spent = sum_range(db, "expense", first, last, category)
    return {"pct": spent / row["limit_amount"] * 100, "spent": spent, "limit": row["limit_amount"]}


# ---------------------------------------------------------------------------
# Logging streaks (used for gentle encouragement)
# ---------------------------------------------------------------------------
def logged_days(db):
    rows = db.execute("SELECT DISTINCT tx_date FROM transactions").fetchall()
    out = set()
    for r in rows:
        try:
            out.add(date.fromisoformat(r["tx_date"]))
        except ValueError:
            pass
    return out


def current_streak(db, today):
    """Consecutive days (ending today, or yesterday if today is empty) with a record."""
    days = logged_days(db)
    d = today if today in days else today - timedelta(days=1)
    count = 0
    start = None
    while d in days:
        count += 1
        start = d
        d -= timedelta(days=1)
    return count, start


def best_streak(db):
    days = sorted(logged_days(db))
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


# ---------------------------------------------------------------------------
# Savings goals
# ---------------------------------------------------------------------------
def goal_rows(db, today):
    rows = []
    for g in db.execute("SELECT * FROM savings_goals ORDER BY (reached_at IS NOT NULL), id"):
        target, saved = g["target_amount"], g["saved_amount"]
        pct_raw = saved / target * 100
        remaining = round(max(0.0, target - saved), 2)
        monthly_needed = None
        days_to_go = None
        if g["target_date"] and remaining > 0:
            try:
                td = date.fromisoformat(g["target_date"])
                days_to_go = (td - today).days
                if days_to_go > 0:
                    months = max(1, round(days_to_go / 30.4))
                    monthly_needed = round(remaining / months, 2)
            except ValueError:
                pass
        rows.append({
            "id": g["id"], "name": g["name"], "target": target, "saved": saved,
            "target_date": g["target_date"], "remaining": remaining,
            "pct": min(100, floor(pct_raw)) if saved < target else 100,
            "pct_raw": pct_raw, "reached": saved >= target,
            "monthly_needed": monthly_needed, "days_to_go": days_to_go,
        })
    return rows


# ---------------------------------------------------------------------------
# Money Left Today / Daily Spending Guide
# ---------------------------------------------------------------------------
def daily_guide(db, settings, today):
    """
    An ESTIMATE of how much you could spend per day for the rest of this month.

    1. If you set a monthly spending target, the pool is
          target - (spent this month before today)
    2. Otherwise the pool is your balance at the start of today
          (current balance + what you already spent today)
    guide per day = pool / days left in the month (including today)
    """
    days_left = days_left_in_month(today)
    first, _last = month_range(today.year, today.month)
    spent_today = sum_range(db, "expense", today, today)
    spent_month = sum_range(db, "expense", first, today)
    has_any = count_range(db) > 0

    if settings["monthly_spend_target"] > 0:
        basis = "target"
        basis_text = f"your monthly spending target of {inr(settings['monthly_spend_target'])}"
        pool = settings["monthly_spend_target"] - (spent_month - spent_today)
        assumption = "Assumes you stay within your target for the rest of the month."
    elif has_any or settings["opening_balance"] > 0:
        basis = "balance"
        basis_text = "your current balance"
        pool = current_balance(db, settings) + spent_today
        assumption = "Assumes no more money arrives this month — add it as income when it does."
    else:
        return {
            "status": "insufficient", "days_left": days_left,
            "missing": "Add your allowance as income (or set a monthly spending target in "
                       "Settings) and I can estimate how much you can spend each day.",
        }

    if pool <= 0:
        return {
            "status": "tight", "basis": basis, "basis_text": basis_text,
            "days_left": days_left, "pool": round(pool, 2), "spent_today": spent_today,
            "assumption": assumption,
        }

    guide = pool / days_left
    left_today = guide - spent_today
    return {
        "status": "ok", "basis": basis, "basis_text": basis_text, "days_left": days_left,
        "pool": round(pool, 2), "guide": round(guide, 2), "spent_today": spent_today,
        "left_today": round(left_today, 2),
        "used_pct": max(0, min(100, spent_today / guide * 100)) if guide else 0,
        "assumption": assumption,
    }
