"""Fixed asset register and depreciation runs."""
from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import or_

from .. import db
from ..models import (ASSET_CATEGORIES, DEPRECIATION_METHODS, Account, DepreciationRun, FixedAsset, Staff)
from ..services import assets as svc
from ..services import currency as fx
from ..services import ledger
from ..utils import (ApiError, audit, body, clean_str, get_or_404, money, next_number, parse_date, parse_float,
                     parse_int, require, permission_required, to_cents)

bp = Blueprint("assets", __name__)

CONDITIONS = ("New", "Good", "Fair", "Poor", "Unserviceable")
# Typical useful lives, offered as defaults in the form (months; None = not depreciated).
DEFAULT_LIFE = {"Land": None, "Buildings": 480, "Furniture & Fittings": 120, "Motor Vehicles": 60,
                "Computer Equipment": 36, "Office Equipment": 60, "Laboratory & Teaching Equipment": 84,
                "Sports Equipment": 60, "Other": 60}


@bp.get("/assets")
@permission_required("assets.view")
def list_assets():
    q = FixedAsset.query
    status = request.args.get("status", "active")
    if status != "all":
        q = q.filter_by(status=status)
    if request.args.get("category"):
        q = q.filter_by(category=request.args["category"])
    if request.args.get("q"):
        like = f"%{request.args['q'].strip()}%"
        q = q.filter(or_(FixedAsset.asset_no.ilike(like), FixedAsset.name.ilike(like),
                         FixedAsset.serial_no.ilike(like), FixedAsset.location.ilike(like)))
    if request.args.get("currency"):
        q = q.filter_by(currency=request.args["currency"])
    rows = q.order_by(FixedAsset.asset_no).all()
    by_cat = {}
    for a in rows:
        key = (a.category, fx.of(a))
        c = by_cat.setdefault(key, {"category": a.category, "currency": fx.of(a), "count": 0, "cost": 0,
                                    "accumulated": 0, "nbv": 0})
        c["count"] += 1
        c["cost"] += a.cost_cents
        c["accumulated"] += a.accumulated_cents
        c["nbv"] += a.nbv_cents
    summary = [{**c, "cost": money(c["cost"]), "accumulated": money(c["accumulated"]), "nbv": money(c["nbv"])}
               for c in sorted(by_cat.values(), key=lambda c: (c["currency"], c["category"]))]
    last = svc.latest_run()
    return jsonify(items=[svc.asset_dict(a) for a in rows], summary=summary, categories=ASSET_CATEGORIES,
                   conditions=CONDITIONS, default_life=DEFAULT_LIFE, reconciliation=svc.reconciliation(),
                   last_run=last.period if last else None)


@bp.get("/assets/<int:aid>")
@permission_required("assets.view")
def get_asset(aid):
    return jsonify(svc.asset_dict(get_or_404(FixedAsset, aid, "Asset"), detail=True))


def _apply_descriptive(a, data):
    a.name = clean_str(data["name"], 120)
    a.serial_no = clean_str(data.get("serial_no"), 60)
    a.location = clean_str(data.get("location"), 80)
    a.supplier = clean_str(data.get("supplier"), 100)
    a.notes = clean_str(data.get("notes"), 300)
    cond = data.get("condition") or "Good"
    if cond not in CONDITIONS:
        raise ApiError("Invalid condition", fields={"condition": "Invalid"})
    a.condition = cond
    cid = parse_int(data.get("custodian_id"), "custodian_id", required=False)
    a.custodian_id = get_or_404(Staff, cid, "Staff member").id if cid else None


@bp.post("/assets")
@permission_required("assets.manage")
def create_asset():
    data = body()
    require(data, "name", "category", "acquisition_date", "cost", "funding")
    if data["category"] not in ASSET_CATEGORIES:
        raise ApiError("Invalid category", fields={"category": "Invalid"})
    acquired = parse_date(data["acquisition_date"], "acquisition_date")
    if acquired > date.today():
        raise ApiError("The acquisition date cannot be in the future", fields={"acquisition_date": "Future date"})
    funding = data["funding"]
    if funding not in ("purchase", "donation", "existing"):
        raise ApiError("Invalid funding", fields={"funding": "Invalid"})
    cost = to_cents(data["cost"], "cost")
    residual = to_cents(data.get("residual") or 0, "residual", allow_zero=True)
    if residual > cost:
        raise ApiError("Residual value cannot exceed cost", fields={"residual": "Too high"})
    method = "none" if data["category"] == "Land" else (data.get("method") or "straight_line")
    if method not in DEPRECIATION_METHODS:
        raise ApiError("Invalid depreciation method", fields={"method": "Invalid"})
    life = rate = None
    if method == "straight_line":
        life = parse_int(data.get("useful_life_months"), "useful_life_months", minimum=1, maximum=1200)
    elif method == "reducing_balance":
        rate = parse_float(data.get("rate_pct"), "rate_pct", minimum=0.1, maximum=100)
    opening = 0
    start = svc.period_of(acquired)
    if funding == "existing":
        opening = to_cents(data.get("opening_accum") or 0, "opening_accum", allow_zero=True)
        if opening > cost - residual:
            raise ApiError("Depreciation brought forward cannot exceed cost less residual value",
                           fields={"opening_accum": "Too high"})
        if data.get("depreciation_start"):
            start = svc.parse_period(data["depreciation_start"], "depreciation_start")
            if start < svc.period_of(acquired):
                raise ApiError("Depreciation cannot start before the asset was acquired",
                               fields={"depreciation_start": "Too early"})
    a = FixedAsset(asset_no=next_number("asset", "FA-", 5), category=data["category"], acquisition_date=acquired,
                   currency=fx.pick(data.get("currency")),
                   funding=funding, cost_cents=cost, residual_cents=residual, method=method,
                   useful_life_months=life, rate_pct=rate, depreciation_start=start, opening_accum_cents=opening,
                   created_by=current_user.id)
    _apply_descriptive(a, data)
    db.session.add(a)
    db.session.flush()
    cash = None
    if funding == "purchase":
        aid = parse_int(data.get("account_id"), "account_id", required=False)
        cash = get_or_404(Account, aid, "Account") if aid else None
    svc.post_acquisition(a, cash, current_user.id)
    audit("create", "asset", a.id, f"{a.asset_no} {a.name} {money(cost)} ({funding})")
    db.session.commit()
    return jsonify(svc.asset_dict(a, detail=True)), 201


@bp.put("/assets/<int:aid>")
@permission_required("assets.manage")
def update_asset(aid):
    """Descriptive details only; cost and depreciation settings are fixed once posted."""
    a = get_or_404(FixedAsset, aid, "Asset")
    data = body()
    require(data, "name")
    _apply_descriptive(a, data)
    audit("update", "asset", a.id, a.asset_no)
    db.session.commit()
    return jsonify(svc.asset_dict(a, detail=True))


@bp.post("/assets/<int:aid>/cancel")
@permission_required("assets.approve")
def cancel_asset(aid):
    """Cancel a registration made in error: allowed only before any depreciation, and reverses the acquisition."""
    a = get_or_404(FixedAsset, aid, "Asset")
    if a.depreciation or a.status != "active":
        raise ApiError("This asset has depreciation or a disposal on record. Dispose of or write it off instead.", 409)
    reason = clean_str(body().get("reason"), 150)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required", fields={"reason": "Required"})
    ledger.reverse_source(("asset",), a.id, date.today(), f"Registration cancelled: {reason}")
    audit("delete", "asset", a.id, f"{a.asset_no} {a.name}: {reason}")
    db.session.delete(a)
    db.session.commit()
    return jsonify(ok=True)


@bp.post("/assets/<int:aid>/dispose")
@permission_required("assets.manage")
def dispose_asset(aid):
    a = get_or_404(FixedAsset, aid, "Asset")
    data = body()
    require(data, "date")
    write_off = bool(data.get("write_off"))
    proceeds = 0 if write_off else to_cents(data.get("proceeds") or 0, "proceeds", allow_zero=True)
    cash = None
    if proceeds:
        cash = get_or_404(Account, parse_int(data.get("account_id"), "account_id"), "Account")
    note = clean_str(data.get("note"), 200)
    if not note:
        raise ApiError("Describe the disposal (buyer, reason, board approval...)", fields={"note": "Required"})
    diff = svc.dispose(a, parse_date(data["date"], "date"), proceeds, cash, note, current_user.id, write_off)
    audit("write_off" if write_off else "dispose", "asset", a.id,
          f"{a.asset_no} proceeds {money(proceeds)} gain/(loss) {money(diff)}")
    db.session.commit()
    return jsonify(svc.asset_dict(a, detail=True))


# --------------------------------------------------------------------------- #
# Depreciation runs
# --------------------------------------------------------------------------- #
def _by_currency(pairs):
    out = {}
    for cur, cents in pairs:
        out[cur] = out.get(cur, 0) + cents
    return {c: money(v) for c, v in sorted(out.items())}


def run_dict(r):
    amounts = _by_currency((fx.of(l.asset), l.amount_cents) for l in r.lines)
    return {"id": r.id, "period": r.period, "entry_no": r.entry.entry_no if r.entry else None,
            "entry_id": r.entry_id, "amount": amounts.get(fx.base(), 0), "amounts": amounts, "assets": len(r.lines),
            "reversed": r.reversed, "by": r.author.full_name if r.author else None,
            "created_at": r.created_at.isoformat()}


@bp.get("/assets/depreciation")
@permission_required("assets.view")
def list_runs():
    runs = DepreciationRun.query.order_by(DepreciationRun.period.desc(), DepreciationRun.id.desc()).all()
    last = svc.latest_run()
    today = date.today()
    # Suggest the next month to run: after the last run, else the last completed month.
    last_complete = svc.month_add(svc.period_of(today), -1)
    suggested = svc.month_add(last.period, 1) if last else last_complete
    return jsonify(items=[run_dict(r) for r in runs], next_period=suggested, up_to_date=suggested > last_complete,
                   last_complete=last_complete, last_run=last.period if last else None)


@bp.get("/assets/depreciation/preview")
@permission_required("assets.view")
def preview():
    period = svc.parse_period(request.args.get("period"))
    rows = svc.preview_run(period)
    items = [{"asset_no": a.asset_no, "name": a.name, "category": a.category, "currency": fx.of(a),
              "from": c[0][0], "to": c[-1][0], "months": len(c), "amount": money(sum(x for _, x in c)),
              "nbv_after": money(a.nbv_cents - sum(x for _, x in c))} for a, c in rows]
    totals = _by_currency((fx.of(a), sum(x for _, x in c)) for a, c in rows)
    return jsonify(period=period, items=items, total=totals.get(fx.base(), 0), totals=totals)


@bp.post("/assets/depreciation")
@permission_required("assets.manage")
def run_depreciation():
    period = svc.parse_period(body().get("period"))
    run, total = svc.run_depreciation(period, current_user.id)
    audit("create", "depreciation", run.id, f"{period} {run_dict(run)['amounts']}")
    db.session.commit()
    return jsonify(run_dict(run)), 201


@bp.post("/assets/depreciation/<int:rid>/reverse")
@permission_required("assets.approve")
def reverse_run(rid):
    run = get_or_404(DepreciationRun, rid, "Depreciation run")
    reason = clean_str(body().get("reason"), 150)
    if not reason or len(reason) < 5:
        raise ApiError("A reason (5+ characters) is required", fields={"reason": "Required"})
    svc.reverse_run(run, reason, current_user.id)
    audit("reverse", "depreciation", run.id, f"{run.period}: {reason}")
    db.session.commit()
    return jsonify(run_dict(run))
