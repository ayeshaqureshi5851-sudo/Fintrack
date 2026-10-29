"""
FinTrack - insights.py

Rule-based savings insights. No AI model is involved: each insight is a plain
"if this pattern is in the data, then say this" rule that reads real transactions.

Every insight explains three things:
    what -> the pattern that was detected
    why  -> why it matters
    next -> something the student could do

Insights are only created when the data supports them.
"""

import re
from datetime import date, timedelta

import finance as fin
from database import now_stamp
from formatting import fmt_date, inr

RIDE_WORDS = re.compile(r"\b(auto|uber|ola|rapido|taxi|cab)\b", re.IGNORECASE)


def _insight(key, kind, priority, icon, title, what, why, nxt, endpoint="budget"):
    return {
        "key": key, "kind": kind, "priority": priority, "icon": icon,
        "title": title, "what": what, "why": why, "next": nxt, "endpoint": endpoint,
    }


def _expense_rows(db, start, end, category=None):
    sql = ("SELECT category, description, amount, tx_date FROM transactions "
           "WHERE type = 'expense' AND tx_date >= ? AND tx_date <= ?")
    params = [start.isoformat(), end.isoformat()]
    if category:
        sql += " AND category = ?"
        params.append(category)
    return db.execute(sql, params).fetchall()


def build_insights(db, settings, today):
    items = []
    year, month = today.year, today.month
    ym = fin.ym_key(year, month)
    first, last = fin.month_range(year, month)
    warn = settings["warn_threshold"]
    days_left = fin.days_left_in_month(today)
    summary = fin.month_summary(db, year, month)
    flagged_categories = set()

    # ---- 1 & 2. Budgets over / nearly used -------------------------------------
    for b in fin.budget_rows(db, year, month, warn):
        cat = b["category"]
        if b["state"] == "over":
            flagged_categories.add(cat)
            items.append(_insight(
                f"budget_over:{cat}:{ym}", "danger", 100, "🚨",
                f"{cat} budget exceeded",
                f"You've spent {inr(b['spent'])} on {cat} against a limit of {inr(b['limit'])} "
                f"— that's {inr(b['over'])} over.",
                "Going over in one area can leave less for the rest of the month.",
                f"With {days_left} day(s) left, you could hold off on non-essential {cat.lower()} "
                f"spending, or raise this limit on My Budget if it was set too low.",
            ))
        elif b["state"] == "warn":
            flagged_categories.add(cat)
            pace = ""
            if today.day >= 5:
                projected = b["spent"] / today.day * calendar_days(year, month)
                if projected > b["limit"]:
                    pace = (f" At your pace so far you'd reach about {inr(projected)} "
                            f"by month-end.")
            items.append(_insight(
                f"budget_warn:{cat}:{ym}", "warning", 80, "⚠️",
                f"You've used {b['pct']}% of your {cat} budget",
                f"{inr(b['spent'])} of {inr(b['limit'])} is spent, leaving {inr(b['remaining'])}.{pace}",
                "A little attention now helps you finish the month inside your budget.",
                f"Plan the remaining {inr(b['remaining'])} across the {days_left} day(s) left.",
            ))

    # ---- 3. Monthly spending target ---------------------------------------------
    target = settings["monthly_spend_target"]
    if target > 0:
        if summary["expense"] > target:
            items.append(_insight(
                f"target_over:{ym}", "danger", 98, "🎯", "Monthly spending target passed",
                f"You've spent {inr(summary['expense'])} this month; your target is {inr(target)}.",
                "Your target is the overall limit you chose for yourself this month.",
                "Look at the Categories page to see where the money went, then decide what can wait.",
                "categories",
            ))
        elif summary["expense"] >= target * warn / 100:
            items.append(_insight(
                f"target_warn:{ym}", "warning", 78, "🎯", "Close to your monthly target",
                f"{inr(summary['expense'])} of your {inr(target)} spending target is used.",
                "You have only a little room left for this month.",
                f"{inr(target - summary['expense'])} remains for {days_left} day(s).",
                "dashboard",
            ))

    # ---- 4. Repeated food spending ------------------------------------------------
    food_rows = _expense_rows(db, today - timedelta(days=20), today, "Food")
    food_days = {date.fromisoformat(r["tx_date"]) for r in food_rows}
    d = today if today in food_days else today - timedelta(days=1)
    run_days = []
    while d in food_days:
        run_days.append(d)
        d -= timedelta(days=1)
    recent_food = [r for r in food_rows if r["tx_date"] >= (today - timedelta(days=13)).isoformat()]

    if len(run_days) >= 3:
        run_total = sum(r["amount"] for r in food_rows if date.fromisoformat(r["tx_date"]) in run_days)
        avg_day = run_total / len(run_days)
        items.append(_insight(
            f"food_streak:{min(run_days).isoformat()}", "tip", 60, "🍱",
            f"Food purchases on {len(run_days)} days in a row",
            f"You recorded food spending on {len(run_days)} consecutive days, "
            f"{inr(run_total)} in total (about {inr(avg_day)} a day).",
            "Small daily food purchases add up quickly and are easy to overlook.",
            "Want to explore alternatives? Packing a meal or snack from the hostel or home "
            "even a couple of days a week could keep some of that money with you.",
            "categories",
        ))
    elif len(recent_food) >= 8:
        total = sum(r["amount"] for r in recent_food)
        avg = total / len(recent_food)
        items.append(_insight(
            f"food_frequent:{today.isocalendar()[0]}-W{today.isocalendar()[1]}", "tip", 58, "🍱",
            "Frequent food purchases lately",
            f"You've logged {len(recent_food)} food purchases in the last 14 days, "
            f"{inr(total)} in all (average {inr(avg)} each).",
            "Frequent small purchases are the easiest place to find savings without big sacrifices.",
            f"As an example using your own average: skipping just 2 purchases a week would keep "
            f"about {inr(avg * 2)} a week (before the cost of anything you make instead).",
            "categories",
        ))

    # ---- 5. Frequent auto / taxi rides -----------------------------------------------
    travel_rows = _expense_rows(db, today - timedelta(days=13), today, "Travel")
    rides = [r for r in travel_rows if RIDE_WORDS.search(r["description"] or "")]
    if len(rides) >= 4:
        total = sum(r["amount"] for r in rides)
        items.append(_insight(
            f"travel_rides:{today.isocalendar()[0]}-W{today.isocalendar()[1]}", "tip", 58, "🛺",
            "Frequent auto / cab rides",
            f"{len(rides)} auto, cab or taxi rides in the last 14 days cost {inr(total)} "
            f"(average {inr(total / len(rides))} a ride).",
            "Rides are convenient, but they can become your biggest travel cost.",
            "If a bus, metro or a shared ride works for some of your regular routes, compare the fare "
            "with what you pay now. (I don't know fares on your routes, so I can't estimate the saving.)",
            "categories",
        ))

    # ---- 6. Unusually high spending (needs history) --------------------------------------
    history = []
    for back in (1, 2, 3):
        y, m = fin.shift_month(year, month, -back)
        f, l = fin.month_range(y, m)
        if fin.count_range(db, None, f, l) >= 5:      # only count months that really have data
            history.append((f, l))
    if history:
        for row in fin.category_breakdown(db, "expense", first, today):
            cat = row["category"]
            if cat in flagged_categories:
                continue
            past = [fin.sum_range(db, "expense", f, l, cat) for f, l in history]
            baseline = sum(past) / len(past)
            if baseline >= 300 and row["total"] >= baseline * 1.25:
                above = (row["total"] / baseline - 1) * 100
                flagged_categories.add(cat)
                items.append(_insight(
                    f"unusual:{cat}:{ym}", "warning", 70, "📈",
                    f"{cat} spending is higher than usual",
                    f"{inr(row['total'])} so far this month — already {above:.0f}% above your usual "
                    f"full-month average of {inr(baseline)} (from {len(history)} earlier month(s)).",
                    "Spending that runs ahead of your normal pattern can squeeze the rest of the month.",
                    f"Check the {cat.lower()} entries: was something a one-off, or can something wait?",
                    "categories",
                ))

        # Overall comparison with the same days last month
        pf, pe = fin.same_days_last_month(today)
        prev_same = fin.sum_range(db, "expense", pf, pe)
        if prev_same >= 500 and summary["expense"] >= prev_same * 1.25:
            items.append(_insight(
                f"spend_up:{ym}", "warning", 55, "📊", "Spending is up on last month",
                f"You've spent {inr(summary['expense'])} so far, compared with {inr(prev_same)} "
                f"in the same days last month (+{(summary['expense'] / prev_same - 1) * 100:.0f}%).",
                "Noticing a rise early gives you time to adjust.",
                "Open Categories and compare where the extra spending came from.",
                "categories",
            ))

    # ---- 7. Biggest category share ------------------------------------------------------------
    if summary["expense"] >= 1000:
        top = fin.category_breakdown(db, "expense", first, last)
        if top and top[0]["pct"] >= 40 and top[0]["category"] not in ("Bills",):
            t = top[0]
            items.append(_insight(
                f"top_cat:{t['category']}:{ym}", "tip", 40, t["icon"],
                f"{t['category']} is your biggest spending area",
                f"{inr(t['total'])} — {t['pct']:.0f}% of this month's {inr(summary['expense'])}.",
                "The largest category is where small changes make the biggest difference.",
                "If it's mostly essentials, that's fine. If not, a budget for it can keep it in view.",
                "categories",
            ))

    # ---- 8. Positive messages -----------------------------------------------------------------
    sav_target = settings["monthly_savings_target"]
    if sav_target > 0 and summary["saved"] >= sav_target:
        items.append(_insight(
            f"save_target:{ym}", "positive", 50, "🌱", "Savings target reached",
            f"You've kept {inr(summary['saved'])} unspent this month — your target was {inr(sav_target)}.",
            "Hitting your own target is a real win.",
            "Consider setting some of it aside in a savings goal.", "budget",
        ))

    streak, start = fin.current_streak(db, today)
    if streak >= 3:
        items.append(_insight(
            f"streak:{start.isoformat()}", "positive", 45, "🔥", f"{streak} days of tracking in a row",
            f"You've recorded transactions on {streak} days in a row.",
            "Regular tracking is what makes every number here trustworthy.",
            "Keep going whenever it suits you — missing a day is completely fine.", "transactions",
        ))

    for g in fin.goal_rows(db, today):
        if g["reached"]:
            items.append(_insight(
                f"goal_done:{g['id']}", "positive", 52, "🏆", f"Goal reached: {g['name']}",
                f"You've set aside {inr(g['saved'])} for a target of {inr(g['target'])}.",
                "You planned for something and made it happen.",
                "Time to enjoy it — or start a new goal.", "budget",
            ))
        elif g["pct_raw"] >= 50:
            items.append(_insight(
                f"goal_half:{g['id']}", "positive", 42, "🎯", f"Halfway to {g['name']}",
                f"{inr(g['saved'])} saved of {inr(g['target'])} — {inr(g['remaining'])} to go.",
                "You're getting closer to your savings goal!",
                "Even a small top-up this week keeps the momentum.", "budget",
            ))

    if today.day <= 10:
        py, pm = fin.shift_month(year, month, -1)
        pf, pl = fin.month_range(py, pm)
        if fin.count_range(db, None, pf, pl) >= 5:
            kept = [b for b in fin.budget_rows(db, py, pm, warn) if b["spent"] > 0 and b["state"] != "over"]
            if kept:
                best = max(kept, key=lambda b: b["spent"])
                items.append(_insight(
                    f"budget_kept:{best['category']}:{fin.ym_key(py, pm)}", "positive", 35, "✅",
                    f"You stayed within your {best['category']} budget",
                    f"Last month you spent {inr(best['spent'])} of {inr(best['limit'])} on "
                    f"{best['category']}.",
                    "Sticking to a limit you chose yourself is a good habit.",
                    "Use last month as a guide for this month's limit.", "budget",
                ))

    # ---- 9. Gentle nudges ------------------------------------------------------------------------
    last_row = db.execute("SELECT MAX(tx_date) AS d FROM transactions").fetchone()
    if last_row and last_row["d"]:
        gap = (today - date.fromisoformat(last_row["d"])).days
        if gap >= 3:
            items.append(_insight(
                f"log_reminder:{today.isoformat()}", "info", 20, "📝", "Quick check-in",
                f"Your last record is from {fmt_date(last_row['d'], today)}.",
                "Adding recent spends keeps your balance and budgets accurate.",
                "Add what you remember — a rough amount is better than nothing.", "transactions",
            ))

    if fin.count_range(db) >= 5:
        has_budget = db.execute("SELECT COUNT(*) FROM budgets").fetchone()[0]
        if not has_budget:
            items.append(_insight(
                "no_budget", "tip", 30, "💡", "Try setting a budget",
                "You've been tracking spending but haven't set any category budgets.",
                "A budget turns your spending history into a plan.",
                "Pick one category, like Food, and set a monthly limit.", "budget",
            ))
        if not db.execute("SELECT COUNT(*) FROM savings_goals").fetchone()[0]:
            items.append(_insight(
                "no_goal", "tip", 28, "💡", "Save for something you want",
                "You don't have a savings goal yet.",
                "A goal gives unspent money a purpose.",
                "Add one on My Budget — headphones, a trip, an emergency fund.", "budget",
            ))

    items.sort(key=lambda i: -i["priority"])
    return items


def calendar_days(year, month):
    return fin.month_range(year, month)[1].day


def dismissed_keys(db):
    return {r["insight_key"] for r in db.execute("SELECT insight_key FROM dismissed_insights")}


def active_insights(db, settings, today):
    if not settings["show_insights"]:
        return []
    hidden = dismissed_keys(db)
    return [i for i in build_insights(db, settings, today) if i["key"] not in hidden]


def dismissed_count(db, settings, today):
    hidden = dismissed_keys(db)
    return sum(1 for i in build_insights(db, settings, today) if i["key"] in hidden)


def dismiss(db, key):
    db.execute(
        "INSERT OR IGNORE INTO dismissed_insights (insight_key, dismissed_at) VALUES (?, ?)",
        (key[:120], now_stamp()),
    )


# ---------------------------------------------------------------------------
# Gentle milestones (encouraging, never punishing)
# ---------------------------------------------------------------------------
def milestones(db, settings, today):
    total = fin.count_range(db)
    best = fin.best_streak(db)
    reviewed = db.execute("SELECT COUNT(*) FROM weekly_reviews").fetchone()[0]
    reached = db.execute("SELECT COUNT(*) FROM savings_goals WHERE reached_at IS NOT NULL").fetchone()[0]

    kept_text = None
    py, pm = fin.shift_month(today.year, today.month, -1)
    pf, pl = fin.month_range(py, pm)
    if fin.count_range(db, None, pf, pl) >= 5:
        kept = [b for b in fin.budget_rows(db, py, pm, settings["warn_threshold"])
                if b["spent"] > 0 and b["state"] != "over"]
        if kept:
            kept_text = f"Stayed within {kept[0]['category']} in {fin.month_label(py, pm)}"

    return [
        {"icon": "✍️", "title": "First transaction", "desc": "You started tracking.", "done": total > 0},
        {"icon": "🔥", "title": "3 days in a row", "desc": "Logged on three consecutive days.", "done": best >= 3},
        {"icon": "🗓️", "title": "Weekly review", "desc": "Completed a weekly spending review.", "done": reviewed > 0},
        {"icon": "✅", "title": "Within budget", "desc": kept_text or "Finish a month inside a category budget.", "done": bool(kept_text)},
        {"icon": "🏆", "title": "Goal reached", "desc": "Reached a savings goal.", "done": reached > 0},
    ]
