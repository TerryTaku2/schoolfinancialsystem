"""Payroll in line with ZIMRA PAYE (Final Deduction System), NSSA and ZIMDEF.

Monthly calculation for each employee (all figures in the payroll currency):

    Gross cash earnings   basic (pro-rated in the month of hire) + allowances + overtime + bonus
    Taxable benefits      non-cash benefits (housing, vehicle...) are taxed but not paid out
    Exempt income         bonus up to the annual bonus exemption, plus allowances marked exempt
    NSSA (employee)       nssa_rate% of insurable earnings, capped at the NSSA ceiling
    Pension               employee contributions to an approved fund, deductible up to the cap
    Taxable income        gross + benefits - exempt - NSSA - deductible pension
    Income tax            ZIMRA progressive bands on taxable income
    Tax credits           elderly (age 55+), blind/disabled, and medical_credit_pct% of medical aid
    PAYE                  income tax less credits (never below zero)
    AIDS levy             aids_levy_pct% of PAYE
    Net pay               gross - NSSA - pension - medical aid - PAYE - AIDS levy - other deductions

Employer costs: NSSA employer share (same as the employee's), WCIF (wcif_pct% of insurable
earnings, set from the school's NSSA assessment) and the ZIMDEF levy (zimdef_pct% of gross).

Workflow and postings:

    Draft       created by the bursar; recalculated freely, no postings
    Approved    by an administrator other than the preparer. Accrued on the last day of the month:
                Dr Salaries & Wages (gross)               Cr Net Salaries Payable
                Dr Employer Payroll Contributions         Cr PAYE & AIDS Levy Payable
                                                          Cr NSSA Payable, ZIMDEF Payable
                                                          Cr Pension & Medical Aid / Other Deductions Payable
    Paid        Dr Net Salaries Payable                   Cr Bank (refused if funds are short)
    Remitted    Dr the liability (ZIMRA, NSSA...)         Cr Bank. PAYE is due to ZIMRA by the 10th
                of the following month (ZIMRA P2 return).
    Void        approved runs not yet paid or remitted: the accrual is reversed.
"""
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .. import db
from ..models import Payslip, PayrollRemittance, PayrollRun, Staff, TaxTable
from ..utils import ApiError, money, next_number
from . import currency as fx
from . import ledger
from .assets import month_add, month_end, month_start

SALARIES, EMPLOYER_COSTS = "5000", "5010"
PAYE_PAYABLE, NSSA_PAYABLE, ZIMDEF_PAYABLE, PENSION_PAYABLE, OTHER_PAYABLE, NET_PAYABLE = (
    "2100", "2110", "2120", "2130", "2140", "2150")
REMIT_ACCOUNT = {"zimra": PAYE_PAYABLE, "nssa": NSSA_PAYABLE, "zimdef": ZIMDEF_PAYABLE,
                 "pension_medical": PENSION_PAYABLE, "other": OTHER_PAYABLE}
REMIT_LABEL = {"zimra": "ZIMRA (PAYE & AIDS levy)", "nssa": "NSSA (contributions & WCIF)",
               "zimdef": "ZIMDEF levy", "pension_medical": "Pension funds & medical aid",
               "other": "Other deductions (loans, unions...)"}

# ZIMRA USD tax tables, 1 January - 31 December 2025 (monthly). Each band's tax is the
# rate on income above the previous limit; this reproduces ZIMRA's "rate less deduction"
# columns exactly (e.g. 300.01-1000: 25% less 35).
ZIMRA_USD_2025 = {
    "currency": "USD",
    "bands": [{"upto": 100, "rate": 0}, {"upto": 300, "rate": 20}, {"upto": 1000, "rate": 25},
              {"upto": 2000, "rate": 30}, {"upto": 3000, "rate": 35}, {"upto": None, "rate": 40}],
    "aids_levy_pct": 3,
    "nssa_rate_pct": 4.5,
    "nssa_ceiling": 700,        # SI 99 of 2024, from 1 June 2024
    "wcif_pct": 0,              # set from the school's NSSA WCIF assessment
    "zimdef_pct": 1,
    "pension_cap": 450,         # US$5,400 a year
    "elderly_credit": 75,       # US$900 a year
    "disabled_credit": 75,      # US$900 a year
    "elderly_age": 55,
    "medical_credit_pct": 50,
    "bonus_exempt": 700,        # per tax year
}
CONFIG_KEYS = [k for k in ZIMRA_USD_2025 if k not in ("bands", "currency")]


def _cents(amount):
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _round(x):
    return int(Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _pct(cents, pct):
    return _round(Decimal(cents) * Decimal(str(pct)) / 100)


# --------------------------------------------------------------------------- #
# Tax tables
# --------------------------------------------------------------------------- #
def ensure_tax_table():
    if TaxTable.query.count() == 0:
        db.session.add(TaxTable(name="ZIMRA USD tax tables 2025", effective_from=date(2025, 1, 1),
                                config=json.dumps(ZIMRA_USD_2025)))
        db.session.commit()


def table_for(period):
    t = (TaxTable.query.filter(TaxTable.effective_from <= month_end(period))
         .order_by(TaxTable.effective_from.desc()).first())
    if not t:
        raise ApiError(f"No tax table is in force for {period}. Add one under Payroll → Tax tables.", 409)
    return t


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ApiError("Invalid tax table")
    bands = cfg.get("bands")
    if not isinstance(bands, list) or len(bands) < 1:
        raise ApiError("A tax table needs at least one band", fields={"bands": "Required"})
    prev, clean = 0, []
    for i, b in enumerate(bands):
        last = i == len(bands) - 1
        try:
            rate = float(b.get("rate"))
            upto = None if last else float(b.get("upto"))
        except (TypeError, ValueError, AttributeError):
            raise ApiError(f"Band {i + 1}: enter a limit and a rate")
        if not 0 <= rate <= 100:
            raise ApiError(f"Band {i + 1}: rate must be between 0 and 100")
        if upto is not None and upto <= prev:
            raise ApiError(f"Band {i + 1}: limits must increase from band to band")
        clean.append({"upto": upto, "rate": rate})
        prev = upto or prev
    out = {"currency": str(cfg.get("currency") or "USD")[:5], "bands": clean}
    for k in CONFIG_KEYS:
        try:
            v = float(cfg.get(k, ZIMRA_USD_2025[k]))
        except (TypeError, ValueError):
            raise ApiError(f"{k.replace('_', ' ')} must be a number", fields={k: "Invalid"})
        if v < 0 or (k.endswith("_pct") and v > 100):
            raise ApiError(f"{k.replace('_', ' ')} is out of range", fields={k: "Out of range"})
        out[k] = v
    return out


def band_tax(taxable_cents, bands):
    """Progressive tax in cents on a monthly taxable income in cents."""
    tax, lower = Decimal(0), 0
    for b in bands:
        upper = None if b["upto"] is None else _cents(b["upto"])
        if taxable_cents <= lower:
            break
        portion = (min(taxable_cents, upper) if upper is not None else taxable_cents) - lower
        tax += Decimal(portion) * Decimal(str(b["rate"])) / 100
        if upper is None:
            break
        lower = upper
    return _round(tax)


def zimra_deduction_column(bands):
    """ZIMRA's 'less' amount per band (rate x lower limit minus tax up to the lower limit), for display."""
    out, lower = [], 0
    for b in bands:
        less = Decimal(lower) * Decimal(str(b["rate"])) / 100 - band_tax(lower, bands)
        out.append(money(_round(less)))
        lower = _cents(b["upto"]) if b["upto"] is not None else lower
    return out


def table_dict(t):
    cfg = json.loads(t.config)
    return {"id": t.id, "name": t.name, "effective_from": t.effective_from.isoformat(), "config": cfg,
            "less": zimra_deduction_column(cfg["bands"])}


# --------------------------------------------------------------------------- #
# Calculation
# --------------------------------------------------------------------------- #
def _age(dob, on):
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def _bonus_exempt_used(slip):
    """Bonus exemption already used by this employee in other runs of the same tax year (Jan-Dec)."""
    year = slip.run.period[:4]
    q = (Payslip.query.join(PayrollRun).filter(Payslip.staff_id == slip.staff_id, PayrollRun.status != "void",
                                               PayrollRun.period.like(f"{year}-%"), PayrollRun.id != slip.run_id))
    return sum(json.loads(s.detail or "{}").get("bonus_exempt", 0) for s in q)


def run_rates(run):
    """{currency: units per 1 USD} the run was calculated with (USD itself is 1)."""
    rates = json.loads(run.exchange_rates) if run.exchange_rates else {}
    if not rates and run.exchange_rate:
        rates = {"ZWG": run.exchange_rate}  # run from the two-currency version
    return {fx.ANCHOR: 1, **rates}


def _converter(run, tax_cur):
    """Functions converting between a component's currency and the tax table's currency at the
    run's exchange rates (units per 1 USD), as ZIMRA requires for pay split across currencies."""
    rates = run_rates(run)

    def need(cur):
        if cur not in rates:
            raise ApiError(f"Pay for {run.period} includes more than one currency. Enter the {cur} exchange rate "
                           f"(per 1 USD) for {run.pay_date.isoformat()} or earlier, then recalculate.", 409)
        return Decimal(str(rates[cur]))

    def to_tax(cents, cur):
        if cur == tax_cur:
            return cents
        return _round(Decimal(cents) * need(tax_cur) / need(cur))

    def from_tax(cents, cur):
        if cur == tax_cur:
            return cents
        return _round(Decimal(cents) * need(cur) / need(tax_cur))

    return to_tax, from_tax


def rates_text(run, currencies):
    """e.g. "1 USD = 26.75 ZWG and 18.4 ZAR" for the currencies a payslip involves."""
    rates = run_rates(run)
    parts = [f"{rates[c]:g} {c}" for c in currencies if c != fx.ANCHOR and c in rates]
    return f"1 USD = {' and '.join(parts)}" if parts else ""


def _split(total, weights):
    """Share `total` across keys in proportion to `weights` (cents), exactly (remainder to the largest)."""
    whole = sum(weights.values())
    if not whole or not total:
        return {k: 0 for k in weights}
    out = {k: _round(Decimal(total) * w / whole) for k, w in weights.items()}
    biggest = max(weights, key=weights.get)
    out[biggest] += total - sum(out.values())
    return out


def calculate(slip, cfg):
    """Work out one payslip (see module docstring), per currency where pay is split.

    Every component keeps its own currency. Tax is computed on the total converted to the tax
    table's currency at the run's rate; PAYE, AIDS levy, NSSA and employer levies are then
    shared across the currencies in proportion to the cash pay in each (ZIMRA's rule for
    remuneration paid partly in each currency). Pension, medical aid and other deductions come
    off the currency they are set in.
    """
    from ..models import PayslipCurrency
    st, run = slip.staff, slip.run
    tax_cur = cfg.get("currency") or "USD"
    to_tax, from_tax = _converter(run, tax_cur)
    start, end = month_start(run.period), month_end(run.period)
    earnings, deductions, notes = [], [], []
    gross_c, pension_c, medical_c, other_c = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)

    base_cur = st.salary_currency or fx.base()
    basic = st.salary_cents
    if st.hire_date and start < st.hire_date <= end:
        days = (end - st.hire_date).days + 1
        basic = _round(Decimal(basic) * days / end.day)
        notes.append(f"Basic pro-rated for {days} of {end.day} days (hired {st.hire_date.isoformat()})")
    if basic:
        earnings.append(("Basic salary", basic, base_cur))
        gross_c[base_cur] += basic

    items = [i for i in st.pay_items if i.active]
    exempt = benefits = 0
    for i in items:
        cur = i.currency or fx.base()
        if i.type == "allowance":
            gross_c[cur] += i.amount_cents
            earnings.append((i.name + ("" if i.taxable else " (exempt)"), i.amount_cents, cur))
            if not i.taxable:
                exempt += to_tax(i.amount_cents, cur)
        elif i.type == "benefit":
            benefits += to_tax(i.amount_cents, cur)
        elif i.type == "pension":
            pension_c[cur] += i.amount_cents
            deductions.append((i.name, i.amount_cents, cur))
        elif i.type == "medical_aid":
            medical_c[cur] += i.amount_cents
            deductions.append((i.name, i.amount_cents, cur))
        else:
            other_c[cur] += i.amount_cents
            deductions.append((i.name, i.amount_cents, cur))
    if slip.overtime_cents:
        cur = slip.overtime_currency or fx.base()
        gross_c[cur] += slip.overtime_cents
        earnings.append(("Overtime", slip.overtime_cents, cur))
    bonus_exempt = 0
    if slip.bonus_cents:
        cur = slip.bonus_currency or fx.base()
        gross_c[cur] += slip.bonus_cents
        earnings.append(("Bonus", slip.bonus_cents, cur))
        remaining = max(_cents(cfg["bonus_exempt"]) - _bonus_exempt_used(slip), 0)
        bonus_exempt = min(to_tax(slip.bonus_cents, cur), remaining)
        exempt += bonus_exempt
        if bonus_exempt:
            notes.append(f"Bonus exemption applied: {tax_cur} {money(bonus_exempt):,.2f}")
    if slip.extra_deduction_cents:
        cur = slip.extra_deduction_currency or fx.base()
        other_c[cur] += slip.extra_deduction_cents
        deductions.append((slip.extra_deduction_note or "Other deduction", slip.extra_deduction_cents, cur))

    # Everything below is in the tax table's currency.
    gross_t = {c: to_tax(v, c) for c, v in gross_c.items()}
    gross = sum(gross_t.values())
    pension = sum(to_tax(v, c) for c, v in pension_c.items())
    medical = sum(to_tax(v, c) for c, v in medical_c.items())
    other = sum(to_tax(v, c) for c, v in other_c.items())
    insurable = min(gross, _cents(cfg["nssa_ceiling"]))
    nssa = _pct(insurable, cfg["nssa_rate_pct"])
    pension_deductible = min(pension, _cents(cfg["pension_cap"]))
    taxable = max(gross + benefits - exempt - nssa - pension_deductible, 0)
    tax = band_tax(taxable, cfg["bands"])

    credits = []
    if st.dob and _age(st.dob, end) >= cfg["elderly_age"]:
        credits.append(("Elderly person's credit", _cents(cfg["elderly_credit"])))
    if st.disabled:
        credits.append(("Blind / disabled person's credit", _cents(cfg["disabled_credit"])))
    if medical:
        credits.append(("Medical aid credit", _pct(medical, cfg["medical_credit_pct"])))
    credit_total = sum(c for _, c in credits)
    paye = max(tax - credit_total, 0)
    levy = _pct(paye, cfg["aids_levy_pct"])
    wcif = _pct(insurable, cfg["wcif_pct"])
    zimdef = _pct(gross, cfg["zimdef_pct"])
    net = gross - nssa - pension - medical - paye - levy - other

    slip.basic_cents, slip.gross_cents, slip.benefits_cents, slip.exempt_cents = to_tax(basic, base_cur), gross, benefits, exempt
    slip.nssa_cents, slip.pension_cents, slip.medical_aid_cents = nssa, pension, medical
    slip.taxable_cents, slip.tax_before_credits_cents = taxable, tax
    slip.credits_cents, slip.paye_cents, slip.aids_levy_cents = min(credit_total, tax), paye, levy
    slip.other_deductions_cents, slip.net_cents = other, net
    slip.nssa_employer_cents, slip.wcif_cents, slip.zimdef_cents = nssa, wcif, zimdef

    # What is actually paid and withheld in each currency.
    currencies = sorted(set(gross_c) | set(pension_c) | set(medical_c) | set(other_c)) or [tax_cur]
    weights = {c: gross_t.get(c, 0) for c in currencies}
    shares = {k: _split(v, weights) for k, v in
              (("nssa", nssa), ("paye", paye), ("levy", levy), ("wcif", wcif), ("zimdef", zimdef))}
    parts = []
    for c in currencies:
        part = {"gross": gross_c.get(c, 0), "pension": pension_c.get(c, 0), "medical": medical_c.get(c, 0),
                "other": other_c.get(c, 0), **{k: from_tax(v[c], c) for k, v in shares.items()}}
        part["net"] = (part["gross"] - part["nssa"] - part["paye"] - part["levy"] - part["pension"]
                       - part["medical"] - part["other"])
        parts.append((c, part))
    slip.currency_parts.clear()
    db.session.flush()
    for c, p in parts:
        slip.currency_parts.append(PayslipCurrency(
            currency=c, gross_cents=p["gross"], nssa_cents=p["nssa"], paye_cents=p["paye"], aids_levy_cents=p["levy"],
            pension_cents=p["pension"], medical_aid_cents=p["medical"], other_deductions_cents=p["other"],
            net_cents=p["net"], nssa_employer_cents=p["nssa"], wcif_cents=p["wcif"], zimdef_cents=p["zimdef"]))

    statutory = []
    for c, p in parts:
        label = f" ({c})" if len(parts) > 1 else ""
        statutory += [("PAYE" + label, p["paye"], c), ("AIDS levy" + label, p["levy"], c), ("NSSA" + label, p["nssa"], c)]
    if len(parts) > 1:
        share = ", ".join(f"{c} {gross_t.get(c, 0) / gross * 100:.1f}%" for c, _ in parts if gross)
        notes.append(f"Pay in {' and '.join(c for c, _ in parts)}: tax worked out on the total in {tax_cur} at "
                     f"{rates_text(run, [tax_cur] + [c for c, _ in parts])} and withheld in proportion to pay ({share}).")
    slip.detail = json.dumps({
        "tax_currency": tax_cur,
        "earnings": earnings, "deductions": [d for d in statutory + deductions if d[1]],
        "benefits": [(i.name, i.amount_cents, i.currency or fx.base()) for i in items if i.type == "benefit"],
        "credits": credits, "notes": notes, "bonus_exempt": bonus_exempt, "insurable": insurable,
        "pension_deductible": pension_deductible,
    })
    return slip


def eligible_staff(period):
    end = month_end(period)
    return [s for s in Staff.query.filter(Staff.status.in_(("active", "on_leave"))).order_by(Staff.last_name, Staff.first_name)
            if (s.hire_date is None or s.hire_date <= end) and (s.salary_cents > 0 or any(i.active for i in s.pay_items))]


def create_run(period, pay_date, notes, user_id):
    if month_start(period) > date.today():
        raise ApiError("Payroll can't be prepared for a future month", fields={"period": "Future month"})
    if PayrollRun.query.filter(PayrollRun.period == period, PayrollRun.status != "void").first():
        raise ApiError(f"A payroll for {period} already exists. Void it before preparing another.", 409)
    if not month_start(period) <= pay_date <= month_end(month_add(period, 1)):
        raise ApiError("The pay date must fall in the payroll month or the month after", fields={"pay_date": "Invalid"})
    staff = eligible_staff(period)
    if not staff:
        raise ApiError("No active staff with a salary or pay items for this month")
    table = table_for(period)
    run = PayrollRun(run_no=next_number("payroll", "PR-", 5), period=period, pay_date=pay_date, notes=notes,
                     tax_table=table, created_by=user_id)
    db.session.add(run)
    for s in staff:
        run.payslips.append(Payslip(staff=s))
    db.session.flush()
    recalculate(run)
    return run


def recalculate(run, slips=None):
    if run.status != "draft":
        raise ApiError("Only draft payrolls can be recalculated", 409)
    rates = {c: r.per_usd for c, r in fx.latest_rates(run.pay_date).items()}
    run.exchange_rates = json.dumps(rates) if rates else None
    run.exchange_rate = rates.get("ZWG")
    cfg = json.loads(run.tax_table.config)
    for slip in slips or run.payslips:
        calculate(slip, cfg)
    db.session.flush()


def totals(run):
    """Totals in the tax table's currency (USD terms for a USD table)."""
    s = run.payslips
    t = {k: sum(getattr(p, f"{k}_cents") for p in s) for k in (
        "gross", "benefits", "exempt", "nssa", "pension", "medical_aid", "taxable", "tax_before_credits", "credits",
        "paye", "aids_levy", "other_deductions", "net", "nssa_employer", "wcif", "zimdef")}
    t["employees"] = len(s)
    t["total_tax"] = t["paye"] + t["aids_levy"]
    t["employer_cost"] = t["gross"] + t["nssa_employer"] + t["wcif"] + t["zimdef"]
    return t


def currency_totals(run):
    """{currency: totals} of what is actually paid and withheld in each currency."""
    out = {}
    for p in run.payslips:
        for part in p.currency_parts:
            t = out.setdefault(part.currency, defaultdict(int))
            for k in ("gross", "nssa", "paye", "aids_levy", "pension", "medical_aid", "other_deductions", "net",
                      "nssa_employer", "wcif", "zimdef"):
                t[k] += getattr(part, f"{k}_cents")
    for t in out.values():
        t["total_tax"] = t["paye"] + t["aids_levy"]
        t["employer_cost"] = t["gross"] + t["nssa_employer"] + t["wcif"] + t["zimdef"]
    return {c: dict(out[c]) for c in sorted(out)}


def liabilities(run):
    """{currency: {payee type: cents}} due for this run."""
    return {c: {"zimra": t["total_tax"], "nssa": t["nssa"] + t["nssa_employer"] + t["wcif"], "zimdef": t["zimdef"],
                "pension_medical": t["pension"] + t["medical_aid"], "other": t["other_deductions"]}
            for c, t in currency_totals(run).items()}


# --------------------------------------------------------------------------- #
# Workflow
# --------------------------------------------------------------------------- #
def approve(run, user):
    if run.status != "draft":
        raise ApiError(f"Only draft payrolls can be approved (this one is {run.status})", 409)
    from .permissions import permissions_for
    if "payroll.approve" not in permissions_for(user) or run.created_by == user.id:
        raise ApiError("Payroll must be approved by someone allowed to approve payroll, other than the person who prepared it", 403)
    recalculate(run)  # pick up any changes to staff records since the draft was prepared
    negative = [f"{p.staff.name} ({part.currency})" for p in run.payslips for part in p.currency_parts if part.net_cents < 0]
    if negative:
        raise ApiError("Deductions exceed pay for: " + ", ".join(negative) + ". Adjust before approving.", 409)
    a = ledger.acct
    for cur, t in currency_totals(run).items():
        lines = [
            {"account": a(SALARIES), "debit": t["gross"], "memo": f"{len(run.payslips)} employees"},
            {"account": a(EMPLOYER_COSTS), "debit": t["nssa_employer"] + t["wcif"] + t["zimdef"], "memo": "NSSA, WCIF, ZIMDEF"},
            {"account": a(PAYE_PAYABLE), "credit": t["total_tax"], "memo": "PAYE and AIDS levy"},
            {"account": a(NSSA_PAYABLE), "credit": t["nssa"] + t["nssa_employer"] + t["wcif"], "memo": "NSSA and WCIF"},
            {"account": a(ZIMDEF_PAYABLE), "credit": t["zimdef"]},
            {"account": a(PENSION_PAYABLE), "credit": t["pension"] + t["medical_aid"]},
            {"account": a(OTHER_PAYABLE), "credit": t["other_deductions"]},
            {"account": a(NET_PAYABLE), "credit": t["net"], "memo": "Net pay"},
        ]
        if any(l.get("debit") or l.get("credit") for l in lines):
            ledger.post(month_end(run.period), f"Payroll {run.run_no} for {run.period} ({cur})", lines, "payroll",
                        run.id, run.run_no, user.id, currency=cur)
    run.status, run.approved_by, run.approved_at = "approved", user.id, date.today()


def pay(run, accounts, on_date, user_id):
    """Pay each currency's net salaries from an account in that currency: accounts = {currency: Account}."""
    if run.status != "approved":
        raise ApiError("Only approved payrolls can be paid", 409)
    if on_date > date.today() or on_date < month_start(run.period):
        raise ApiError("The payment date must be between the start of the payroll month and today",
                       fields={"paid_on": "Invalid"})
    due = {c: t["net"] for c, t in currency_totals(run).items() if t["net"] > 0}
    for cur, net in due.items():
        acc = accounts.get(cur)
        if acc is None:
            raise ApiError(f"Choose the account to pay the {cur} salaries from", fields={f"account_{cur}": "Required"})
        if acc.subtype != "cash" or fx.of(acc) != cur:
            raise ApiError(f"{cur} salaries must be paid from a {cur} cash, bank or mobile money account")
        available = ledger.balance(acc, on_date)
        if available < net:
            raise ApiError(f"Insufficient funds in {acc.name}: balance on {on_date.isoformat()} is "
                           f"{cur} {money(available):,.2f}, net pay is {cur} {money(net):,.2f}", 409)
    for cur, net in due.items():
        acc = accounts[cur]
        ledger.post(on_date, f"Salaries paid: payroll {run.run_no} ({run.period}, {cur})", [
            {"account": ledger.acct(NET_PAYABLE), "debit": net, "memo": f"{len(run.payslips)} employees"},
            {"account": acc, "credit": net, "memo": run.run_no},
        ], "payroll_payment", run.id, run.run_no, user_id, currency=cur)
    run.paid_accounts = json.dumps({c: accounts[c].id for c in due})
    first = next(iter(due), None)
    run.status, run.paid_at = "paid", on_date
    run.paid_from_account_id = accounts[first].id if first else None


def remit(run, kind, currency, cash_account, on_date, reference, user_id):
    if run.status not in ("approved", "paid"):
        raise ApiError("Only approved payrolls can be remitted", 409)
    if kind not in REMIT_ACCOUNT:
        raise ApiError("Unknown remittance type")
    if any(r.type == kind and fx.of(r) == currency for r in run.remittances):
        raise ApiError(f"{REMIT_LABEL[kind]} in {currency} has already been remitted for this payroll", 409)
    amount = liabilities(run).get(currency, {}).get(kind, 0)
    if amount <= 0:
        raise ApiError(f"Nothing is due in {currency} to {REMIT_LABEL[kind]} for this payroll")
    if cash_account.subtype != "cash" or fx.of(cash_account) != currency:
        raise ApiError(f"Pay the {currency} amount from a {currency} cash, bank or mobile money account")
    if on_date > date.today() or on_date < month_start(run.period):
        raise ApiError("The payment date must be between the start of the payroll month and today",
                       fields={"paid_on": "Invalid"})
    if ledger.balance(cash_account, on_date) < amount:
        raise ApiError(f"Insufficient funds in {cash_account.name} on {on_date.isoformat()}", 409)
    ledger.post(on_date, f"{REMIT_LABEL[kind]} remitted for {run.period} ({run.run_no}, {currency})", [
        {"account": ledger.acct(REMIT_ACCOUNT[kind]), "debit": amount, "memo": reference},
        {"account": cash_account, "credit": amount, "memo": reference},
    ], "payroll_remittance", run.id, reference or run.run_no, user_id, currency=currency)
    r = PayrollRemittance(run=run, type=kind, currency=currency, amount_cents=amount, paid_on=on_date,
                          account_id=cash_account.id, reference=reference, created_by=user_id)
    db.session.add(r)
    return r


def void(run, reason, user_id):
    if run.status == "void":
        raise ApiError("This payroll is already void")
    if run.status == "paid" or run.remittances:
        raise ApiError("Salaries or statutory payments have been made for this payroll, so it can't be voided. "
                       "Correct it through next month's payroll.", 409)
    if run.status == "approved":
        ledger.reverse_source(("payroll",), run.id, date.today(), reason)
    run.status, run.void_reason, run.voided_at = "void", reason, date.today()


def zimra_due_date(period):
    """PAYE (and NSSA) for a month are due by the 10th of the following month."""
    return month_start(month_add(period, 1)).replace(day=10)


# --------------------------------------------------------------------------- #
# Views & returns
# --------------------------------------------------------------------------- #
def payslip_dict(p, detail=False):
    st = p.staff
    d = {"id": p.id, "staff_id": st.id, "staff_no": st.staff_no, "name": st.name, "position": st.position,
         "basic": money(p.basic_cents), "gross": money(p.gross_cents), "benefits": money(p.benefits_cents),
         "taxable": money(p.taxable_cents), "nssa": money(p.nssa_cents), "pension": money(p.pension_cents),
         "medical_aid": money(p.medical_aid_cents), "credits": money(p.credits_cents), "paye": money(p.paye_cents),
         "aids_levy": money(p.aids_levy_cents), "total_tax": money(p.total_tax_cents),
         "other_deductions": money(p.other_deductions_cents), "net": money(p.net_cents),
         "overtime": money(p.overtime_cents), "bonus": money(p.bonus_cents),
         "extra_deduction": money(p.extra_deduction_cents), "extra_deduction_note": p.extra_deduction_note,
         "nssa_employer": money(p.nssa_employer_cents), "wcif": money(p.wcif_cents), "zimdef": money(p.zimdef_cents),
         "overtime_currency": p.overtime_currency or fx.base(), "bonus_currency": p.bonus_currency or fx.base(),
         "extra_deduction_currency": p.extra_deduction_currency or fx.base(),
         "tax_number": st.tax_number, "nssa_number": st.nssa_number, "national_id": st.national_id,
         "parts": [_part_dict(x) for x in p.currency_parts]}
    if detail:
        raw = json.loads(p.detail or "{}")
        tax_cur = raw.get("tax_currency") or fx.base()
        # Older payslips stored (name, cents); newer ones (name, cents, currency).
        conv = lambda rows, cur=tax_cur: [{"name": r[0], "amount": money(r[1]), "currency": r[2] if len(r) > 2 else cur}
                                          for r in rows]
        d.update(earnings=conv(raw.get("earnings", [])), deductions=conv(raw.get("deductions", [])),
                 benefit_items=conv(raw.get("benefits", [])), credit_items=conv(raw.get("credits", [])),
                 notes=raw.get("notes", []), insurable=money(raw.get("insurable", 0)),
                 exempt=money(p.exempt_cents), tax_before_credits=money(p.tax_before_credits_cents),
                 pension_deductible=money(raw.get("pension_deductible", 0)),
                 bank_name=st.bank_name, bank_account=st.bank_account, department=st.department,
                 period=p.run.period, pay_date=p.run.pay_date.isoformat(), run_no=p.run.run_no,
                 tax_currency=tax_cur, exchange_rate=p.run.exchange_rate,
                 exchange_rates={c: r for c, r in run_rates(p.run).items() if c != fx.ANCHOR}, ytd=year_to_date(p))
    return d


def _part_dict(x):
    return {"currency": x.currency, "gross": money(x.gross_cents), "nssa": money(x.nssa_cents),
            "paye": money(x.paye_cents), "aids_levy": money(x.aids_levy_cents), "pension": money(x.pension_cents),
            "medical_aid": money(x.medical_aid_cents), "other_deductions": money(x.other_deductions_cents),
            "net": money(x.net_cents)}


def year_to_date(slip):
    year = slip.run.period[:4]
    q = (Payslip.query.join(PayrollRun).filter(Payslip.staff_id == slip.staff_id, PayrollRun.status != "void",
                                               PayrollRun.period.like(f"{year}-%"),
                                               PayrollRun.period <= slip.run.period))
    rows = q.all()
    return {k: money(sum(getattr(s, f"{k}_cents") for s in rows))
            for k in ("gross", "taxable", "paye", "aids_levy", "nssa", "pension", "net")}


def run_dict(run, detail=False):
    t = totals(run)
    due = liabilities(run)
    ct = currency_totals(run)
    d = {"id": run.id, "run_no": run.run_no, "period": run.period, "pay_date": run.pay_date.isoformat(),
         "status": run.status, "notes": run.notes, "tax_table": run.tax_table.name,
         "prepared_by": run.creator.full_name if run.creator else None, "created_by": run.created_by,
         "approved_by": run.approver.full_name if run.approver else None,
         "approved_at": run.approved_at.isoformat() if run.approved_at else None,
         "paid_at": run.paid_at.isoformat() if run.paid_at else None,
         "void_reason": run.void_reason, "zimra_due": zimra_due_date(run.period).isoformat(),
         "tax_currency": json.loads(run.tax_table.config).get("currency", "USD"), "exchange_rate": run.exchange_rate,
         "exchange_rates": {c: r for c, r in run_rates(run).items() if c != fx.ANCHOR},
         "totals": {k: (v if k == "employees" else money(v)) for k, v in t.items()},
         "by_currency": [{"currency": c, **{k: money(v) for k, v in x.items()}} for c, x in ct.items()]}
    if detail:
        from ..models import Account
        d["payslips"] = [payslip_dict(p) for p in run.payslips]
        paid = {(r.type, fx.of(r)): r for r in run.remittances}
        d["remittances"] = [{"type": k, "currency": c, "label": REMIT_LABEL[k], "due": money(v),
                             "paid_on": paid[(k, c)].paid_on.isoformat() if (k, c) in paid else None,
                             "reference": paid[(k, c)].reference if (k, c) in paid else None,
                             "account": paid[(k, c)].account.name if (k, c) in paid else None}
                            for c, kinds in due.items() for k, v in kinds.items() if v or (k, c) in paid]
        accounts = json.loads(run.paid_accounts or "{}") or (
            {fx.base(): run.paid_from_account_id} if run.paid_from_account_id else {})
        d["paid_from"] = ", ".join(f"{c}: {db.session.get(Account, aid).name}" for c, aid in accounts.items()) or None
    return d


def p2_return(year):
    """ZIMRA P2 (monthly PAYE return) figures for each month of a year."""
    out = []
    for run in (PayrollRun.query.filter(PayrollRun.status.in_(("approved", "paid")),
                                        PayrollRun.period.like(f"{year}-%")).order_by(PayrollRun.period)):
        t = totals(run)
        # Tax withheld in each currency is remitted to ZIMRA in that currency.
        for cur, ct in currency_totals(run).items():
            if not ct["total_tax"] and cur != fx.base():
                continue
            zr = next((r for r in run.remittances if r.type == "zimra" and fx.of(r) == cur), None)
            out.append({"period": run.period, "run_no": run.run_no, "currency": cur, "employees": t["employees"],
                        "gross_remuneration": money(ct["gross"]), "paye": money(ct["paye"]),
                        "aids_levy": money(ct["aids_levy"]), "total_due": money(ct["total_tax"]),
                        "taxable": money(t["taxable"]), "tax_currency": json.loads(run.tax_table.config).get("currency", "USD"),
                        "due_date": zimra_due_date(run.period).isoformat(),
                        "remitted_on": zr.paid_on.isoformat() if zr else None,
                        "reference": zr.reference if zr else None,
                        "late": bool((zr.paid_on if zr else date.today()) > zimra_due_date(run.period)) and ct["total_tax"] > 0})
    return out


def annual_summary(year):
    """Per-employee totals for the tax year (for P6 tax certificates and the ITF16 return)."""
    rows = {}
    q = (Payslip.query.join(PayrollRun).filter(PayrollRun.status.in_(("approved", "paid")),
                                               PayrollRun.period.like(f"{year}-%")))
    for p in q:
        st = p.staff
        r = rows.setdefault(st.id, {"staff_no": st.staff_no, "name": st.name, "national_id": st.national_id,
                                    "tax_number": st.tax_number, "months": 0, **{k: 0 for k in (
                                        "gross", "benefits", "exempt", "nssa", "pension", "taxable",
                                        "credits", "paye", "aids_levy", "net")}})
        r["months"] += 1
        for k in ("gross", "benefits", "exempt", "nssa", "pension", "taxable", "credits", "paye", "aids_levy", "net"):
            r[k] += getattr(p, f"{k}_cents")
    out = []
    for r in sorted(rows.values(), key=lambda r: r["name"]):
        out.append({**{k: v for k, v in r.items() if isinstance(v, str) or v is None or k == "months"},
                    **{k: money(r[k]) for k in ("gross", "benefits", "exempt", "nssa", "pension", "taxable",
                                                "credits", "paye", "aids_levy", "net")},
                    "total_tax": money(r["paye"] + r["aids_levy"])})
    return out
