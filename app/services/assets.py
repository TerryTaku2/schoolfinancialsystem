"""Fixed asset register: acquisition, monthly depreciation and disposal.

Every movement posts to the general ledger so the register and the balance sheet agree:

    Purchase            Dr Property, Plant & Equipment   Cr Cash / Bank (refused if funds are short)
    Donation            Dr Property, Plant & Equipment   Cr Donations & Grants
    Existing asset      no posting (already in the opening balances)
    Depreciation run    Dr Depreciation                  Cr Accumulated Depreciation   (dated month end)
    Disposal            Dr Accumulated Depreciation, Dr Cash (proceeds)
                        Cr Property, Plant & Equipment, and the difference to Gain / Loss on Disposal

Depreciation conventions:
- A full month is charged from `depreciation_start` (normally the month of acquisition);
  no charge is made in the month of disposal.
- Straight line spreads (cost - brought-forward depreciation - residual value) evenly over
  the useful life remaining from `depreciation_start`, with exact cents (no rounding drift).
- Reducing balance charges rate% a year on the net book value at the start of each month.
- The net book value never falls below the residual value. Land is not depreciated.
- A run catches up any months an asset missed (e.g. an asset registered late), so
  months are never skipped, and runs must be made in date order.
"""
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .. import db
from ..models import AssetDepreciation, DepreciationRun, FixedAsset
from ..utils import ApiError, money
from . import currency as fx
from . import ledger

PPE, ACCUM_DEP, DEPRECIATION, DONATIONS, GAIN, LOSS = "1500", "1590", "5900", "4950", "4960", "5950"


# --------------------------------------------------------------------------- #
# Month helpers (periods are "YYYY-MM" strings)
# --------------------------------------------------------------------------- #
def period_of(d):
    return f"{d.year:04d}-{d.month:02d}"


def month_add(period, n):
    y, m = int(period[:4]), int(period[5:7])
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def months_between(a, b):
    """Whole months from period a to period b (b - a)."""
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def month_end(period):
    y, m = int(period[:4]), int(period[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def month_start(period):
    return date(int(period[:4]), int(period[5:7]), 1)


def parse_period(value, field="period"):
    s = str(value or "").strip()[:7]
    try:
        month_start(s)
        if len(s) != 7 or s[4] != "-":
            raise ValueError
    except (ValueError, IndexError):
        raise ApiError(f"Invalid {field}; expected YYYY-MM", fields={field: "Invalid month"})
    return s


def _round(x):
    return int(Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# Depreciation calculation
# --------------------------------------------------------------------------- #
def monthly_charges(asset, upto, nbv=None, after=None):
    """[(period, cents)] still to charge on `asset` for the months after `after`
    (default: its last charged month) up to and including `upto`."""
    if asset.method == "none" or asset.category == "Land":
        return []
    after = after if after is not None else asset.depreciated_to
    first = month_add(after, 1) if after else asset.depreciation_start
    if first < asset.depreciation_start:
        first = asset.depreciation_start
    nbv = asset.nbv_cents if nbv is None else nbv
    out, p = [], first
    while p <= upto:
        floor = nbv - asset.residual_cents
        if floor <= 0:
            break
        if asset.method == "straight_line":
            life = asset.useful_life_months or 0
            k = months_between(asset.depreciation_start, p) + 1
            if life <= 0 or k > life:
                break
            base = Decimal(asset.cost_cents - asset.opening_accum_cents - asset.residual_cents)
            charge = _round(base * k / life) - _round(base * (k - 1) / life)
        else:  # reducing balance
            charge = _round(Decimal(nbv) * Decimal(str(asset.rate_pct or 0)) / 1200)
        charge = min(charge, floor)
        if charge > 0:
            out.append((p, charge))
            nbv -= charge
        p = month_add(p, 1)
    return out


def projection(asset, months=12):
    """Next `months` charges from the last charged month, for the asset detail view."""
    if asset.status != "active":
        return []
    start = month_add(asset.depreciated_to, 1) if asset.depreciated_to else asset.depreciation_start
    return monthly_charges(asset, month_add(start, months - 1))


def latest_run():
    return (DepreciationRun.query.filter_by(reversed=False)
            .order_by(DepreciationRun.period.desc(), DepreciationRun.id.desc()).first())


def preview_run(period):
    """Charges a run for `period` would post: [(asset, [(period, cents)])]."""
    out = []
    end = month_end(period)
    for a in FixedAsset.query.filter_by(status="active").order_by(FixedAsset.asset_no):
        if a.acquisition_date > end:
            continue
        charges = monthly_charges(a, period)
        if charges:
            out.append((a, charges))
    return out


def run_depreciation(period, user_id):
    end = month_end(period)
    if end > date.today():
        raise ApiError(f"{period} has not ended yet. Depreciation is charged for completed months only.")
    last = latest_run()
    if last and period <= last.period:
        raise ApiError(f"Depreciation has already been run up to {last.period}. "
                       f"Run {month_add(last.period, 1)} or later.", 409)
    rows = preview_run(period)
    total = sum(c for _, charges in rows for _, c in charges)
    if not total:
        raise ApiError(f"There is no depreciation to charge for {period}")
    run = DepreciationRun(period=period, created_by=user_id)
    db.session.add(run)
    db.session.flush()
    # One entry per currency: assets are depreciated in the currency they were bought in.
    entries = {}
    for cur in sorted({fx.of(a) for a, _ in rows}):
        by_cat, n = {}, 0
        for a, charges in rows:
            if fx.of(a) == cur:
                by_cat[a.category] = by_cat.get(a.category, 0) + sum(c for _, c in charges)
                n += 1
        lines = [{"account": ledger.acct(DEPRECIATION), "debit": amt, "memo": cat} for cat, amt in sorted(by_cat.items())]
        lines.append({"account": ledger.acct(ACCUM_DEP), "credit": sum(by_cat.values()), "memo": f"{n} assets"})
        entries[cur] = ledger.post(end, f"Depreciation for {period} ({cur})", lines, "depreciation", run.id,
                                   f"DEP-{period}", user_id, currency=cur)
    run.entry_id = entries[fx.base()].id if fx.base() in entries else next(iter(entries.values())).id
    for a, charges in rows:
        db.session.add(AssetDepreciation(asset=a, run=run, entry_id=entries[fx.of(a)].id, from_period=charges[0][0],
                                         to_period=charges[-1][0], amount_cents=sum(c for _, c in charges)))
        a.depreciated_to = charges[-1][0]
    # Assets with nothing left to charge are still considered done for this month.
    for a in FixedAsset.query.filter(FixedAsset.status == "active", FixedAsset.method != "none",
                                     FixedAsset.category != "Land"):
        if a.acquisition_date <= end and (not a.depreciated_to or a.depreciated_to < period) \
                and a.depreciation_start <= period and not monthly_charges(a, period):
            a.depreciated_to = period
    db.session.flush()
    return run, total


def reverse_run(run, reason, user_id):
    last = latest_run()
    if run.reversed:
        raise ApiError("This run has already been reversed")
    if not last or last.id != run.id:
        raise ApiError("Only the most recent depreciation run can be reversed. Reverse later runs first.", 409)
    if any(l.asset.status != "active" for l in run.lines):
        raise ApiError("An asset in this run has since been disposed of, so the run cannot be reversed", 409)
    ledger.reverse_source(("depreciation",), run.id, date.today(), reason)
    run.reversed = True
    db.session.flush()
    # Roll each asset back to its last remaining charge. Recomputing from there is safe:
    # months with nothing to charge simply produce nothing again.
    for a in FixedAsset.query.filter_by(status="active"):
        if a.depreciated_to and a.depreciated_to >= run.period:
            kept = [d.to_period for d in a.depreciation if not (d.run and d.run.reversed)]
            a.depreciated_to = max(kept) if kept else None


# --------------------------------------------------------------------------- #
# Acquisition & disposal
# --------------------------------------------------------------------------- #
def post_acquisition(asset, cash_account=None, user_id=None):
    if asset.funding == "existing":
        return None
    if asset.funding == "purchase":
        if not cash_account or cash_account.subtype != "cash":
            raise ApiError("Choose the cash, bank or mobile money account the asset was paid from",
                           fields={"account_id": "Required"})
        if fx.of(cash_account) != fx.of(asset):
            raise ApiError(f"This asset costs {fx.of(asset)}; pay it from a {fx.of(asset)} account",
                           fields={"account_id": "Wrong currency"})
        available = ledger.balance(cash_account, asset.acquisition_date)
        if available < asset.cost_cents:
            raise ApiError(f"Insufficient funds in {cash_account.name}: balance on "
                           f"{asset.acquisition_date.isoformat()} is {money(available)}", 409)
        credit = cash_account
    else:
        credit = ledger.acct(DONATIONS)
    return ledger.post(asset.acquisition_date, f"Asset {asset.asset_no} acquired: {asset.name}", [
        {"account": ledger.acct(PPE), "debit": asset.cost_cents, "memo": asset.category},
        {"account": credit, "credit": asset.cost_cents, "memo": asset.supplier},
    ], "asset", asset.id, asset.asset_no, user_id, currency=fx.of(asset))


def dispose(asset, on_date, proceeds_cents, cash_account, note, user_id, write_off=False):
    if asset.status != "active":
        raise ApiError("This asset has already been disposed of")
    if on_date > date.today() or on_date < asset.acquisition_date:
        raise ApiError("The disposal date must be between the acquisition date and today", fields={"date": "Invalid"})
    month = period_of(on_date)
    if asset.depreciated_to and asset.depreciated_to >= month:
        raise ApiError(f"Depreciation has been charged up to {asset.depreciated_to}. Use a disposal date after "
                       f"that month, or reverse the depreciation run first.", 409)
    if proceeds_cents and (not cash_account or cash_account.subtype != "cash"):
        raise ApiError("Choose the account the sale proceeds were paid into", fields={"account_id": "Required"})
    if proceeds_cents and fx.of(cash_account) != fx.of(asset):
        raise ApiError(f"This asset is recorded in {fx.of(asset)}; receive the proceeds into a {fx.of(asset)} account. "
                       "A sale for another currency is recorded at its value in the asset's currency.",
                       fields={"account_id": "Wrong currency"})
    ledger.check_open(on_date)
    # Catch-up depreciation to the end of the month before disposal, as its own entry.
    catch_up = monthly_charges(asset, month_add(month, -1))
    if catch_up:
        amt = sum(c for _, c in catch_up)
        entry = ledger.post(on_date, f"Depreciation to disposal of {asset.asset_no}", [
            {"account": ledger.acct(DEPRECIATION), "debit": amt, "memo": asset.category},
            {"account": ledger.acct(ACCUM_DEP), "credit": amt, "memo": asset.asset_no},
        ], "asset", asset.id, asset.asset_no, user_id, currency=fx.of(asset))
        db.session.add(AssetDepreciation(asset=asset, entry_id=entry.id, from_period=catch_up[0][0],
                                         to_period=catch_up[-1][0], amount_cents=amt))
        asset.depreciated_to = catch_up[-1][0]
        db.session.flush()
    accum, nbv = asset.accumulated_cents, asset.nbv_cents
    lines = [{"account": ledger.acct(ACCUM_DEP), "debit": accum, "memo": asset.asset_no},
             {"account": ledger.acct(PPE), "credit": asset.cost_cents, "memo": asset.asset_no}]
    if proceeds_cents:
        lines.append({"account": cash_account, "debit": proceeds_cents, "memo": note})
    diff = proceeds_cents - nbv
    if diff > 0:
        lines.append({"account": ledger.acct(GAIN), "credit": diff, "memo": asset.name})
    elif diff < 0:
        lines.append({"account": ledger.acct(LOSS), "debit": -diff, "memo": asset.name})
    label = "written off" if write_off else "disposed of"
    ledger.post(on_date, f"Asset {asset.asset_no} {label}: {asset.name}", lines, "asset", asset.id,
                asset.asset_no, user_id, currency=fx.of(asset))
    asset.status = "written_off" if write_off else "disposed"
    asset.disposal_date, asset.disposal_proceeds_cents = on_date, proceeds_cents
    asset.disposal_note = note
    return diff


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def asset_dict(a, detail=False):
    d = {"id": a.id, "asset_no": a.asset_no, "name": a.name, "category": a.category, "serial_no": a.serial_no,
         "location": a.location, "custodian_id": a.custodian_id, "custodian": a.custodian.name if a.custodian else None,
         "supplier": a.supplier, "condition": a.condition, "notes": a.notes,
         "acquisition_date": a.acquisition_date.isoformat(), "funding": a.funding,
         "cost": money(a.cost_cents), "currency": fx.of(a), "residual": money(a.residual_cents), "method": a.method,
         "useful_life_months": a.useful_life_months, "rate_pct": a.rate_pct,
         "depreciation_start": a.depreciation_start, "opening_accum": money(a.opening_accum_cents),
         "depreciated_to": a.depreciated_to, "accumulated": money(a.accumulated_cents), "nbv": money(a.nbv_cents),
         "status": a.status, "disposal_date": a.disposal_date.isoformat() if a.disposal_date else None,
         "disposal_proceeds": money(a.disposal_proceeds_cents) if a.disposal_proceeds_cents is not None else None,
         "disposal_note": a.disposal_note}
    if detail:
        d["history"] = [{"from": h.from_period, "to": h.to_period, "amount": money(h.amount_cents),
                         "run_id": h.run_id, "reversed": bool(h.run and h.run.reversed),
                         "source": "Depreciation run" if h.run_id else "Disposal catch-up"} for h in a.depreciation]
        nbv = a.nbv_cents
        rows = []
        for p, c in projection(a):
            nbv -= c
            rows.append({"period": p, "charge": money(c), "nbv": money(nbv)})
        d["projection"] = rows
    return d


def reconciliation(currency=None):
    """Register totals against the ledger balances of PPE (1500) and accumulated depreciation (1590).

    Without a currency: the base-currency figures (top-level keys) plus one result per currency.
    """
    if currency is None:
        per = [reconciliation(c) for c in fx.enabled()]
        out = dict(per[0])
        out["by_currency"] = per
        out["reconciled"] = all(r["reconciled"] for r in per)
        return out
    active = [a for a in FixedAsset.query.filter_by(status="active") if fx.of(a) == currency]
    cost = sum(a.cost_cents for a in active)
    accum = sum(a.accumulated_cents for a in active)
    gl_cost = ledger.balance(ledger.acct(PPE), currency=currency)
    gl_accum = ledger.balance(ledger.acct(ACCUM_DEP), currency=currency)
    return {"currency": currency, "register_cost": money(cost), "ledger_cost": money(gl_cost),
            "cost_difference": money(gl_cost - cost),
            "register_accumulated": money(accum), "ledger_accumulated": money(gl_accum),
            "accumulated_difference": money(gl_accum - accum),
            "register_nbv": money(cost - accum), "ledger_nbv": money(gl_cost - gl_accum),
            "reconciled": cost == gl_cost and accum == gl_accum}
