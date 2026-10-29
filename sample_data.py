"""
FinTrack - sample_data.py

Optional demonstration data, loaded ONLY when the student clicks "Load sample data"
on an empty FinTrack. Every sample row is flagged is_demo = 1, so it can be removed
later without touching real records.

Dates are generated relative to today so the sample always looks current.
The current month adds up to: ₹8,000 received, ₹3,150 spent, ₹4,850 left.
"""

from datetime import date, timedelta

import finance as fin
from database import now_stamp

# (days_ago, time, category, description, amount) - current month expenses
CURRENT_EXPENSES = [
    (0, "13:10", "Food", "College Canteen", 90),
    (1, "13:25", "Food", "Canteen Lunch", 110),
    (2, "17:40", "Food", "Tea & Snacks", 70),
    (4, "19:05", "Food", "Fruits & Snacks", 180),
    (6, "20:30", "Food", "Dinner with Friends", 350),
    (8, "21:15", "Food", "Maggi & Groceries", 250),
    (10, "16:20", "Food", "Juice Centre", 150),
    (1, "08:20", "Travel", "Auto to College", 80),
    (3, "09:05", "Travel", "Uber", 180),
    (5, "08:35", "Travel", "Auto", 90),
    (7, "18:10", "Travel", "Rapido", 120),
    (9, "08:40", "Travel", "Auto to College", 80),
    (11, "10:00", "Travel", "Metro Card Recharge", 100),
    (6, "11:30", "College & Study", "Printing & Notes", 100),
    (8, "12:15", "College & Study", "Stationery", 250),
    (12, "15:00", "College & Study", "Lab Record Book", 50),
    (5, "19:45", "Entertainment", "Cafe with Friends", 300),
    (9, "09:00", "Entertainment", "Spotify Subscription", 119),
    (13, "20:50", "Entertainment", "Popcorn & Cold Drink", 81),
    (7, "17:15", "Shopping", "Phone Cover", 150),
    (10, "22:00", "Shopping", "Online Order (Accessories)", 250),
]

# (day_of_month, time, category, description, amount) - last month
PREVIOUS_EXPENSES = [
    (3, "13:00", "Food", "College Canteen", 450),
    (8, "20:00", "Food", "Dinner with Friends", 300),
    (14, "13:30", "Food", "Canteen Lunch", 200),
    (20, "18:45", "Food", "Groceries", 250),
    (26, "19:30", "Food", "Snacks", 150),
    (5, "08:30", "Travel", "Auto to College", 200),
    (15, "09:10", "Travel", "Uber", 150),
    (24, "08:45", "Travel", "Bus Pass", 250),
    (4, "11:00", "College & Study", "Semester Books", 300),
    (9, "09:00", "Entertainment", "Spotify Subscription", 119),
    (18, "19:15", "Entertainment", "Movie Ticket", 250),
    (22, "21:00", "Entertainment", "Popcorn & Cold Drink", 81),
    (12, "16:00", "Shopping", "Notebook Covers & Pens", 200),
]


def sample_exists(db):
    return db.execute("SELECT COUNT(*) FROM transactions WHERE is_demo = 1").fetchone()[0] > 0


def load_sample(db, today):
    """Insert sample data. Refuses to run if ANY transaction already exists."""
    if db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 0:
        return False

    stamp = now_stamp()
    month_start = date(today.year, today.month, 1)

    def add(kind, amount, category, description, day, time):
        db.execute(
            "INSERT INTO transactions (type, amount, category, description, tx_date, tx_time, is_demo, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
            (kind, amount, category, description, day.isoformat(), time, stamp),
        )

    add("income", 8000, "Allowance", "Monthly Allowance", month_start, "09:00")
    for days_ago, time, category, description, amount in CURRENT_EXPENSES:
        day = max(month_start, today - timedelta(days=days_ago))
        add("expense", amount, category, description, day, time)

    py, pm = fin.shift_month(today.year, today.month, -1)
    first, last = fin.month_range(py, pm)
    add("income", 8000, "Allowance", "Monthly Allowance", first, "09:00")
    for dom, time, category, description, amount in PREVIOUS_EXPENSES:
        add("expense", amount, category, description, date(py, pm, min(dom, last.day)), time)

    db.commit()
    return True


def clear_sample(db):
    cur = db.execute("DELETE FROM transactions WHERE is_demo = 1")
    db.commit()
    return cur.rowcount
