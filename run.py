"""Entry point.

    python run.py            # start the dev server (auto-seeds demo data on first run)
    python run.py --reset    # wipe and re-seed demo data, then start (single-school mode)

Multi-school mode (MULTI_SCHOOL=1): schools are created in the platform console at
/platform/ or with `flask --app run create-school`, and each is served at /s/<code>/.
"""
import os
import sys

from app import create_app, db
from app.models import Invoice, JournalEntry, User
from app.services.ledger import rebuild_ledger
from app.seed import DEMO_ACCOUNTS, seed_demo

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        if app.config["MULTI_SCHOOL"]:
            from app.models import PlatformAdmin, School
            print(f"Multi-school mode: {School.query.count()} school(s). Platform console: /platform/")
            if not PlatformAdmin.query.first():
                print("No platform operator yet: set PLATFORM_ADMIN_PASSWORD or run "
                      "'flask --app run create-platform-admin'.")
        elif "--reset" in sys.argv or User.query.count() == 0:
            print("Loading demo data...")
            print(seed_demo())
            print("Demo logins:")
            for uname, (pw, role, _) in DEMO_ACCOUNTS.items():
                print(f"  {role:8} {uname} / {pw}")
        elif JournalEntry.query.count() == 0 and Invoice.query.count() > 0:
            # Database from a version without the general ledger: post its history once.
            print("Building the general ledger from existing invoices, receipts and expenses...")
            print(f"{rebuild_ledger()} postings created")
            db.session.commit()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
