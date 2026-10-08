"""Any number of currencies side by side, each recorded exactly as it happened.

- A school works in its own currency (the base, e.g. USD or ZWG) and can add any of the
  currencies used in Zimbabwe's multi-currency system (Settings -> School): ZWG, USD, ZAR,
  BWP, GBP, EUR and others. Every money transaction is then in one of the school's currencies.
- Nothing is converted when it is recorded. A ZAR receipt is ZAR cash and clears ZAR
  invoices; a USD expense is paid from a USD account. Each currency has its own cash on hand,
  bank and mobile money accounts, its own student balances and its own complete set of
  books (every journal entry is in one currency, so each currency's trial balance, balance
  sheet and cash flow balance on their own).
- The bursar records each day's rates against the US dollar (units of the currency per
  1 USD, as the RBZ quotes them), so any pair converts through USD. Rates are used only to
  show combined totals in the base currency, and for PAYE when a salary is paid in more than
  one currency (ZIMRA taxes the total, then the tax is paid in each currency in proportion).
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from flask import g, has_app_context
from sqlalchemy import text

from .. import db
from ..models import Account, ExchangeRate, JournalEntry, Setting
from ..utils import ApiError

# Currencies a school can use: Zimbabwe's own, the multi-currency basket and regional neighbours.
CATALOG = {
    "USD": "US dollar",
    "ZWG": "Zimbabwe Gold (ZiG)",
    "ZAR": "South African rand",
    "BWP": "Botswana pula",
    "GBP": "British pound",
    "EUR": "Euro",
    "CNY": "Chinese yuan",
    "INR": "Indian rupee",
    "JPY": "Japanese yen",
    "AUD": "Australian dollar",
    "ZMW": "Zambian kwacha",
    "MZN": "Mozambican metical",
}
CURRENCIES = tuple(CATALOG)
LABEL = CATALOG
ANCHOR = "USD"  # rates are quoted as units of a currency per 1 US dollar
# Cash-type accounts (cash on hand, bank, mobile money) in the base currency. Each other currency
# gets its own copy with a code in the same block: 1001-1009, 1011-1019, 1021-1029.
CASH_FAMILIES = ("1000", "1010", "1020")


def base():
    from .structure import currency
    c = currency()
    return c if c in CURRENCIES else "USD"


def enabled():
    """The school's currencies, base first."""
    s = db.session.get(Setting, "currencies")
    values = [c for c in (s.value or "").split(",") if c in CURRENCIES] if s else []
    b = base()
    return [b] + [c for c in CURRENCIES if c in values and c != b]


def is_multi():
    return len(enabled()) > 1


is_dual = is_multi  # older name, kept for callers from the two-currency version


def in_use(cur):
    """Whether anything has been recorded in `cur` (it can't be removed then)."""
    if JournalEntry.query.filter_by(currency=cur).first():
        return True
    for table in _LEGACY_TABLES:
        if db.session.execute(text(f'SELECT 1 FROM "{table}" WHERE currency = :c LIMIT 1'), {"c": cur}).first():
            return True
    return bool(db.session.execute(text('SELECT 1 FROM "staff" WHERE salary_currency = :c LIMIT 1'), {"c": cur}).first())


def set_enabled(codes):
    """Choose the school's currencies. The base is always included; a currency with records can't be removed."""
    codes = [str(c or "").strip().upper() for c in (codes or [])]
    bad = [c for c in codes if c not in CURRENCIES]
    if bad:
        raise ApiError(f"Unknown currency: {', '.join(bad)}", fields={"currencies": "Invalid"})
    b = base()
    wanted = [b] + [c for c in CURRENCIES if c in codes and c != b]
    removed = [c for c in enabled() if c not in wanted]
    for c in removed:
        if in_use(c):
            raise ApiError(f"There are {c} transactions on record, so {c} can't be removed.", 409)
    s = db.session.get(Setting, "currencies") or Setting(key="currencies")
    s.value = ",".join(wanted)
    db.session.add(s)
    ensure_accounts()
    return wanted, removed


def set_dual(on):
    """Two-currency switch from the earlier version: base plus USD/ZWG, or the base alone."""
    b = base()
    return set_enabled([b, "ZWG" if b == "USD" else "USD"] if on else [b])


def pick(value, field="currency"):
    """The currency for a new transaction: as given, or the base currency."""
    cur = (value or "").strip().upper() or base()
    if cur not in CURRENCIES:
        raise ApiError(f"Unknown currency {cur}", fields={field: "Invalid"})
    if cur not in enabled():
        raise ApiError(f"{cur} isn't one of this school's currencies. An administrator can add it in "
                       "Settings → School → Currencies.", fields={field: "Not enabled"})
    return cur


def of(obj, attr="currency"):
    return getattr(obj, attr, None) or base()


# --------------------------------------------------------------------------- #
# Exchange rates (units of each currency per 1 USD)
# --------------------------------------------------------------------------- #
def rate_currencies():
    """The school's currencies that need a daily rate: all of them except USD itself."""
    return [c for c in enabled() if c != ANCHOR]


def rate_on(cur="ZWG", on=None):
    """The latest rate for `cur` on or before `on`."""
    return (ExchangeRate.query.filter(ExchangeRate.currency == cur, ExchangeRate.date <= (on or date.today()))
            .order_by(ExchangeRate.date.desc()).first())


def latest_rates(on=None):
    """{currency: ExchangeRate} for each of the school's currencies that has a rate on or before `on`."""
    out = {}
    for c in rate_currencies():
        r = rate_on(c, on)
        if r:
            out[c] = r
    return out


def per_usd(cur, on=None, required=True):
    """Units of `cur` for 1 USD on `on` (Decimal), or None when no rate has been recorded."""
    if cur == ANCHOR:
        return Decimal(1)
    r = rate_on(cur, on)
    if r is None:
        if required:
            raise ApiError(f"No {cur} exchange rate has been entered on or before "
                           f"{(on or date.today()).isoformat()}. The bursar records it under Exchange rates "
                           f"({cur} per 1 USD).", 409)
        return None
    return Decimal(str(r.per_usd))


def factor(frm, to, on=None, required=True):
    """Multiplier turning an amount in `frm` into `to`, through USD at the rates on or before `on`."""
    if frm == to:
        return Decimal(1)
    a, b = per_usd(frm, on, required), per_usd(to, on, required)
    if a is None or b is None:
        return None
    return b / a


def convert(cents, frm, to, on=None, required=True):
    f = factor(frm, to, on, required)
    if f is None:
        return None
    return int((Decimal(cents) * f).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def in_base(by_currency, on=None):
    """{currency: cents} -> total cents in the base currency, or None when a rate is missing."""
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
    d = {"id": r.id, "date": r.date.isoformat(), "currency": r.currency, "per_usd": r.per_usd,
         "source": r.source, "entered_by": r.author.full_name if r.author else None}
    if r.currency == "ZWG":
        d["zwg_per_usd"] = r.per_usd  # name used by the two-currency version
    return d


def catalog():
    return [{"code": c, "label": CATALOG[c]} for c in CURRENCIES]


# --------------------------------------------------------------------------- #
# Translation context for combined statements
# --------------------------------------------------------------------------- #
class translation:
    """`with translation(on):` makes ledger figures for currency "ALL" use that date's rates."""

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
def _family_codes(code):
    return [str(int(code) + i) for i in range(1, 10)]


def _copy_of(code, cur):
    return (Account.query.filter(Account.code.in_(_family_codes(code)), Account.currency == cur,
                                 Account.subtype == "cash", Account.is_system.is_(True)).first())


def cash_account(code, cur):
    """The cash/bank/mobile account `code` (base-currency code) in currency `cur`."""
    from .ledger import acct
    if cur == base():
        return acct(code)
    if code not in CASH_FAMILIES:
        raise ApiError(f"There is no {cur} version of account {code}")
    a = _copy_of(code, cur)
    if a is None:
        ensure_accounts(extra=[cur])
        a = _copy_of(code, cur)
    if a is None:
        raise ApiError(f"Account {code} has no free code left for a {cur} version", 500)
    return a


def ensure_accounts(extra=()):
    """Tag cash accounts with their currency and give every school currency its own cash accounts."""
    b = base()
    for a in Account.query.filter_by(subtype="cash", currency=None):
        a.currency = b
    others = [c for c in list(enabled()) + list(extra) if c != b]
    for code in CASH_FAMILIES:
        src = Account.query.filter_by(code=code).first()
        if src is None:
            continue
        name = src.name.replace(f" ({b})", "")
        for cur in dict.fromkeys(others):
            if _copy_of(code, cur):
                continue
            taken = {a.code for a in Account.query.filter(Account.code.in_(_family_codes(code)))}
            free = next((c for c in _family_codes(code) if c not in taken), None)
            if free is None:
                continue
            db.session.add(Account(code=free, name=f"{name} ({cur})", type="asset", subtype="cash",
                                   cash_flow="operating", currency=cur, is_system=True,
                                   description=f"{CATALOG[cur]} held as {name.lower()}"))
            db.session.flush()
        if others and not src.name.endswith(f"({b})"):
            src.name = f"{src.name} ({b})"
    db.session.flush()


_LEGACY_TABLES = ("journal_entry", "fee_item", "invoice", "payment", "expense", "fixed_asset", "staff_pay_item",
                  "payroll_remittance")


def migrate():
    """Give records from before multi-currency the school's currency. Idempotent."""
    b = base()
    for table in _LEGACY_TABLES:
        db.session.execute(text(f'UPDATE "{table}" SET currency = :c WHERE currency IS NULL'), {"c": b})
    db.session.execute(text('UPDATE "staff" SET salary_currency = :c WHERE salary_currency IS NULL'), {"c": b})
    # Rates from the two-currency version were all ZWG per USD.
    db.session.execute(text('UPDATE "exchange_rate" SET currency = \'ZWG\' WHERE currency IS NULL'))
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
