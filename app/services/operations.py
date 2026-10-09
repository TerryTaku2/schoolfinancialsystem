"""Operator analytics for the platform console: revenue, schools, usage and health.

Revenue and school counts come from the platform database. Usage comes from each school's own
database (learners, active users, last activity, fees processed, attendance, library, storage).
Opening every school's database takes time as the number of schools grows, so the result is
cached for a few minutes; the console can ask for a fresh copy.
"""
import os
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, text

from .. import db
from ..models import (AuditLog, Attendance, Loan, Payment, Resource, School, Staff, Student, SubscriptionInvoice,
                      SubscriptionPayment, User)
from ..utils import money

CACHE_SECONDS = 600
_cache = {"at": 0, "data": None}
_lock = threading.Lock()


def _month(d):
    return f"{d.year}-{d.month:02d}"


def _last_months(n, today):
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def _school_stats(row):
    """Usage figures from one school's database."""
    from ..tenancy import use_school
    now, today = datetime.utcnow(), date.today()
    month_ago, week_ago = now - timedelta(days=30), now - timedelta(days=7)
    with use_school(row) as ref:
        q = db.session
        users_active = q.query(func.count(User.id)).filter(User.active.is_(True), User.last_login >= month_ago).scalar()
        users_week = q.query(func.count(User.id)).filter(User.active.is_(True), User.last_login >= week_ago).scalar()
        fees = q.query(Payment.currency, func.sum(Payment.amount_cents)).filter(
            Payment.void.is_(False), Payment.paid_on >= date(today.year, 1, 1)).group_by(Payment.currency).all()
        fees_30 = q.query(Payment.currency, func.sum(Payment.amount_cents)).filter(
            Payment.void.is_(False), Payment.paid_on >= today - timedelta(days=30)).group_by(Payment.currency).all()
        stats = {
            "learners": q.query(func.count(Student.id)).filter(Student.status == "active").scalar(),
            "staff": q.query(func.count(Staff.id)).filter(Staff.status != "left").scalar(),
            "users": q.query(func.count(User.id)).filter(User.active.is_(True)).scalar(),
            "users_active_30": users_active,
            "users_active_7": users_week,
            "last_login": q.query(func.max(User.last_login)).scalar(),
            "last_activity": q.query(func.max(AuditLog.timestamp)).scalar(),
            "payments_30": q.query(func.count(Payment.id)).filter(Payment.void.is_(False),
                                                                  Payment.paid_on >= today - timedelta(days=30)).scalar(),
            "fees_ytd": {c: v or 0 for c, v in fees},
            "fees_30": {c: v or 0 for c, v in fees_30},
            "attendance_days_30": q.query(func.count(func.distinct(Attendance.date))).filter(
                Attendance.date >= today - timedelta(days=30)).scalar(),
            "loans_30": q.query(func.count(Loan.id)).filter(Loan.issued_on >= today - timedelta(days=30)).scalar(),
            "resources": q.query(func.count(Resource.id)).scalar(),
            "resource_bytes": q.query(func.coalesce(func.sum(Resource.size_bytes), 0)).scalar(),
            # PDFs kept in a folder (UPLOADS_DIR) aren't part of the database size.
            "disk_bytes": q.query(func.coalesce(func.sum(Resource.size_bytes), 0)).filter(Resource.storage == "disk").scalar(),
            "db_bytes": _db_size(ref),
        }
    return stats


def _db_size(ref):
    try:
        if ref.database_url.startswith("sqlite:///"):
            path = ref.database_url[len("sqlite:///"):]
            return os.path.getsize(path) if os.path.exists(path) else 0
        if ref.db_schema:
            return db.session.execute(text(
                "SELECT COALESCE(SUM(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename))), 0) "
                "FROM pg_tables WHERE schemaname = :s"), {"s": ref.db_schema}).scalar() or 0
    except Exception:
        return None
    return None


def _health(st, created, today):
    """active / quiet / inactive / new, from the last time anyone did anything."""
    last = st.get("last_activity") or st.get("last_login")
    if last is None:
        return "new" if (today - created.date()).days <= 14 else "inactive"
    days = (today - last.date()).days
    return "active" if days <= 7 else "quiet" if days <= 30 else "inactive"


def build():
    from . import billing
    from ..api.platform import SETUP
    today = date.today()
    cfg = billing.settings()
    cur = cfg["currency"]

    # ---- platform data first (opening school databases resets the session) ----
    schools = [{"id": s.id, "slug": s.slug, "name": s.name, "type": s.school_type, "status": s.status,
                "free": bool(s.billing_free), "created": s.created_at, "rate": s.billing_rate_cents,
                "minimum": s.billing_minimum_cents, "preparing": s.slug in SETUP}
               for s in School.query.order_by(School.name)]
    invoices = [{"id": i.id, "school_id": i.school_id, "period": i.period, "issued_on": i.issued_on, "due_on": i.due_on,
                 "amount": i.amount_cents, "paid": i.paid_cents, "status": i.status, "number": i.number,
                 "pending": sum(1 for p in i.payments if p.status == "pending")}
                for i in SubscriptionInvoice.query.filter_by(void=False)]
    pays = [(p.paid_on, p.amount_cents) for p in SubscriptionPayment.query.filter_by(status="confirmed")]

    # ---- each school's own database ----
    stats = {}
    for s in schools:
        if s["preparing"]:
            continue
        try:
            stats[s["id"]] = _school_stats(db.session.get(School, s["id"]))
        except Exception as err:  # one broken school must not hide the rest
            stats[s["id"]] = {"error": str(err)[:200]}

    # ---- revenue ----
    year_inv = [i for i in invoices if i["issued_on"].year == today.year]
    billed_year = sum(i["amount"] for i in year_inv)
    collected_year = sum(a for d, a in pays if d.year == today.year)
    outstanding = sum(max(0, i["amount"] - i["paid"]) for i in invoices)
    aging = {"Not yet due": 0, "1–30 days late": 0, "31–60 days late": 0, "Over 60 days late": 0}
    for i in invoices:
        bal = max(0, i["amount"] - i["paid"])
        if not bal:
            continue
        late = (today - i["due_on"]).days
        key = "Not yet due" if late <= 0 else "1–30 days late" if late <= 30 else "31–60 days late" if late <= 60 else "Over 60 days late"
        aging[key] += bal
    months = _last_months(12, today)
    by_month = defaultdict(int)
    for d, a in pays:
        by_month[_month(d)] += a
    periods = defaultdict(lambda: {"billed": 0, "collected": 0, "first": None, "schools": 0})
    for i in invoices:
        p = periods[i["period"]]
        p["billed"] += i["amount"]
        p["collected"] += min(i["paid"], i["amount"])
        p["schools"] += 1
        p["first"] = min(p["first"] or i["issued_on"], i["issued_on"])
    recent_periods = sorted(periods.items(), key=lambda kv: kv[1]["first"])[-6:]

    # Next term if billed today: each active, paying school at its current learner count.
    forecast = 0
    for s in schools:
        st = stats.get(s["id"], {})
        if s["status"] != "active" or s["free"] or "learners" not in st:
            continue
        rate = s["rate"] if s["rate"] is not None else cfg["default_rate_cents"]
        minimum = s["minimum"] if s["minimum"] is not None else cfg["default_minimum_cents"]
        forecast += max(st["learners"] * rate, minimum)

    # ---- schools ----
    by_type = defaultdict(int)
    for s in schools:
        by_type[s["type"]] += 1
    growth, running = [], sum(1 for s in schools if _month(s["created"]) < months[0])
    for m in months:
        running += sum(1 for s in schools if _month(s["created"]) == m)
        growth.append(running)

    inv_by_school = defaultdict(list)
    for i in invoices:
        inv_by_school[i["school_id"]].append(i)
    rows, fees_total, fees_30_total = [], defaultdict(int), defaultdict(int)
    totals = defaultdict(int)
    for s in schools:
        st = stats.get(s["id"], {})
        open_inv = [i for i in inv_by_school[s["id"]] if i["status"] != "paid"]
        sub_state = ("free" if s["free"] else "overdue" if any(i["status"] == "overdue" for i in open_inv)
                     else "due" if open_inv else "paid up" if inv_by_school[s["id"]] else "not billed")
        for c, v in (st.get("fees_ytd") or {}).items():
            fees_total[c] += v
        for c, v in (st.get("fees_30") or {}).items():
            fees_30_total[c] += v
        for k in ("learners", "staff", "users", "users_active_30", "users_active_7", "resources", "resource_bytes", "loans_30"):
            totals[k] += st.get(k) or 0
        totals["db_bytes"] += (st.get("db_bytes") or 0) + (st.get("disk_bytes") or 0)
        health = ("setting up" if s["preparing"] else "suspended" if s["status"] != "active"
                  else "error" if "error" in st else _health(st, s["created"], today))
        last = st.get("last_activity") or st.get("last_login")
        rows.append({
            "id": s["id"], "name": s["name"], "slug": s["slug"], "type": s["type"], "status": s["status"],
            "health": health, "learners": st.get("learners"), "staff": st.get("staff"),
            "users_active_30": st.get("users_active_30"), "users": st.get("users"),
            "last_activity": last.isoformat() if last else None,
            "payments_30": st.get("payments_30"), "attendance_days_30": st.get("attendance_days_30"),
            "fees_ytd": [{"currency": c, "amount": money(v)} for c, v in (st.get("fees_ytd") or {}).items() if v],
            "loans_30": st.get("loans_30"), "resources": st.get("resources"),
            "storage_bytes": (st.get("db_bytes") or 0) + (st.get("disk_bytes") or 0),
            "subscription": sub_state,
            "balance": money(sum(max(0, i["amount"] - i["paid"]) for i in open_inv)),
            "error": st.get("error"),
        })

    # ---- what needs attention ----
    attention = []
    for i in sorted(invoices, key=lambda x: x["due_on"]):
        name = next((s["name"] for s in schools if s["id"] == i["school_id"]), "?")
        if i["pending"]:
            attention.append({"kind": "confirm", "text": f"{name}: {i['pending']} EcoCash payment(s) to confirm ({i['number']})"})
        if i["status"] == "overdue":
            attention.append({"kind": "overdue", "text": f"{name}: {cur} {money(i['amount'] - i['paid']):,.2f} overdue "
                                                         f"{(today - i['due_on']).days} days ({i['number']}, {i['period']})"})
    for r in rows:
        if r["health"] == "inactive" and r["status"] == "active":
            attention.append({"kind": "inactive", "text": f"{r['name']}: no activity for over 30 days"})
        if r["health"] == "error":
            attention.append({"kind": "error", "text": f"{r['name']}: its database couldn't be read ({r['error']})"})
    order = {"error": 0, "confirm": 1, "overdue": 2, "inactive": 3}
    attention.sort(key=lambda a: order[a["kind"]])

    active = [s for s in schools if s["status"] == "active" and not s["preparing"]]
    health_counts = defaultdict(int)
    for r in rows:
        health_counts[r["health"]] += 1
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "currency": cur,
        "kpi": {
            "collected_year": money(collected_year),
            "collected_30": money(sum(a for d, a in pays if d >= today - timedelta(days=30))),
            "billed_year": money(billed_year),
            "collection_rate": round(min(collected_year, billed_year) / billed_year * 100, 1) if billed_year else None,
            "outstanding": money(outstanding),
            "overdue": money(sum(v for k, v in aging.items() if k != "Not yet due")),
            "overdue_schools": len({i["school_id"] for i in invoices if i["status"] == "overdue"}),
            "forecast_next_term": money(forecast),
            "schools_total": len(schools), "schools_active": len(active),
            "schools_suspended": sum(1 for s in schools if s["status"] != "active" and not s["preparing"]),
            "schools_free": sum(1 for s in schools if s["free"]),
            "learners": totals["learners"], "staff": totals["staff"],
            "users": totals["users"], "users_active_30": totals["users_active_30"], "users_active_7": totals["users_active_7"],
            "fees_processed_ytd": [{"currency": c, "amount": money(v)} for c, v in sorted(fees_total.items()) if v],
            "fees_processed_30": [{"currency": c, "amount": money(v)} for c, v in sorted(fees_30_total.items()) if v],
            "storage_bytes": totals["db_bytes"], "resources": totals["resources"], "loans_30": totals["loans_30"],
        },
        "health": dict(health_counts),
        "revenue_by_month": {"labels": months, "values": [money(by_month[m]) for m in months]},
        "periods": [{"period": k, "billed": money(v["billed"]), "collected": money(v["collected"]), "schools": v["schools"]}
                    for k, v in recent_periods],
        "aging": [{"label": k, "value": money(v)} for k, v in aging.items()],
        "growth": {"labels": months, "values": growth},
        "by_type": [{"label": k, "value": v} for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
        "schools": rows,
        "attention": attention[:30],
    }


def dashboard(refresh=False):
    with _lock:
        if refresh or not _cache["data"] or time.time() - _cache["at"] > CACHE_SECONDS:
            _cache["data"], _cache["at"] = build(), time.time()
        return _cache["data"]
