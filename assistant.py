"""
FinTrack - assistant.py

FinTrack AI is a RULE-BASED assistant. It is NOT a language model.

How it works (all in plain Python):
    1. Lower-case the question and look for keywords  -> detect the INTENT
    2. Look for a time period (today / this week / last month ...) and a category
    3. Run a database query and fill the numbers into a ready-made sentence

It only ever reports amounts that exist in the database.
"""

import re
from datetime import timedelta

import finance as fin
import insights as ins
from formatting import fmt_date, inr

CATEGORY_WORDS = {
    "Food": ["food", "eat", "eating", "meal", "meals", "lunch", "dinner", "breakfast",
             "canteen", "snack", "snacks", "restaurant", "groceries", "grocery"],
    "Travel": ["travel", "transport", "auto", "uber", "ola", "rapido", "cab", "taxi",
               "bus", "metro", "commute", "ride", "rides", "fuel", "petrol"],
    "College & Study": ["study", "college", "stationery", "book", "books", "printing",
                        "notes", "education", "fees", "academic", "exam"],
    "Entertainment": ["entertainment", "movie", "movies", "netflix", "spotify", "fun",
                      "games", "gaming", "subscription", "subscriptions"],
    "Shopping": ["shopping", "shop", "clothes", "clothing", "online order"],
    "Bills": ["bill", "bills", "recharge", "rent", "electricity", "wifi"],
}

EXAMPLES = [
    "How much did I spend today?",
    "How much did I spend this month?",
    "How much money did I receive this month?",
    "What is my current balance?",
    "How much of my food budget is left?",
    "Which category did I spend the most on?",
    "Where can I reduce my spending?",
    "How much did I spend on travel this month?",
    "Am I spending more than last month?",
    "What can I do to save more money?",
    "How much can I spend per day?",
]


def _has(text, *words):
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _detect_category(db, text):
    custom = [r["name"] for r in db.execute(
        "SELECT name FROM categories WHERE is_custom = 1 AND kind = 'expense'")]
    for name in custom:
        if name.lower() in text:
            return name
    for name, words in CATEGORY_WORDS.items():
        if name.lower() in text or _has(text, *words):
            return name
    return None


def _detect_period(text, today):
    """Return (start, end, label)."""
    first, last = fin.month_range(today.year, today.month)
    if _has(text, "today"):
        return today, today, "today"
    if _has(text, "yesterday"):
        y = today - timedelta(days=1)
        return y, y, "yesterday"
    if "last month" in text or "previous month" in text:
        py, pm = fin.shift_month(today.year, today.month, -1)
        f, l = fin.month_range(py, pm)
        return f, l, f"in {fin.month_label(py, pm)}"
    if "this week" in text or _has(text, "week"):
        monday = today - timedelta(days=today.weekday())
        return monday, today, "this week"
    return first, last, "this month"


def answer(db, settings, question, today):
    text = re.sub(r"[^a-z0-9&' ]", " ", (question or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Ask me something about your money, for example: “How much did I spend today?”"

    total_tx = fin.count_range(db)
    first, last = fin.month_range(today.year, today.month)

    # --- greetings & help -----------------------------------------------------------------
    if _has(text, "hi", "hello", "hey") and len(text.split()) <= 3:
        return f"Hi {settings['display_name']}! I can answer questions about your spending, budgets and balance. Try one of the suggestions below."
    if _has(text, "help") or "what can you do" in text:
        return "I'm a simple rule-based assistant. I read your saved transactions and answer questions like:\n" + "\n".join(f"• {e}" for e in EXAMPLES[:6])

    if total_tx == 0 and not _has(text, "balance", "budget", "goal", "per day", "daily"):
        return ("You haven't recorded any transactions yet, so I have nothing to calculate from. "
                "Add your first expense or your allowance and ask me again!")

    category = _detect_category(db, text)
    start, end, label = _detect_period(text, today)

    # --- Money Left Today / daily guide --------------------------------------------------------
    if "left today" in text or "per day" in text or _has(text, "daily") or "can i spend" in text \
            or "safe to spend" in text or "money left" in text:
        g = fin.daily_guide(db, settings, today)
        if g["status"] == "insufficient":
            return g["missing"]
        if g["status"] == "tight":
            return (f"Based on {g['basis_text']}, there isn't any room left in this plan for the rest of the month. "
                    "That's just an estimate — you can raise your target in Settings or add money you've received.")
        left = g["left_today"]
        line = (f"You have about {inr(left)} left today" if left >= 0
                else f"You're about {inr(-left)} past today's guide")
        return (f"{line}.\nThe guide is {inr(g['guide'])} a day: {inr(g['pool'])} (from {g['basis_text']}) "
                f"spread over {g['days_left']} day(s). You've spent {inr(g['spent_today'])} today.\n"
                f"This is an estimate, not a limit. {g['assumption']}")

    # --- Budgets ------------------------------------------------------------------------------------
    if _has(text, "budget", "budgets"):
        rows = fin.budget_rows(db, today.year, today.month, settings["warn_threshold"])
        if not rows:
            return "You haven't set any budgets yet. Open My Budget to add one."
        if category:
            row = next((r for r in rows if r["category"] == category), None)
            if not row:
                return f"You don't have a budget for {category} yet. You can add one on My Budget."
            if row["state"] == "over":
                return (f"You've spent {inr(row['spent'])} on {category} this month, which is "
                        f"{inr(row['over'])} over your {inr(row['limit'])} budget.")
            return (f"You have {inr(row['remaining'])} left in your {category} budget this month "
                    f"({inr(row['spent'])} of {inr(row['limit'])} used, {row['pct']}%).")
        lines = []
        for r in rows:
            note = f"{inr(r['over'])} over" if r["state"] == "over" else f"{inr(r['remaining'])} left"
            lines.append(f"• {r['category']}: {inr(r['spent'])} of {inr(r['limit'])} ({note})")
        return "Here's where your budgets stand this month:\n" + "\n".join(lines)

    # --- Compare with last month ------------------------------------------------------------------
    if ("last month" in text or "previous month" in text) and \
            _has(text, "more", "less", "compare", "than", "higher", "lower", "vs", "versus") \
            or "spending more" in text or "spending less" in text:
        pf, pe = fin.same_days_last_month(today)
        prev_same = fin.sum_range(db, "expense", pf, pe)
        py, pm = fin.shift_month(today.year, today.month, -1)
        pfull = fin.sum_range(db, "expense", *fin.month_range(py, pm))
        cur = fin.sum_range(db, "expense", first, today)
        if pfull == 0:
            return (f"This month you've spent {inr(cur)}, but I don't have any expenses recorded for last month "
                    "to compare with.")
        diff = cur - prev_same
        direction = "more" if diff > 0 else "less"
        text_out = (f"So far this month (up to {fmt_date(today.isoformat(), today).lower()}) you've spent {inr(cur)}. "
                    f"In the same days last month you spent {inr(prev_same)}")
        if prev_same:
            text_out += f", so you're spending {inr(abs(diff))} {direction} ({abs(diff) / prev_same * 100:.0f}%)."
        else:
            text_out += "."
        text_out += f"\nLast month's full total was {inr(pfull)}."
        this_cats = {r["category"]: r["total"] for r in fin.category_breakdown(db, "expense", first, today)}
        prev_cats = {r["category"]: r["total"] for r in fin.category_breakdown(db, "expense", pf, pe)}
        changes = sorted(((c, this_cats.get(c, 0) - prev_cats.get(c, 0)) for c in set(this_cats) | set(prev_cats)),
                         key=lambda x: -abs(x[1]))
        if changes and abs(changes[0][1]) > 0:
            c, d = changes[0]
            text_out += f"\nBiggest change: {c}, {inr(abs(d))} {'higher' if d > 0 else 'lower'} than the same days last month."
        return text_out

    # --- Most spent category -------------------------------------------------------------------------------
    if _has(text, "most", "biggest", "highest", "top", "largest") and not _has(text, "income", "received"):
        rows = fin.category_breakdown(db, "expense", start, end)
        if not rows:
            return f"I don't see any expenses recorded for {label}."
        t = rows[0]
        extra = f" Next is {rows[1]['category']} at {inr(rows[1]['total'])}." if len(rows) > 1 else ""
        return (f"You spent the most on {t['category']} {label}: {inr(t['total'])} "
                f"({t['pct']:.0f}% of your {inr(sum(r['total'] for r in rows))} total).{extra}")

    # --- Reduce spending / save more -------------------------------------------------------------------------
    if _has(text, "reduce", "cut", "lower", "overspend", "overspending", "wasting", "waste") \
            or "spend less" in text or "cut down" in text:
        items = [i for i in ins.build_insights(db, settings, today) if i["kind"] in ("danger", "warning", "tip")][:3]
        if not items:
            return ("I don't see any strong patterns to flag right now. Keep recording your spending and I'll "
                    "point out anything that stands out.")
        out = ["Here's what the data suggests:"]
        for i in items:
            out.append(f"• {i['title']} — {i['what']} {i['next']}")
        return "\n".join(out)

    if "save" in text or "saving" in text or "savings" in text:
        if _has(text, "goal", "goals") and fin.goal_rows(db, today):
            pass  # fall through to goal answer below
        else:
            summary = fin.month_summary(db, today.year, today.month)
            tips = []
            if summary["income"]:
                tips.append(f"This month you've received {inr(summary['income'])} and spent "
                            f"{inr(summary['expense'])}, leaving {inr(summary['saved'])} unspent.")
            for i in [i for i in ins.build_insights(db, settings, today)
                      if i["kind"] in ("danger", "warning", "tip")][:2]:
                tips.append(f"{i['title']}: {i['next']}")
            tips.append("Set a savings goal on My Budget so unspent money has a purpose, and record spends "
                        "the same day so nothing slips by.")
            return "\n".join(f"• {t}" for t in tips)

    # --- Goals ----------------------------------------------------------------------------------------------------
    if _has(text, "goal", "goals"):
        goals = fin.goal_rows(db, today)
        if not goals:
            return "You don't have a savings goal yet. You can create one on My Budget."
        return "\n".join(f"• {g['name']}: {inr(g['saved'])} of {inr(g['target'])} ({g['pct']}%), {inr(g['remaining'])} to go"
                         for g in goals)

    # --- Balance -------------------------------------------------------------------------------------------------------
    if _has(text, "balance") or "how much money do i have" in text or "how much do i have" in text:
        bal = fin.current_balance(db, settings)
        return (f"Your current balance is {inr(bal)}: opening balance {inr(settings['opening_balance'])} "
                f"+ income {inr(fin.sum_range(db, 'income'))} − expenses {inr(fin.sum_range(db, 'expense'))}.")

    # --- Income ----------------------------------------------------------------------------------------------------------
    if _has(text, "receive", "received", "income", "earn", "earned", "allowance", "got"):
        total = fin.sum_range(db, "income", start, end)
        if total == 0:
            return f"I don't see any money received {label}."
        parts = ", ".join(f"{r['category']} {inr(r['total'])}" for r in fin.category_breakdown(db, "income", start, end))
        return f"You received {inr(total)} {label} ({parts})."

    # --- Amount saved / unspent ----------------------------------------------------------------------------------------------
    if _has(text, "saved", "unspent", "left") and not category:
        s = fin.month_summary(db, today.year, today.month)
        return (f"This month you've received {inr(s['income'])} and spent {inr(s['expense'])}, "
                f"so {inr(s['saved'])} is unspent.")

    # --- Spending totals -------------------------------------------------------------------------------------------------------
    if _has(text, "spend", "spent", "spending", "expense", "expenses", "cost", "paid", "pay"):
        total = fin.sum_range(db, "expense", start, end, category)
        what = f" on {category}" if category else ""
        if total == 0:
            return f"I don't see any spending{what} recorded {label}."
        count = fin.count_range(db, "expense", start, end) if not category else int(db.execute(
            "SELECT COUNT(*) FROM transactions WHERE type='expense' AND category=? AND tx_date>=? AND tx_date<=?",
            (category, start.isoformat(), end.isoformat())).fetchone()[0])
        reply = f"You spent {inr(total)}{what} {label}, across {count} transaction(s)."
        if not category:
            top = fin.category_breakdown(db, "expense", start, end)
            if top:
                reply += f" The biggest category was {top[0]['category']} ({inr(top[0]['total'])})."
        return reply

    # --- Category only ("travel?") --------------------------------------------------------------------------------------------------
    if category:
        total = fin.sum_range(db, "expense", start, end, category)
        return f"You spent {inr(total)} on {category} {label}." if total else f"I don't see any {category} spending {label}."

    return ("I'm not sure I understood that — I'm a simple rule-based assistant, so I only know certain kinds of questions. "
            "Try one of these:\n" + "\n".join(f"• {e}" for e in EXAMPLES[:7]))
