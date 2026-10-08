"""Bank reconciliation: prove a bank, mobile money or cash account in the books against its statement.

For one account and a statement date:

    Balance per bank statement (closing)
    + deposits in the books not yet on a statement      ("outstanding deposits")
    - payments in the books not yet on a statement      ("unpresented payments")
    = adjusted bank balance, which must equal the balance per books on that date.

Working a statement:
- Statement lines (money in positive, money out negative) are entered or imported from the bank's CSV.
- Each line is matched to the book transaction it represents: automatically (same amount, date within a
  week, reference preferred) or by hand.
- Items only the bank knows about (bank charges, interest, a deposit that was never receipted) are
  posted from the reconciliation with one click and matched straight away. Fee receipts are recorded
  under Payments as usual, so the student's account is credited, and then matched.
- The statement can be completed only when every line is matched, its lines add up from the opening to
  the closing balance, and the difference above is nil. Completing marks the matched book lines as
  cleared, so the next reconciliation starts from what is still outstanding.
- A receipt and its void cancel each other out and are cleared together.
- An account's first reconciliation has a start date: book transactions before it are already in the
  statement's opening balance, so they are brought forward and cleared with it rather than listed as
  outstanding. If the books and the bank disagree at that point the difference still shows.
- Only the latest completed statement of an account can be reopened.

Amounts are in the account's own currency (each cash account holds one currency).
"""
from datetime import date

from .. import db
from ..models import BankStatement, BankStatementLine, JournalEntry, JournalLine
from ..utils import ApiError, audit, money
from . import currency as fx
from . import ledger

MATCH_WINDOW_DAYS = 7


def bank_accounts():
    return [a for a in ledger.cash_accounts() if a.active]


def signed(line):
    """Effect of a book line on the bank balance: money in positive, money out negative."""
    return line.debit_cents - line.credit_cents


def last_completed(account):
    return (BankStatement.query.filter_by(account_id=account.id, status="completed")
            .order_by(BankStatement.statement_date.desc(), BankStatement.id.desc()).first())


def draft_for(account):
    return BankStatement.query.filter_by(account_id=account.id, status="draft").first()


def _uncleared(account, upto):
    return (JournalLine.query.join(JournalEntry)
            .filter(JournalLine.account_id == account.id, JournalLine.cleared_statement_id.is_(None),
                    JournalEntry.date <= upto)
            .order_by(JournalEntry.date, JournalLine.id).all())


def _self_cancelling(lines):
    """Lines whose entry and its reversal are both uncleared here: together they never touch the bank."""
    by_entry = {l.entry_id: l for l in lines}
    pairs = set()
    for l in lines:
        orig = l.entry.reversal_of_id
        if orig and orig in by_entry and signed(by_entry[orig]) == -signed(l):
            pairs |= {l.id, by_entry[orig].id}
    return pairs


def outstanding(statement):
    """Book lines on the account up to the statement date that this statement hasn't matched.

    Returns (outstanding lines, ids cleared alongside this statement without a statement line:
    self-cancelling pairs and, on a first reconciliation, lines brought forward before its start)."""
    lines = _uncleared(statement.account, statement.statement_date)
    matched = {l.journal_line_id for l in statement.lines if l.journal_line_id}
    automatic = _self_cancelling(lines)
    if statement.start_date:
        automatic |= {l.id for l in lines if l.entry.date < statement.start_date and l.id not in matched}
    return [l for l in lines if l.id not in matched and l.id not in automatic], automatic


def summary(statement):
    acc = statement.account
    items, _ = outstanding(statement)
    deposits = sum(signed(l) for l in items if signed(l) > 0)
    payments = -sum(signed(l) for l in items if signed(l) < 0)
    books = ledger.balance(acc, statement.statement_date)
    adjusted = statement.closing_cents + deposits - payments
    lines_total = sum(l.amount_cents for l in statement.lines)
    unmatched = sum(1 for l in statement.lines if not l.journal_line_id)
    prev = last_completed(acc)
    from datetime import timedelta
    brought = (ledger.balance(acc, statement.start_date - timedelta(days=1)) if statement.start_date else None)
    return {
        "currency": fx.of(acc),
        "start_date": statement.start_date.isoformat() if statement.start_date else None,
        "books_at_start": money(brought) if brought is not None else None,
        "opening": money(statement.opening_cents), "closing": money(statement.closing_cents),
        "lines_total": money(lines_total),
        "lines_balance": statement.opening_cents + lines_total == statement.closing_cents,
        "lines_difference": money(statement.closing_cents - statement.opening_cents - lines_total),
        "outstanding_deposits": money(deposits), "unpresented_payments": money(payments),
        "adjusted_bank": money(adjusted), "book_balance": money(books),
        "difference": money(books - adjusted), "unmatched_lines": unmatched,
        "previous_closing": money(prev.closing_cents) if prev and prev.id != statement.id else None,
        "opening_continues": (not prev or prev.id == statement.id or prev.closing_cents == statement.opening_cents),
        "can_complete": statement.status == "draft" and unmatched == 0 and books == adjusted
        and statement.opening_cents + lines_total == statement.closing_cents,
    }


# --------------------------------------------------------------------------- #
# Statements and lines
# --------------------------------------------------------------------------- #
def create_statement(account, statement_date, opening_cents, closing_cents, reference, user_id, start_date=None):
    if account.subtype != "cash":
        raise ApiError("Only cash, bank and mobile money accounts can be reconciled")
    if statement_date > date.today():
        raise ApiError("The statement date can't be in the future", fields={"statement_date": "Future date"})
    if draft_for(account):
        raise ApiError(f"{account.name} already has a reconciliation in progress. Finish or delete it first.", 409)
    prev = last_completed(account)
    if prev and statement_date <= prev.statement_date:
        raise ApiError(f"{account.name} is reconciled up to {prev.statement_date.isoformat()}. "
                       "The next statement must end after that date.", fields={"statement_date": "Too early"})
    if start_date and (prev or start_date > statement_date):
        raise ApiError("A start date is only used on an account's first reconciliation, on or before the statement date",
                       fields={"start_date": "Invalid"})
    s = BankStatement(account=account, statement_date=statement_date, opening_cents=opening_cents,
                      closing_cents=closing_cents, reference=reference, created_by=user_id, start_date=start_date)
    db.session.add(s)
    db.session.flush()
    audit("create", "bank_statement", s.id, f"{account.name} to {statement_date.isoformat()}")
    return s


def _draft(statement):
    if statement.status != "draft":
        raise ApiError("This reconciliation is completed. Reopen it to make changes.", 409)


def add_lines(statement, rows):
    _draft(statement)
    added = []
    for i, r in enumerate(rows):
        if r["date"] > statement.statement_date:
            raise ApiError(f"Line {i + 1} is dated after the statement date")
        line = BankStatementLine(statement=statement, date=r["date"], description=r["description"],
                                 reference=r.get("reference"), amount_cents=r["amount_cents"])
        db.session.add(line)
        added.append(line)
    db.session.flush()
    return added


def _taken_elsewhere(journal_line_id, line_id):
    """A book line can be matched by only one statement line at a time."""
    return BankStatementLine.query.filter(BankStatementLine.journal_line_id == journal_line_id,
                                          BankStatementLine.id != line_id).first()


def match(line, journal_line):
    st = line.statement
    _draft(st)
    if journal_line.account_id != st.account_id:
        raise ApiError("That transaction is on a different account")
    if journal_line.cleared_statement_id:
        raise ApiError("That transaction was cleared on an earlier statement")
    if journal_line.entry.date > st.statement_date:
        raise ApiError("That transaction is dated after the statement date")
    if signed(journal_line) != line.amount_cents:
        raise ApiError(f"Amounts differ: statement {money(line.amount_cents):,.2f}, books "
                       f"{money(signed(journal_line)):,.2f}")
    if _taken_elsewhere(journal_line.id, line.id):
        raise ApiError("That transaction is already matched to another statement line", 409)
    line.journal_line = journal_line


def unmatch(line):
    _draft(line.statement)
    if line.posted_here:
        raise ApiError("This line's entry was posted from the reconciliation. Delete the line and reverse "
                       "the journal entry in the General Ledger if it was wrong.", 409)
    line.journal_line = None


def auto_match(statement):
    """Match lines to book transactions of the same amount within a week; references win ties."""
    _draft(statement)
    pool, _ = outstanding(statement)
    pool = [l for l in pool if not _taken_elsewhere(l.id, -1)]
    count = 0
    for line in [l for l in statement.lines if not l.journal_line_id]:
        candidates = [b for b in pool if signed(b) == line.amount_cents
                      and abs((b.entry.date - line.date).days) <= MATCH_WINDOW_DAYS]
        if not candidates:
            continue
        ref = (line.reference or "").strip().lower()

        def score(b):
            refs = " ".join(filter(None, [b.entry.reference, b.memo, b.entry.description])).lower()
            return (0 if ref and ref in refs else 1, abs((b.entry.date - line.date).days), b.id)
        best = min(candidates, key=score)
        line.journal_line = best
        pool.remove(best)
        count += 1
    db.session.flush()
    return count


def post_adjustment(line, counter, description, user_id):
    """Record an item only the bank knew about (charges, interest...) and match it to the line."""
    st = line.statement
    _draft(st)
    if line.journal_line_id:
        raise ApiError("This line is already matched")
    if counter.subtype in ("cash", "receivable", "payable", "payroll"):
        raise ApiError(f"{counter.name} can't be used here. Record fee receipts under Payments, expenses under "
                       "Expenses and transfers as a journal, then match them.")
    if counter.currency and counter.currency != fx.of(st.account):
        raise ApiError(f"{counter.name} holds {counter.currency}")
    amt = abs(line.amount_cents)
    bank_side = {"account": st.account, "memo": line.reference or line.description}
    other_side = {"account": counter, "memo": line.description}
    if line.amount_cents > 0:
        lines = [{**bank_side, "debit": amt}, {**other_side, "credit": amt}]
    else:
        lines = [{**other_side, "debit": amt}, {**bank_side, "credit": amt}]
    entry = ledger.post(line.date, description or f"Bank statement: {line.description}", lines, "manual", None,
                        line.reference or f"BANKREC-{st.id}", user_id, currency=fx.of(st.account))
    line.journal_line = next(l for l in entry.lines if l.account_id == st.account_id)
    line.posted_here = True
    db.session.flush()
    return entry


def complete(statement, user_id):
    _draft(statement)
    s = summary(statement)
    if s["unmatched_lines"]:
        raise ApiError(f"{s['unmatched_lines']} statement line(s) are not matched yet", 409)
    if not s["lines_balance"]:
        raise ApiError(f"The statement lines don't add up from the opening to the closing balance "
                       f"(short by {s['lines_difference']:,.2f}). Check for a missing line.", 409)
    if s["difference"]:
        raise ApiError(f"The books and the bank still differ by {s['difference']:,.2f}", 409)
    _, automatic = outstanding(statement)
    for line in statement.lines:
        line.journal_line.cleared_statement_id = statement.id
    for jl in JournalLine.query.filter(JournalLine.id.in_(automatic or {-1})):
        jl.cleared_statement_id = statement.id
    statement.status, statement.completed_by, statement.completed_at = "completed", user_id, date.today()
    audit("complete", "bank_statement", statement.id,
          f"{statement.account.name} reconciled to {statement.statement_date.isoformat()}")


def reopen(statement, reason):
    if statement.status != "completed":
        raise ApiError("This reconciliation is still in progress")
    latest = last_completed(statement.account)
    if latest.id != statement.id:
        raise ApiError("Only the latest completed reconciliation of an account can be reopened", 409)
    if draft_for(statement.account):
        raise ApiError("Finish or delete the reconciliation in progress first", 409)
    JournalLine.query.filter_by(cleared_statement_id=statement.id).update({"cleared_statement_id": None})
    statement.status, statement.completed_by, statement.completed_at = "draft", None, None
    audit("reopen", "bank_statement", statement.id, reason)


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def book_line_dict(l):
    e = l.entry
    return {"id": l.id, "date": e.date.isoformat(), "entry_no": e.entry_no, "entry_id": e.id,
            "description": e.description, "reference": e.reference, "memo": l.memo,
            "amount": money(signed(l))}


def line_dict(l):
    return {"id": l.id, "date": l.date.isoformat(), "description": l.description, "reference": l.reference,
            "amount": money(l.amount_cents), "posted_here": l.posted_here,
            "match": book_line_dict(l.journal_line) if l.journal_line else None}


def statement_dict(s, detail=False):
    d = {"id": s.id, "account_id": s.account_id, "account": s.account.name, "currency": fx.of(s.account),
         "statement_date": s.statement_date.isoformat(), "reference": s.reference, "status": s.status,
         "start_date": s.start_date.isoformat() if s.start_date else None,
         "opening": money(s.opening_cents), "closing": money(s.closing_cents), "lines": len(s.lines),
         "created_by": s.creator.full_name if s.creator else None,
         "completed_by": s.completer.full_name if s.completer else None,
         "completed_at": s.completed_at.isoformat() if s.completed_at else None}
    if detail:
        items, _ = outstanding(s)
        d.update(lines=[line_dict(l) for l in s.lines], outstanding=[book_line_dict(l) for l in items],
                 summary=summary(s))
    return d


def account_status(a):
    last, draft = last_completed(a), draft_for(a)
    uncleared = (JournalLine.query.filter(JournalLine.account_id == a.id, JournalLine.cleared_statement_id.is_(None))
                 .count())
    return {"id": a.id, "code": a.code, "name": a.name, "currency": fx.of(a),
            "book_balance": money(ledger.balance(a)),
            "reconciled_to": last.statement_date.isoformat() if last else None,
            "reconciled_balance": money(last.closing_cents) if last else None,
            "draft_id": draft.id if draft else None, "uncleared": uncleared,
            "days_since": (date.today() - last.statement_date).days if last else None}
