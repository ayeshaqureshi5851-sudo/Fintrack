"""
FinTrack - app.py

Flask routes for the FinTrack student finance app.
Data lives in SQLite (fintrack.db). Calculations live in finance.py,
smart insights in insights.py and the rule-based assistant in assistant.py.

How to run:
    pip install flask
    python app.py
    open http://127.0.0.1:5000
"""

import calendar
import csv
import io
import math
import sqlite3
from datetime import date, datetime, timedelta
from itertools import groupby

from flask import (Flask, Response, flash, get_flashed_messages, jsonify, redirect,
                   render_template, request, session, url_for)

import assistant
import finance as fin
import insights as ins
import sample_data
from database import (CUSTOM_COLOURS, close_db, get_db, get_raw_setting, init_db, load_settings,
                      now_stamp, save_setting)
from formatting import fmt_date, fmt_date_long, fmt_time, inr, parse_date, plain_number

app = Flask(__name__)
# Needed only for flash messages / form drafts. This is a local project with no logins.
app.secret_key = "fintrack-local-project-key"
app.teardown_appcontext(close_db)
init_db()

MAX_AMOUNT = 10_000_000
PAGE_SIZE = 20

NAV_ITEMS = [
    ("dashboard", "Dashboard", "dashboard"),
    ("transactions", "Transactions", "transactions"),
    ("budget", "My Budget", "budget"),
    ("calendar_page", "Calendar", "calendar"),
    ("categories", "Categories", "categories"),
    ("ai_assistant", "Assistant", "ai"),
    ("settings_page", "Settings", "settings"),
]

# Jinja filters
app.jinja_env.filters["inr"] = inr
app.jinja_env.filters["plain"] = plain_number
app.jinja_env.filters["fdate"] = lambda s: fmt_date(s, date.today())
app.jinja_env.filters["fdate_long"] = fmt_date_long
app.jinja_env.filters["ftime"] = fmt_time


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def today_local():
    """The server's local date. FinTrack is a local app, so this is the student's date."""
    return date.today()


def safe_next(target, fallback):
    """Only allow redirects back to pages inside this app."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return fallback


def categories_for(db, kind):
    return [dict(r) for r in db.execute(
        "SELECT * FROM categories WHERE kind = ? ORDER BY is_custom, id", (kind,))]


def parse_amount(raw, allow_zero=False):
    """Return a float rounded to 2 decimals, or None when invalid."""
    try:
        value = float(str(raw).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value) or value < 0 or value > MAX_AMOUNT:
        return None
    if value == 0 and not allow_zero:
        return None
    return round(value, 2)


def validate_transaction(db, form, today):
    """Check a transaction form. Returns (clean_data, list_of_error_messages)."""
    errors = []
    kind = form.get("type", "")
    if kind not in ("expense", "income"):
        errors.append("Please choose whether this is an expense or income.")
        kind = "expense"

    amount = parse_amount(form.get("amount"))
    if amount is None:
        errors.append(f"Enter an amount greater than ₹0 (up to {inr(MAX_AMOUNT)}).")

    category = (form.get("category") or "").strip()
    custom = " ".join((form.get("custom_category") or "").split())
    new_category = None
    if category == "Other" and custom:
        if len(custom) > 24:
            errors.append("Custom category names can be up to 24 characters.")
        else:
            existing = db.execute(
                "SELECT name FROM categories WHERE kind = ? AND LOWER(name) = LOWER(?)",
                (kind, custom)).fetchone()
            if existing:
                category = existing["name"]
            else:
                category, new_category = custom, custom
    elif not db.execute("SELECT 1 FROM categories WHERE kind = ? AND name = ?", (kind, category)).fetchone():
        errors.append("Pick a category.")

    description = " ".join((form.get("description") or "").split())
    if len(description) > 60:
        errors.append("Keep the description under 60 characters.")

    tx_date = parse_date(form.get("tx_date"))
    if tx_date is None:
        errors.append("Choose a valid date.")
    elif tx_date > today + timedelta(days=1):
        errors.append("That date is in the future — pick today or an earlier date.")
    elif tx_date.year < 2000:
        errors.append("Choose a more recent date.")

    time_text = (form.get("tx_time") or "").strip()
    if not time_text:
        time_text = datetime.now().strftime("%H:%M")
    try:
        datetime.strptime(time_text, "%H:%M")
    except ValueError:
        errors.append("Enter the time like 14:30.")

    data = {
        "type": kind, "amount": amount, "category": category, "description": description,
        "tx_date": tx_date.isoformat() if tx_date else "", "tx_time": time_text,
        "new_category": new_category,
    }
    return data, errors


def ensure_category(db, kind, name):
    count = db.execute("SELECT COUNT(*) FROM categories WHERE is_custom = 1").fetchone()[0]
    db.execute(
        "INSERT OR IGNORE INTO categories (name, kind, icon, color, is_custom) VALUES (?, ?, '🏷️', ?, 1)",
        (name, kind, CUSTOM_COLOURS[count % len(CUSTOM_COLOURS)]))


def fail_form(errors, form, action_url, mode, next_url):
    """Keep what the student typed so the form can reopen with messages."""
    for message in errors:
        flash(message, "error")
    session["draft"] = {"values": {k: v for k, v in form.items()}, "action": action_url, "mode": mode}
    return redirect(next_url)


def budget_after_save_messages(db, settings, category, tx_date_text, before):
    """Toast only when a save pushes a budget across 80% or 100% (never repeats)."""
    d = parse_date(tx_date_text)
    if d is None or not settings["show_insights"]:
        return
    after = fin.budget_percent(db, category, d.year, d.month)
    if not after or before is None:
        return
    warn = settings["warn_threshold"]
    if before["pct"] <= 100 < after["pct"]:
        flash(f"You're now over your {category} budget by {inr(after['spent'] - after['limit'])}.", "warning")
    elif before["pct"] < warn <= after["pct"] <= 100:
        flash(f"You've used {int(after['pct'])}% of your {category} budget.", "warning")


def weekly_summary(db, today):
    start = today - timedelta(days=6)
    prev_start, prev_end = start - timedelta(days=7), start - timedelta(days=1)
    spent = fin.sum_range(db, "expense", start, today)
    prev = fin.sum_range(db, "expense", prev_start, prev_end)
    top = fin.category_breakdown(db, "expense", start, today)
    monday = today - timedelta(days=today.weekday())
    reviewed = db.execute("SELECT 1 FROM weekly_reviews WHERE week_start = ?", (monday.isoformat(),)).fetchone()
    return {
        "start": start, "spent": spent, "prev": prev, "change": fin.change_pct(spent, prev),
        "top": top[0] if top else None, "count": fin.count_range(db, "expense", start, today),
        "monday": monday.isoformat(), "reviewed": bool(reviewed),
    }


# ---------------------------------------------------------------------------
# Values available in every template
# ---------------------------------------------------------------------------
@app.context_processor
def inject_globals():
    db = get_db()
    today = today_local()
    settings = load_settings(db)
    active = ins.active_insights(db, settings, today)
    return {
        "settings": settings,
        "nav_items": NAV_ITEMS,
        "active_endpoint": request.endpoint,
        "nav_insights": active[:8],
        "nav_insight_count": len(active),
        "demo_count": db.execute("SELECT COUNT(*) FROM transactions WHERE is_demo = 1").fetchone()[0],
        "expense_cats": categories_for(db, "expense"),
        "income_cats": categories_for(db, "income"),
        "today_iso": today.isoformat(),
        "now_hhmm": datetime.now().strftime("%H:%M"),
        "draft": session.pop("draft", None),
        "undo_tx": session.pop("undo_tx", None),
        "quick_items": [dict(r) for r in db.execute("SELECT * FROM quick_items ORDER BY id")],
        "current_path": request.full_path.rstrip("?"),
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    db, today = get_db(), today_local()
    settings = load_settings(db)
    year, month = today.year, today.month
    first, last = fin.month_range(year, month)

    summary = fin.month_summary(db, year, month)
    pf, pe = fin.same_days_last_month(today)
    prev_income = fin.sum_range(db, "income", *fin.month_range(*fin.shift_month(year, month, -1)))
    prev_spent_same = fin.sum_range(db, "expense", pf, pe)

    total_tx = fin.count_range(db)
    recent = db.execute(
        """SELECT t.*, COALESCE(c.icon, '🏷️') AS icon, COALESCE(c.color, '#7B8782') AS color
           FROM transactions t LEFT JOIN categories c ON c.name = t.category AND c.kind = t.type
           ORDER BY t.tx_date DESC, t.tx_time DESC, t.id DESC LIMIT 5""").fetchall()

    goals = [g for g in fin.goal_rows(db, today) if not g["reached"]]
    goal = max(goals, key=lambda g: g["pct_raw"]) if goals else None
    insights_now = ins.active_insights(db, settings, today)
    top_cats = fin.category_breakdown(db, "expense", first, last)

    return render_template(
        "dashboard.html",
        balance=fin.current_balance(db, settings),
        summary=summary,
        income_change=fin.change_pct(summary["income"], prev_income) if prev_income else None,
        spent_change=fin.change_pct(summary["expense"], prev_spent_same) if prev_spent_same else None,
        total_tx=total_tx, recent=recent, series=fin.monthly_series(db, today),
        guide=fin.daily_guide(db, settings, today), goal=goal, goal_count=len(fin.goal_rows(db, today)),
        insights=insights_now[:2], more_insights=max(0, len(insights_now) - 2),
        top_cat=top_cats[0] if top_cats else None, month_name=fin.month_label(year, month),
        sample_possible=(total_tx == 0), month_tx=summary["count"],
        questions=["How much can I spend per day?", "Where can I reduce my spending?",
                   "Am I spending more than last month?"],
    )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
def resolve_range(preset, date_from, date_to, today):
    first, last = fin.month_range(today.year, today.month)
    if preset == "today":
        return today, today
    if preset == "week":
        return today - timedelta(days=6), today
    if preset == "month":
        return first, last
    if preset == "last_month":
        return fin.month_range(*fin.shift_month(today.year, today.month, -1))
    return parse_date(date_from), parse_date(date_to)


@app.route("/transactions")
def transactions():
    db, today = get_db(), today_local()
    args = request.args
    kind = args.get("type", "all")
    category = args.get("category", "")
    q = (args.get("q") or "").strip()[:60]
    preset = args.get("range", "")
    start, end = resolve_range(preset, args.get("from"), args.get("to"), today)

    where, params = ["1 = 1"], []
    if kind in ("income", "expense"):
        where.append("t.type = ?")
        params.append(kind)
    if category:
        where.append("t.category = ?")
        params.append(category)
    if start:
        where.append("t.tx_date >= ?")
        params.append(start.isoformat())
    if end:
        where.append("t.tx_date <= ?")
        params.append(end.isoformat())
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(t.description LIKE ? ESCAPE '\\' OR t.category LIKE ? ESCAPE '\\')")
        params += [like, like]
    where_sql = " AND ".join(where)

    totals = db.execute(
        f"""SELECT COALESCE(ROUND(SUM(CASE WHEN type='income' THEN amount END),2),0) AS income,
                   COALESCE(ROUND(SUM(CASE WHEN type='expense' THEN amount END),2),0) AS expense,
                   COUNT(*) AS n
            FROM transactions t WHERE {where_sql}""", params).fetchone()

    pages = max(1, math.ceil(totals["n"] / PAGE_SIZE))
    try:
        page = min(max(1, int(args.get("page", 1))), pages)
    except ValueError:
        page = 1

    rows = db.execute(
        f"""SELECT t.*, COALESCE(c.icon, '🏷️') AS icon, COALESCE(c.color, '#7B8782') AS color
            FROM transactions t LEFT JOIN categories c ON c.name = t.category AND c.kind = t.type
            WHERE {where_sql}
            ORDER BY t.tx_date DESC, t.tx_time DESC, t.id DESC LIMIT ? OFFSET ?""",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()

    groups = []
    for day, items in groupby(rows, key=lambda r: r["tx_date"]):
        items = list(items)
        net = sum(r["amount"] if r["type"] == "income" else -r["amount"] for r in items)
        groups.append({"date": day, "rows": items, "net": net})

    keep = {k: v for k, v in args.items() if k not in ("page", "range", "from", "to") and v}
    presets = []
    for key, label in [("", "All time"), ("today", "Today"), ("week", "Last 7 days"),
                       ("month", "This month"), ("last_month", "Last month")]:
        link = url_for("transactions", **(dict(keep, range=key) if key else keep))
        presets.append((key, label, link))

    filters_active = bool(kind in ("income", "expense") or category or q or start or end)
    base_args = {k: v for k, v in args.items() if k != "page" and v}
    return render_template(
        "transactions.html", groups=groups, totals=totals, page=page, pages=pages,
        filters_active=filters_active, base_args=base_args, presets=presets,
        f_kind=kind, f_category=category, f_q=q, f_preset=preset,
        f_from=args.get("from", ""), f_to=args.get("to", ""),
        all_tx=fin.count_range(db), all_categories=sorted({c["name"] for c in categories_for(db, "expense") + categories_for(db, "income")}),
    )


@app.post("/transactions/add")
def add_transaction():
    db, today = get_db(), today_local()
    settings = load_settings(db)
    next_url = safe_next(request.form.get("next"), url_for("transactions"))
    data, errors = validate_transaction(db, request.form, today)
    if errors:
        return fail_form(errors, request.form, url_for("add_transaction"), "add", next_url)

    streak_before, _ = fin.current_streak(db, today)
    before = None
    d = parse_date(data["tx_date"])
    if data["type"] == "expense":
        before = fin.budget_percent(db, data["category"], d.year, d.month)

    if data["new_category"]:
        ensure_category(db, data["type"], data["new_category"])
    db.execute(
        "INSERT INTO transactions (type, amount, category, description, tx_date, tx_time, is_demo, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (data["type"], data["amount"], data["category"], data["description"],
         data["tx_date"], data["tx_time"], now_stamp()))
    db.commit()

    word = "Expense" if data["type"] == "expense" else "Income"
    flash(f"{word} of {inr(data['amount'])} saved successfully!", "success")
    if data["type"] == "expense":
        budget_after_save_messages(db, settings, data["category"], data["tx_date"], before)
    streak_after, _ = fin.current_streak(db, today)
    if settings["show_insights"] and streak_before < 3 <= streak_after:
        flash("That's 3 days of tracking in a row — nicely done! 🔥", "success")
    return redirect(next_url)


@app.post("/quick/<int:qid>")
def quick_add(qid):
    """One tap on a Frequent item records an expense straight away."""
    db, today = get_db(), today_local()
    settings = load_settings(db)
    next_url = safe_next(request.form.get("next"), url_for("transactions"))
    item = db.execute("SELECT * FROM quick_items WHERE id = ?", (qid,)).fetchone()
    if not item:
        flash("That frequent item no longer exists.", "error")
        return redirect(next_url)

    # Use the date/time shown in the form (so you can log an earlier spend), else right now
    tx_date = parse_date(request.form.get("tx_date")) or today
    if tx_date > today + timedelta(days=1):
        tx_date = today
    tx_time = (request.form.get("tx_time") or "").strip()
    try:
        datetime.strptime(tx_time, "%H:%M")
    except ValueError:
        tx_time = datetime.now().strftime("%H:%M")

    before = fin.budget_percent(db, item["category"], tx_date.year, tx_date.month)
    cur = db.execute(
        "INSERT INTO transactions (type, amount, category, description, tx_date, tx_time, is_demo, created_at) "
        "VALUES ('expense', ?, ?, ?, ?, ?, 0, ?)",
        (item["amount"], item["category"], item["label"], tx_date.isoformat(), tx_time, now_stamp()))
    db.commit()
    session["undo_tx"] = cur.lastrowid
    flash(f"{item['label']} {inr(item['amount'])} added to {item['category']}.", "success")
    budget_after_save_messages(db, settings, item["category"], tx_date.isoformat(), before)
    return redirect(next_url)


@app.post("/quick/add")
def add_quick_item():
    db = get_db()
    label = " ".join((request.form.get("label") or "").split())
    amount = parse_amount(request.form.get("amount"))
    category = (request.form.get("category") or "").strip()
    icon = (request.form.get("icon") or "").strip()[:4] or "⚡"
    cat_ok = db.execute("SELECT 1 FROM categories WHERE kind='expense' AND name=?", (category,)).fetchone()
    if not label or len(label) > 20:
        flash("Give the frequent item a short name (up to 20 characters).", "error")
    elif amount is None:
        flash("Enter an amount greater than ₹0.", "error")
    elif not cat_ok:
        flash("Pick a category for the frequent item.", "error")
    elif db.execute("SELECT COUNT(*) FROM quick_items").fetchone()[0] >= 12:
        flash("You can keep up to 12 frequent items. Delete one to add another.", "error")
    else:
        db.execute("INSERT INTO quick_items (label, amount, category, icon) VALUES (?, ?, ?, ?)",
                   (label, amount, category, icon))
        db.commit()
        flash(f"Added {label} ({inr(amount)}) to your frequent items.", "success")
    return redirect(url_for("settings_page") + "#frequent")


@app.post("/quick/<int:qid>/delete")
def delete_quick_item(qid):
    db = get_db()
    db.execute("DELETE FROM quick_items WHERE id = ?", (qid,))
    db.commit()
    flash("Frequent item removed.", "success")
    return redirect(url_for("settings_page") + "#frequent")


@app.post("/transactions/<int:tid>/edit")
def edit_transaction(tid):
    db, today = get_db(), today_local()
    settings = load_settings(db)
    next_url = safe_next(request.form.get("next"), url_for("transactions"))
    if not db.execute("SELECT 1 FROM transactions WHERE id = ?", (tid,)).fetchone():
        flash("That transaction no longer exists.", "error")
        return redirect(next_url)
    data, errors = validate_transaction(db, request.form, today)
    if errors:
        return fail_form(errors, request.form, url_for("edit_transaction", tid=tid), "edit", next_url)

    before = None
    d = parse_date(data["tx_date"])
    if data["type"] == "expense":
        before = fin.budget_percent(db, data["category"], d.year, d.month)
    if data["new_category"]:
        ensure_category(db, data["type"], data["new_category"])
    db.execute(
        "UPDATE transactions SET type=?, amount=?, category=?, description=?, tx_date=?, tx_time=? WHERE id=?",
        (data["type"], data["amount"], data["category"], data["description"],
         data["tx_date"], data["tx_time"], tid))
    db.commit()
    flash("Transaction updated.", "success")
    if data["type"] == "expense":
        budget_after_save_messages(db, settings, data["category"], data["tx_date"], before)
    return redirect(next_url)


@app.post("/transactions/<int:tid>/delete")
def delete_transaction(tid):
    db = get_db()
    next_url = safe_next(request.form.get("next"), url_for("transactions"))
    cur = db.execute("DELETE FROM transactions WHERE id = ?", (tid,))
    db.commit()
    flash("Transaction deleted." if cur.rowcount else "That transaction was already deleted.",
          "success" if cur.rowcount else "info")
    return redirect(next_url)


# ---------------------------------------------------------------------------
# My Budget (category budgets + savings goals)
# ---------------------------------------------------------------------------
@app.route("/budget")
def budget():
    db, today = get_db(), today_local()
    settings = load_settings(db)
    year, month = fin.parse_month(request.args.get("month"), today)
    rows = fin.budget_rows(db, year, month, settings["warn_threshold"])
    first, last = fin.month_range(year, month)
    budgeted = {r["category"] for r in rows}
    unbudgeted = [c for c in categories_for(db, "expense") if c["name"] not in budgeted]
    py, pm = fin.shift_month(year, month, -1)
    ny, nm = fin.shift_month(year, month, 1)
    total_limit = sum(r["limit"] for r in rows)
    total_spent = sum(r["spent"] for r in rows)
    goals = fin.goal_rows(db, today)
    return render_template(
        "budget.html", rows=rows, unbudgeted=unbudgeted, month_label=fin.month_label(year, month),
        prev_month=fin.ym_key(py, pm), next_month=fin.ym_key(ny, nm),
        is_current=(year, month) == (today.year, today.month),
        can_next=(ny, nm) <= (today.year, today.month),
        total_limit=total_limit, total_spent=total_spent,
        total_remaining=round(total_limit - total_spent, 2),
        month_tx=fin.count_range(db, "expense", first, last), month_key=fin.ym_key(year, month),
        goals=goals, goals_total=sum(g["saved"] for g in goals),
        month_saved=fin.month_summary(db, today.year, today.month)["saved"],
    )


@app.post("/budget/save")
def save_budget():
    db = get_db()
    category = (request.form.get("category") or "").strip()
    limit_amount = parse_amount(request.form.get("limit"))
    month = request.form.get("month", "")
    back = url_for("budget", month=month) if month else url_for("budget")
    if not db.execute("SELECT 1 FROM categories WHERE kind='expense' AND name=?", (category,)).fetchone():
        flash("Pick a valid category for the budget.", "error")
    elif limit_amount is None:
        flash("Enter a budget limit greater than ₹0.", "error")
    else:
        db.execute(
            "INSERT INTO budgets (category, period, limit_amount) VALUES (?, 'monthly', ?) "
            "ON CONFLICT(category, period) DO UPDATE SET limit_amount = excluded.limit_amount",
            (category, limit_amount))
        db.commit()
        flash(f"{category} budget set to {inr(limit_amount)} a month.", "success")
    return redirect(back)


@app.post("/budget/remove")
def remove_budget():
    db = get_db()
    category = (request.form.get("category") or "").strip()
    db.execute("DELETE FROM budgets WHERE category = ? AND period = 'monthly'", (category,))
    db.commit()
    flash(f"Removed the {category} budget.", "success")
    month = request.form.get("month", "")
    return redirect(url_for("budget", month=month) if month else url_for("budget"))


def parse_goal_form(form, today):
    errors = []
    name = " ".join((form.get("name") or "").split())
    if not name or len(name) > 40:
        errors.append("Give your goal a name (up to 40 characters).")
    target = parse_amount(form.get("target"))
    if target is None:
        errors.append("Enter a target amount greater than ₹0.")
    saved = parse_amount(form.get("saved") or "0", allow_zero=True)
    if saved is None:
        errors.append("Enter how much you've already set aside (₹0 or more).")
    target_date = None
    if (form.get("target_date") or "").strip():
        d = parse_date(form.get("target_date"))
        if d is None or d < today:
            errors.append("The target date should be today or later.")
        else:
            target_date = d.isoformat()
    return {"name": name, "target": target, "saved": saved, "target_date": target_date}, errors


@app.post("/goals/add")
def add_goal():
    db, today = get_db(), today_local()
    data, errors = parse_goal_form(request.form, today)
    if errors:
        for e in errors:
            flash(e, "error")
    else:
        reached = now_stamp() if data["saved"] >= data["target"] else None
        db.execute(
            "INSERT INTO savings_goals (name, target_amount, saved_amount, target_date, created_at, reached_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (data["name"], data["target"], data["saved"], data["target_date"], now_stamp(), reached))
        db.commit()
        flash(f"Goal “{data['name']}” created. 🎯", "success")
    return redirect(url_for("budget") + "#goals")


@app.post("/goals/<int:gid>/edit")
def edit_goal(gid):
    db, today = get_db(), today_local()
    row = db.execute("SELECT * FROM savings_goals WHERE id = ?", (gid,)).fetchone()
    data, errors = parse_goal_form(request.form, today)
    if not row:
        flash("That goal no longer exists.", "error")
    elif errors:
        for e in errors:
            flash(e, "error")
    else:
        reached = (row["reached_at"] or now_stamp()) if data["saved"] >= data["target"] else None
        db.execute(
            "UPDATE savings_goals SET name=?, target_amount=?, saved_amount=?, target_date=?, reached_at=? WHERE id=?",
            (data["name"], data["target"], data["saved"], data["target_date"], reached, gid))
        db.commit()
        flash("Goal updated.", "success")
    return redirect(url_for("budget") + "#goals")


@app.post("/goals/<int:gid>/contribute")
def contribute_goal(gid):
    db = get_db()
    row = db.execute("SELECT * FROM savings_goals WHERE id = ?", (gid,)).fetchone()
    amount = parse_amount(request.form.get("amount"))
    mode = request.form.get("mode", "add")
    if not row:
        flash("That goal no longer exists.", "error")
    elif amount is None:
        flash("Enter an amount greater than ₹0.", "error")
    else:
        old_pct = row["saved_amount"] / row["target_amount"] * 100
        if mode == "withdraw":
            new_saved = max(0.0, round(row["saved_amount"] - amount, 2))
        else:
            new_saved = round(row["saved_amount"] + amount, 2)
        reached = (row["reached_at"] or now_stamp()) if new_saved >= row["target_amount"] else None
        db.execute("UPDATE savings_goals SET saved_amount = ?, reached_at = ? WHERE id = ?",
                   (new_saved, reached, gid))
        db.commit()
        new_pct = new_saved / row["target_amount"] * 100
        if mode == "withdraw":
            flash(f"Took {inr(amount)} out of “{row['name']}”.", "success")
        elif new_pct >= 100 > old_pct:
            flash(f"🏆 You reached your goal: {row['name']}!", "success")
        elif new_pct >= 75 > old_pct:
            flash(f"Almost there — “{row['name']}” is {int(new_pct)}% funded!", "success")
        elif new_pct >= 50 > old_pct:
            flash(f"Halfway to “{row['name']}” — you're getting closer!", "success")
        else:
            flash(f"Added {inr(amount)} to “{row['name']}”.", "success")
    return redirect(url_for("budget") + "#goals")


@app.post("/goals/<int:gid>/delete")
def delete_goal(gid):
    db = get_db()
    db.execute("DELETE FROM savings_goals WHERE id = ?", (gid,))
    db.commit()
    flash("Goal deleted.", "success")
    return redirect(url_for("budget") + "#goals")


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
@app.route("/calendar")
def calendar_page():
    db, today = get_db(), today_local()
    year, month = fin.parse_month(request.args.get("month"), today)
    selected = parse_date(request.args.get("date"))
    if selected and (selected.year, selected.month) != (year, month):
        selected = None
    if selected is None and (year, month) == (today.year, today.month):
        selected = today

    daily = fin.daily_totals(db, year, month)
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for d in week:
            info = daily.get(d.isoformat()) if d.month == month else None
            cells.append({"date": d, "iso": d.isoformat(), "in_month": d.month == month, "info": info,
                          "is_today": d == today, "is_selected": d == selected, "future": d > today})
        weeks.append(cells)

    day_rows, day_totals = [], None
    if selected:
        day_rows = db.execute(
            """SELECT t.*, COALESCE(c.icon, '🏷️') AS icon, COALESCE(c.color, '#7B8782') AS color
               FROM transactions t LEFT JOIN categories c ON c.name = t.category AND c.kind = t.type
               WHERE t.tx_date = ? ORDER BY t.tx_time DESC, t.id DESC""", (selected.isoformat(),)).fetchall()
        inc = sum(r["amount"] for r in day_rows if r["type"] == "income")
        exp = sum(r["amount"] for r in day_rows if r["type"] == "expense")
        day_totals = {"income": round(inc, 2), "expense": round(exp, 2), "net": round(inc - exp, 2)}

    py, pm = fin.shift_month(year, month, -1)
    ny, nm = fin.shift_month(year, month, 1)
    return render_template(
        "calendar.html", weeks=weeks, month_label=fin.month_label(year, month),
        prev_month=fin.ym_key(py, pm), next_month=fin.ym_key(ny, nm),
        selected=selected.isoformat() if selected else None, day_rows=day_rows, day_totals=day_totals,
        summary=fin.month_summary(db, year, month), month_key=fin.ym_key(year, month),
        is_current=(year, month) == (today.year, today.month),
        weekdays=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    )


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
@app.route("/categories")
def categories():
    db, today = get_db(), today_local()
    rng = request.args.get("range", "this_month")
    first, last = fin.month_range(today.year, today.month)
    if rng == "last_month":
        start, end = fin.month_range(*fin.shift_month(today.year, today.month, -1))
        label = "last month"
    elif rng == "last_30":
        start, end, label = today - timedelta(days=29), today, "the last 30 days"
    elif rng == "all":
        start, end, label = None, None, "all time"
    else:
        rng, start, end, label = "this_month", first, last, "this month"

    expense_rows = fin.category_breakdown(db, "expense", start, end)
    income_rows = fin.category_breakdown(db, "income", start, end)
    total_exp = sum(r["total"] for r in expense_rows)
    total_inc = sum(r["total"] for r in income_rows)
    biggest = db.execute(
        "SELECT description, category, amount, tx_date FROM transactions WHERE type='expense' "
        "AND (? IS NULL OR tx_date >= ?) AND (? IS NULL OR tx_date <= ?) ORDER BY amount DESC LIMIT 1",
        (start and start.isoformat(), start and start.isoformat(), end and end.isoformat(), end and end.isoformat()),
    ).fetchone()

    manage = [dict(r) for r in db.execute(
        """SELECT c.*, (SELECT COUNT(*) FROM transactions t WHERE t.category = c.name AND t.type = c.kind) AS uses
           FROM categories c ORDER BY c.kind DESC, c.is_custom, c.id""")]
    return render_template(
        "categories.html", rng=rng, label=label, expense_rows=expense_rows, income_rows=income_rows,
        total_exp=total_exp, total_inc=total_inc, net=round(total_inc - total_exp, 2), biggest=biggest,
        manage=manage, icons=["🏷️", "🎮", "🏋️", "🐶", "☕", "🧴", "💊", "📱", "🎁", "🚲", "✈️", "🎨"],
    )


@app.post("/categories/add")
def add_category():
    db = get_db()
    name = " ".join((request.form.get("name") or "").split())
    kind = request.form.get("kind", "expense")
    icon = (request.form.get("icon") or "🏷️")[:4]
    if kind not in ("expense", "income") or not name or len(name) > 24:
        flash("Give the category a name (up to 24 characters).", "error")
    elif db.execute("SELECT 1 FROM categories WHERE kind=? AND LOWER(name)=LOWER(?)", (kind, name)).fetchone():
        flash(f"You already have a {kind} category called {name}.", "error")
    else:
        count = db.execute("SELECT COUNT(*) FROM categories WHERE is_custom = 1").fetchone()[0]
        db.execute("INSERT INTO categories (name, kind, icon, color, is_custom) VALUES (?, ?, ?, ?, 1)",
                   (name, kind, icon, CUSTOM_COLOURS[count % len(CUSTOM_COLOURS)]))
        db.commit()
        flash(f"Added the category “{name}”.", "success")
    return redirect(url_for("categories") + "#manage")


@app.post("/categories/<int:cid>/delete")
def delete_category(cid):
    db = get_db()
    row = db.execute("SELECT * FROM categories WHERE id = ? AND is_custom = 1", (cid,)).fetchone()
    if not row:
        flash("Only categories you created can be deleted.", "error")
    elif db.execute("SELECT 1 FROM transactions WHERE category=? AND type=?", (row["name"], row["kind"])).fetchone():
        flash("That category is used by transactions, so it can't be deleted.", "error")
    else:
        db.execute("DELETE FROM categories WHERE id = ?", (cid,))
        db.execute("DELETE FROM budgets WHERE category = ?", (row["name"],))
        db.commit()
        flash("Category deleted.", "success")
    return redirect(url_for("categories") + "#manage")


# ---------------------------------------------------------------------------
# FinTrack AI (rule-based)
# ---------------------------------------------------------------------------
@app.route("/ai")
def ai_assistant():
    db, today = get_db(), today_local()
    settings = load_settings(db)
    return render_template(
        "ai_assistant.html", examples=assistant.EXAMPLES[:8],
        all_insights=ins.active_insights(db, settings, today),
        hidden_count=ins.dismissed_count(db, settings, today) if settings["show_insights"] else 0,
        milestones=ins.milestones(db, settings, today), week=weekly_summary(db, today),
        guide=fin.daily_guide(db, settings, today), initial_q=(request.args.get("q") or "")[:200],
        streak=fin.current_streak(db, today)[0], total_tx=fin.count_range(db),
    )


@app.post("/ai/ask")
def ai_ask():
    db = get_db()
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", ""))[:300]
    reply = assistant.answer(db, load_settings(db), question, today_local())
    return jsonify({"answer": reply})


@app.post("/ai/review")
def mark_review():
    db = get_db()
    monday = (today_local() - timedelta(days=today_local().weekday())).isoformat()
    db.execute("INSERT OR IGNORE INTO weekly_reviews (week_start, reviewed_at) VALUES (?, ?)", (monday, now_stamp()))
    db.commit()
    flash("Weekly review completed — nice habit! ✅", "success")
    return redirect(url_for("ai_assistant") + "#review")


@app.post("/insights/dismiss")
def dismiss_insight():
    db = get_db()
    key = (request.get_json(silent=True) or request.form or {}).get("key", "")
    if key:
        ins.dismiss(db, str(key))
        db.commit()
    return jsonify({"ok": True})


@app.post("/insights/restore")
def restore_insights():
    db = get_db()
    db.execute("DELETE FROM dismissed_insights")
    db.commit()
    flash("Dismissed insights are back.", "info")
    return redirect(url_for("ai_assistant"))


# ---------------------------------------------------------------------------
# Settings, sample data, export
# ---------------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    db = get_db()
    if request.method == "POST":
        f = request.form
        name = " ".join((f.get("display_name") or "").split())
        opening = parse_amount(f.get("opening_balance") or "0", allow_zero=True)
        spend_t = parse_amount(f.get("monthly_spend_target") or "0", allow_zero=True)
        save_t = parse_amount(f.get("monthly_savings_target") or "0", allow_zero=True)
        try:
            warn = int(f.get("warn_threshold", "80"))
        except ValueError:
            warn = 0
        errors = []
        if not name or len(name) > 30:
            errors.append("Enter your name (up to 30 characters).")
        if opening is None or spend_t is None or save_t is None:
            errors.append("Amounts must be numbers of ₹0 or more.")
        if not 50 <= warn <= 99:
            errors.append("The budget warning level must be between 50% and 99%.")
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            save_setting(db, "display_name", name)
            save_setting(db, "opening_balance", opening)
            save_setting(db, "monthly_spend_target", spend_t)
            save_setting(db, "monthly_savings_target", save_t)
            save_setting(db, "warn_threshold", warn)
            save_setting(db, "show_insights", "1" if f.get("show_insights") else "0")
            db.commit()
            flash("Settings saved.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", total_tx=fin.count_range(db),
                           sample_exists=sample_data.sample_exists(db))


@app.post("/sample/load")
def load_sample():
    db = get_db()
    if sample_data.load_sample(db, today_local()):
        flash("Sample data loaded. It's marked as sample so you can clear it any time.", "info")
    else:
        flash("Sample data can only be loaded when FinTrack has no transactions.", "error")
    return redirect(safe_next(request.form.get("next"), url_for("dashboard")))


@app.post("/sample/clear")
def clear_sample():
    removed = sample_data.clear_sample(get_db())
    flash(f"Removed {removed} sample transaction(s). Your own records were not touched.", "success")
    return redirect(safe_next(request.form.get("next"), url_for("dashboard")))


@app.route("/export.csv")
def export_csv():
    """
    Spreadsheet-friendly export. The date is written as text like "09 Oct 2026"
    (Excel shows "####" for real dates in a narrow column), with the weekday
    and time in their own columns. A BOM is added so Excel reads ₹ correctly.
    """
    db = get_db()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Day", "Time", "Type", "Category", "Description", "Amount (INR)", "Sample data"])
    for r in db.execute("SELECT * FROM transactions ORDER BY tx_date, tx_time, id"):
        d = parse_date(r["tx_date"])
        writer.writerow([
            f"{d.day:02d} {d.strftime('%b %Y')}" if d else r["tx_date"],
            d.strftime("%A") if d else "",
            fmt_time(r["tx_time"]),
            "Income" if r["type"] == "income" else "Expense",
            r["category"], r["description"], f"{r['amount']:.2f}", "yes" if r["is_demo"] else "no",
        ])
    stamp = today_local().strftime("%Y-%m-%d")
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=fintrack_transactions_{stamp}.csv"})


# ---------------------------------------------------------------------------
# Friendly errors (never show technical details to the student)
# ---------------------------------------------------------------------------
@app.errorhandler(sqlite3.Error)
def database_error(_error):
    try:
        get_db().rollback()
    except sqlite3.Error:
        pass
    flash("Something went wrong while saving. Please try again.", "error")
    return redirect(request.referrer or url_for("dashboard"))


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", title="Page not found",
                           message="That page doesn't exist. Use the sidebar to get back on track."), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template("error.html", title="Something went wrong",
                           message="Please go back and try again."), 500


def lan_address():
    """The address your phone can use on the same Wi-Fi (no data is sent anywhere)."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


if __name__ == "__main__":
    import sys
    if "--phone" in sys.argv:
        # Phone mode: reachable from other devices on your Wi-Fi.
        # Debug mode is OFF here because its error console must never be exposed on a network.
        print("\n  FinTrack is running for your phone.")
        print(f"  On your phone (same Wi-Fi) open:  http://{lan_address()}:5000")
        print("  There is no password, so use this only on a Wi-Fi network you trust.\n")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        app.run(debug=True)