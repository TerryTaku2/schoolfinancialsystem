"""USD and ZWG side by side, each recorded exactly as it happened.

- A school works in its own currency (the base: USD or ZWG). Turning on dual currency
  (Settings -> School) lets every money transaction be in either currency.
- Nothing is converted when it is recorded. A ZWG receipt is ZWG cash and clears ZWG
  invoices; a USD expense is paid from a USD account. Each currency has its own cash on hand,
  bank and mobile money accounts, its own student balances and its own complete set of
  books (every journal entry is in one currency, so each currency's trial balance, balance
  sheet and cash flow balance on their own).
- The bursar records the day's exchange rate (ZWG per 1 USD). It is used only to show
  combined totals in the base currency, and for PAYE when a salary is paid partly in each
  currency (ZIMRA taxes the total, then the tax is paid in each currency in proportion).
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from flask import g, has_app_context
from sqlalchemy import text

from .. import db
from ..models import Account, ExchangeRate, Setting
from ..utils import ApiError

CURRENCIES = ("USD", "ZWG")
LABEL = {"USD": "US dollars", "ZWG": "Zimbabwe Gold (ZiG)"}
# Cash-type accounts and their counterparts in the second currency.
CASH_MIRRORS = {"1000": "1001", "1010": "1011", "1020": "1021"}


def base():
    from .structure import currency
    c = currency()
    return c if c in CURRENCIES else "USD"


def other(cur):
    return "ZWG" if cur == "USD" else "USD"


def enabled():
    s = db.session.get(Setting, "currencies")
    values = [c for c in (s.value or "").split(",") if c in CURRENCIES] if s else []
    b = base()
    return [b] + [c for c in values if c != b]


def is_dual():
    return len(enabled()) > 1


def set_dual(on):
    s = db.session.get(Setting, "currencies") or Setting(key="currencies")
    s.value = ",".join(CURRENCIES if on else [base()])
    db.session.add(s)
    ensure_accounts()


def pick(value, field="currency"):
    """The currency for a new transaction: as given, or the base currency."""
    cur = (value or "").strip().upper() or base()
    if cur not in CURRENCIES:
        raise ApiError("Currency must be USD or ZWG", fields={field: "Invalid"})
    if cur not in enabled():
        raise ApiError(f"This school only works in {base()}. Turn on dual currency in Settings to record {cur}.",
                       fields={field: "Not enabled"})
    return cur


def of(obj, attr="currency"):
    return getattr(obj, attr, None) or base()


# --------------------------------------------------------------------------- #
# Exchange rates
# --------------------------------------------------------------------------- #
def rate_on(on=None):
    return (ExchangeRate.query.filter(ExchangeRate.date <= (on or date.today()))
            .order_by(ExchangeRate.date.desc()).first())


def factor(frm, to, on=None, required=True):
    """Multiplier turning an amount in `frm` into `to`, using the rate on or before `on`."""
    if frm == to:
        return Decimal(1)
    r = rate_on(on)
    if r is None:
        if required:
            raise ApiError(f"No USD/ZWG exchange rate has been entered on or before "
                           f"{(on or date.today()).isoformat()}. The bursar records it under Exchange rates.", 409)
        return None
    rate = Decimal(str(r.zwg_per_usd))
    return rate if (frm, to) == ("USD", "ZWG") else 1 / rate


def convert(cents, frm, to, on=None, required=True):
    f = factor(frm, to, on, required)
    if f is None:
        return None
    return int((Decimal(cents) * f).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def in_base(by_currency, on=None):
    """{currency: cents} -> total cents in the base currency, or None when no rate is available."""
    total = 0
    for cur, cents in by_currency.items():
        if not cents:
            continue
        v = convert(cents, cur, base(), on, required=False)
        if v is None:
            return None
        total += v
    return total


def rate_dict(r):
    return {"id": r.id, "date": r.date.isoformat(), "zwg_per_usd": r.zwg_per_usd, "source": r.source,
            "entered_by": r.author.full_name if r.author else None}


# --------------------------------------------------------------------------- #
# Translation context for combined statements
# --------------------------------------------------------------------------- #
class translation:
    """`with translation(on):` makes ledger figures for currency "ALL" use that date's rate."""

    def __init__(self, on):
        self.on = on

    def __enter__(self):
        self.prev = g.get("_translate_on") if has_app_context() else None
        g._translate_on = self.on
        return self

    def __exit__(self, *exc):
        g._translate_on = self.prev


def translate_on():
    return (g.get("_translate_on") if has_app_context() else None) or date.today()


# --------------------------------------------------------------------------- #
# Accounts and upgrades
# --------------------------------------------------------------------------- #
def cash_account(code, cur):
    """The cash/bank/mobile account `code` (base-currency code) in currency `cur`."""
    from .ledger import acct
    if cur == base():
        return acct(code)
    mirror = CASH_MIRRORS.get(code)
    if mirror is None:
        raise ApiError(f"There is no {cur} version of account {code}")
    a = Account.query.filter_by(code=mirror).first()
    if a is None:
        ensure_accounts(force=True)
        a = acct(mirror)
    return a


def ensure_accounts(force=False):
    """Tag cash accounts with their currency and create the second currency's cash accounts."""
    b = base()
    for a in Account.query.filter_by(subtype="cash", currency=None):
        a.currency = b
    if not (force or is_dual()):
        db.session.flush()
        return
    o = other(b)
    for code, mirror in CASH_MIRRORS.items():
        src = Account.query.filter_by(code=code).first()
        if src is None or Account.query.filter_by(code=mirror).first():
            continue
        name = src.name.replace(f" ({b})", "")
        db.session.add(Account(code=mirror, name=f"{name} ({o})", type="asset", subtype="cash", cash_flow="operating",
                               currency=o, is_system=True, description=f"{LABEL[o]} held as {name.lower()}"))
        if not src.name.endswith(f"({b})"):
            src.name = f"{src.name} ({b})"
    db.session.flush()


_LEGACY_TABLES = ("journal_entry", "fee_item", "invoice", "payment", "expense", "fixed_asset", "staff_pay_item",
                  "payroll_remittance")


def migrate():
    """Give records from before dual currency the school's currency. Idempotent."""
    b = base()
    for table in _LEGACY_TABLES:
        db.session.execute(text(f'UPDATE "{table}" SET currency = :c WHERE currency IS NULL'), {"c": b})
    db.session.execute(text('UPDATE "staff" SET salary_currency = :c WHERE salary_currency IS NULL'), {"c": b})
    ensure_accounts()
    from ..models import Payslip, PayslipCurrency
    if Payslip.query.first() and not PayslipCurrency.query.first():
        for slip in Payslip.query:
            db.session.add(PayslipCurrency(
                payslip=slip, currency=b, gross_cents=slip.gross_cents, nssa_cents=slip.nssa_cents,
                paye_cents=slip.paye_cents, aids_levy_cents=slip.aids_levy_cents, pension_cents=slip.pension_cents,
                medical_aid_cents=slip.medical_aid_cents, other_deductions_cents=slip.other_deductions_cents,
                net_cents=slip.net_cents, nssa_employer_cents=slip.nssa_employer_cents, wcif_cents=slip.wcif_cents,
                zimdef_cents=slip.zimdef_cents))
    db.session.flush()
