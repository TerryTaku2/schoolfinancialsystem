"""Payroll: staff pay setup, monthly runs, payslips, statutory remittances and ZIMRA returns."""
import json
from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import db
from ..models import PAY_ITEM_TYPES, Account, Payslip, PayrollRun, Staff, StaffPayItem, TaxTable
from ..services import currency as fx
from ..services import payroll as svc
from ..services.assets import month_start, parse_period
from ..utils import (ApiError, audit, body, clean_str, get_or_404, money, parse_date, parse_int, require,
                     permission_required, to_cents)

from ..services.permissions import has, has_any  # noqa: E402

bp = Blueprint("payroll", __name__)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
@bp.get("/payroll/runs")
@permission_required("payroll.view")
def list_runs():
    q = PayrollRun.query
    if request.args.get("year"):
        q = q.filter(PayrollRun.period.like(f"{parse_int(request.args['year'], 'year')}-%"))
    runs = q.order_by(PayrollRun.period.desc(), PayrollRun.id.desc()).limit(120).all()
    return jsonify(items=[svc.run_dict(r) for r in runs])


@bp.get("/payroll/runs/<int:rid>")
@permission_required("payroll.view")
def get_run(rid):
    return jsonify(svc.run_dict(get_or_404(PayrollRun, rid, "Payroll"), detail=True))


@bp.post("/payroll/runs")
@permission_required("payroll.manage")
def create_run():
    data = body()
    require(data, "period")
    period = parse_period(data["period"])
    pay_date = parse_date(data.get("pay_date"), "pay_date", required=False) or month_start(period).replace(day=25)
    run = svc.create_run(period, pay_date, clean_str(data.get("notes"), 200), current_user.id)
    audit("create", "payroll", run.id, f"{run.run_no} {period}")
    db.session.commit()
    return jsonify(svc.run_dict(run, detail=True)), 201


@bp.post("/payroll/runs/<int:rid>/recalculate")
@permission_required("payroll.manage")
def recalculate(rid):
    """Refresh a draft from current staff records, adding newly eligible staff."""
    run = get_or_404(PayrollRun, rid, "Payroll")
    if run.status != "draft":
        raise ApiError("Only draft payrolls can be recalculated", 409)
    have = {p.staff_id for p in run.payslips}
    eligible = {s.id: s for s in svc.eligible_staff(run.period)}
    for sid, s in eligible.items():
        if sid not in have:
            run.payslips.append(Payslip(staff=s))
    for p in list(run.payslips):
        if p.staff_id not in eligible:
            run.payslips.remove(p)
    run.tax_table = svc.table_for(run.period)
    db.session.flush()
    svc.recalculate(run)
    db.session.commit()
    return jsonify(svc.run_dict(run, detail=True))


@bp.put("/payroll/payslips/<int:pid>")
@permission_required("payroll.manage")
def update_payslip(pid):
    """One-off inputs for the month (overtime, bonus, extra deduction) on a draft payroll."""
    p = get_or_404(Payslip, pid, "Payslip")
    if p.run.status != "draft":
        raise ApiError("Payslips can only be changed while the payroll is a draft", 409)
    data = body()
    p.overtime_cents = to_cents(data.get("overtime") or 0, "overtime", allow_zero=True)
    p.overtime_currency = fx.pick(data.get("overtime_currency"), "overtime_currency")
    p.bonus_currency = fx.pick(data.get("bonus_currency"), "bonus_currency")
    p.extra_deduction_currency = fx.pick(data.get("extra_deduction_currency"), "extra_deduction_currency")
    p.bonus_cents = to_cents(data.get("bonus") or 0, "bonus", allow_zero=True)
    p.extra_deduction_cents = to_cents(data.get("extra_deduction") or 0, "extra_deduction", allow_zero=True)
    p.extra_deduction_note = clean_str(data.get("extra_deduction_note"), 60)
    if p.extra_deduction_cents and not p.extra_deduction_note:
        raise ApiError("Describe the deduction", fields={"extra_deduction_note": "Required"})
    svc.recalculate(p.run, [p])
    audit("update", "payslip", p.id, f"{p.run.run_no} {p.staff.name}")
    db.session.commit()
    return jsonify(svc.payslip_dict(p, detail=True))


@bp.get("/payroll/payslips/<int:pid>")
@permission_required("payroll.view")
def get_payslip(pid):
    return jsonify(svc.payslip_dict(get_or_404(Payslip, pid, "Payslip"), detail=True))


@bp.post("/payroll/runs/<int:rid>/approve")
@permission_required("payroll.approve")
def approve(rid):
    run = get_or_404(PayrollRun, rid, "Payroll")
    svc.approve(run, current_user)
    audit("approve", "payroll", run.id, run.run_no)
    db.session.commit()
    return jsonify(svc.run_dict(run, detail=True))


def _cash_account(data):
    aid = parse_int(data.get("account_id"), "account_id")
    return get_or_404(Account, aid, "Account")


@bp.post("/payroll/runs/<int:rid>/pay")
@permission_required("payroll.manage")
def pay(rid):
    run = get_or_404(PayrollRun, rid, "Payroll")
    data = body()
    on = parse_date(data.get("paid_on"), "paid_on", required=False) or date.today()
    # One account per currency paid: {"accounts": {"USD": id, "ZWG": id}}, or account_id for one currency.
    accounts = {}
    for cur, aid in (data.get("accounts") or {}).items():
        if aid:
            accounts[cur] = get_or_404(Account, parse_int(aid, f"account_{cur}"), "Account")
    if data.get("account_id"):
        acc = _cash_account(data)
        accounts.setdefault(acc.currency or fx.base(), acc)
    svc.pay(run, accounts, on, current_user.id)
    audit("pay", "payroll", run.id, run.run_no)
    db.session.commit()
    return jsonify(svc.run_dict(run, detail=True))


@bp.post("/payroll/runs/<int:rid>/remit")
@permission_required("payroll.manage")
def remit(rid):
    run = get_or_404(PayrollRun, rid, "Payroll")
    data = body()
    require(data, "type")
    on = parse_date(data.get("paid_on"), "paid_on", required=False) or date.today()
    reference = clean_str(data.get("reference"), 60)
    if not reference:
        raise ApiError("Enter the payment reference (e.g. the ZIMRA assessment or bank reference)",
                       fields={"reference": "Required"})
    r = svc.remit(run, data["type"], fx.pick(data.get("currency")), _cash_account(data), on, reference, current_user.id)
    audit("remit", "payroll", run.id, f"{run.run_no} {r.type} {money(r.amount_cents)}")
    db.session.commit()
    return jsonify(svc.run_dict(run, detail=True))


@bp.post("/payroll/runs/<int:rid>/void")
@permission_required("payroll.manage", "payroll.approve")
def void(rid):
    run = get_or_404(PayrollRun, rid, "Payroll")
    reason = clean_str(body().get("reason"), 200)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required", fields={"reason": "Required"})
    if run.status != "draft" and not has("payroll.approve"):
        raise ApiError("Voiding an approved payroll needs permission to approve payroll", 403)
    if run.status == "draft" and not has("payroll.manage"):
        raise ApiError("You do not have permission to discard a draft payroll", 403)
    svc.void(run, reason, current_user.id)
    audit("void", "payroll", run.id, f"{run.run_no}: {reason}")
    db.session.commit()
    return jsonify(svc.run_dict(run, detail=True))


# --------------------------------------------------------------------------- #
# Staff pay setup
# --------------------------------------------------------------------------- #
def item_dict(i):
    return {"id": i.id, "staff_id": i.staff_id, "type": i.type, "name": i.name, "amount": money(i.amount_cents),
            "currency": i.currency or fx.base(),
            "taxable": i.taxable, "active": i.active}


@bp.get("/payroll/staff")
@permission_required("payroll.view")
def staff_pay():
    rows = []
    for s in Staff.query.filter(Staff.status != "left").order_by(Staff.last_name, Staff.first_name):
        items = [i for i in s.pay_items]
        rows.append({"id": s.id, "staff_no": s.staff_no, "name": s.name, "position": s.position, "status": s.status,
                     "salary": money(s.salary_cents), "salary_currency": s.salary_currency or fx.base(),
                     "tax_number": s.tax_number, "nssa_number": s.nssa_number,
                     "national_id": s.national_id, "dob": s.dob.isoformat() if s.dob else None,
                     "disabled": bool(s.disabled), "bank_name": s.bank_name, "bank_account": s.bank_account,
                     "items": [item_dict(i) for i in items],
                     "missing": [lbl for lbl, v in (("ZIMRA BP/TIN", s.tax_number), ("NSSA no.", s.nssa_number),
                                                    ("National ID", s.national_id)) if not v]})
    return jsonify(items=rows, types=PAY_ITEM_TYPES)


@bp.put("/payroll/staff/<int:sid>")
@permission_required("payroll.manage")
def update_staff_pay(sid):
    s = get_or_404(Staff, sid, "Staff member")
    data = body()
    s.tax_number = clean_str(data.get("tax_number"), 30)
    s.nssa_number = clean_str(data.get("nssa_number"), 30)
    s.national_id = clean_str(data.get("national_id"), 30)
    s.bank_name = clean_str(data.get("bank_name"), 60)
    s.bank_account = clean_str(data.get("bank_account"), 40)
    s.dob = parse_date(data.get("dob"), "dob", required=False)
    if s.dob and not 15 <= (date.today() - s.dob).days / 365.25 <= 100:
        raise ApiError("Check the date of birth", fields={"dob": "Implausible"})
    s.disabled = bool(data.get("disabled"))
    if "salary" in data and has("payroll.approve"):
        s.salary_cents = to_cents(data.get("salary") or 0, "salary", allow_zero=True)
    if data.get("salary_currency") and has("payroll.approve"):
        s.salary_currency = fx.pick(data["salary_currency"], "salary_currency")
    audit("update", "staff_pay", s.id, s.name)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/payroll/staff/<int:sid>/items")
@permission_required("payroll.manage")
def add_item(sid):
    s = get_or_404(Staff, sid, "Staff member")
    data = body()
    require(data, "type", "name", "amount")
    if data["type"] not in PAY_ITEM_TYPES:
        raise ApiError("Invalid item type", fields={"type": "Invalid"})
    i = StaffPayItem(staff=s, type=data["type"], name=clean_str(data["name"], 60), amount_cents=to_cents(data["amount"]),
                     currency=fx.pick(data.get("currency")),
                     taxable=bool(data.get("taxable", True)) if data["type"] == "allowance" else True)
    db.session.add(i)
    db.session.flush()
    audit("create", "pay_item", i.id, f"{s.name}: {i.type} {i.name} {money(i.amount_cents)}")
    db.session.commit()
    return jsonify(item_dict(i)), 201


@bp.put("/payroll/items/<int:iid>")
@permission_required("payroll.manage")
def update_item(iid):
    i = get_or_404(StaffPayItem, iid, "Pay item")
    data = body()
    if data.get("name"):
        i.name = clean_str(data["name"], 60)
    if data.get("amount") not in (None, ""):
        i.amount_cents = to_cents(data["amount"])
    if "active" in data:
        i.active = bool(data["active"])
    if "taxable" in data and i.type == "allowance":
        i.taxable = bool(data["taxable"])
    audit("update", "pay_item", i.id, f"{i.staff.name}: {i.name}")
    db.session.commit()
    return jsonify(item_dict(i))


@bp.delete("/payroll/items/<int:iid>")
@permission_required("payroll.manage")
def delete_item(iid):
    i = get_or_404(StaffPayItem, iid, "Pay item")
    audit("delete", "pay_item", i.id, f"{i.staff.name}: {i.name}")
    db.session.delete(i)
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Tax tables
# --------------------------------------------------------------------------- #
@bp.get("/payroll/tax-tables")
@permission_required("payroll.view")
def tax_tables():
    tables = TaxTable.query.order_by(TaxTable.effective_from.desc()).all()
    return jsonify(items=[svc.table_dict(t) for t in tables], defaults=svc.ZIMRA_USD_2025)


@bp.post("/payroll/tax-tables")
@permission_required("payroll.approve")
def create_tax_table():
    data = body()
    require(data, "name", "effective_from", "config")
    eff = parse_date(data["effective_from"], "effective_from")
    if TaxTable.query.filter_by(effective_from=eff).first():
        raise ApiError("A tax table already starts on that date", fields={"effective_from": "Duplicate"})
    used = PayrollRun.query.filter(PayrollRun.status.in_(("approved", "paid")), PayrollRun.period >= eff.isoformat()[:7]).first()
    if used:
        raise ApiError(f"Payroll {used.run_no} for {used.period} is already approved under the existing table. "
                       "New tables can only take effect after the last approved payroll.", 409)
    cfg = svc.validate_config(data["config"])
    t = TaxTable(name=clean_str(data["name"], 80), effective_from=eff, config=json.dumps(cfg), created_by=current_user.id)
    db.session.add(t)
    db.session.flush()
    audit("create", "tax_table", t.id, f"{t.name} from {eff.isoformat()}")
    db.session.commit()
    return jsonify(svc.table_dict(t)), 201


@bp.get("/payroll/calculator")
@permission_required("payroll.view")
def calculator():
    """Quick PAYE check against the current table: ?gross=1500"""
    gross = to_cents(request.args.get("gross") or 0, "gross", allow_zero=True)
    t = svc.table_for(date.today().isoformat()[:7])
    cfg = json.loads(t.config)
    nssa = svc._pct(min(gross, svc._cents(cfg["nssa_ceiling"])), cfg["nssa_rate_pct"])
    taxable = max(gross - nssa, 0)
    tax = svc.band_tax(taxable, cfg["bands"])
    levy = svc._pct(tax, cfg["aids_levy_pct"])
    return jsonify(table=t.name, gross=money(gross), nssa=money(nssa), taxable=money(taxable), paye=money(tax),
                   aids_levy=money(levy), net=money(gross - nssa - tax - levy))


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #
@bp.get("/payroll/returns")
@permission_required("payroll.view")
def returns():
    year = parse_int(request.args.get("year"), "year", required=False) or date.today().year
    return jsonify(year=year, p2=svc.p2_return(year), annual=svc.annual_summary(year))
