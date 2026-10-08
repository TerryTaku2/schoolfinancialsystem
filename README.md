# School Management System

A school management system that runs in the browser. The backend uses Python (Flask, SQLAlchemy) and the frontend uses plain HTML5, CSS and JavaScript (ES modules, no build step). It covers student records, academics, attendance and finance, and enforces the school's business rules on the server.

## Quick start

```bash
pip install -r requirements.txt
python run.py            # first run loads demo data, then serves http://127.0.0.1:5000
python run.py --reset    # wipe and reload demo data
python -m pytest -q      # run the business-rule tests
```

| Role          | Username  | Password       |
|---------------|-----------|----------------|
| Administrator | `admin`   | `Admin@2026`   |
| Bursar        | `bursar`  | `Bursar@2026`  |
| Teacher       | `teacher` | `Teacher@2026` |
| Parent        | `parent`  | `Parent@2026`  |

For production, set `SECRET_KEY` and `DATABASE_URL`, run the app behind a WSGI server (e.g. `waitress-serve --call app:create_app`), then run `flask --app run create-admin` to create the first administrator. `SCHOOL_NAME` and `CURRENCY` are also configurable through environment variables. The school type (primary, secondary or combined) is set in Settings → School.

## Several schools on one installation

Set `MULTI_SCHOOL=1` to run many schools from one deployment, each with its own data:

| Address | What it is |
|---|---|
| `/s/<school-code>/` | A school's own system: its staff, parents, students, fees, payroll and books |
| `/platform/` | The platform console, where operators create, rename and suspend schools |
| `/` | A page that asks for the school code |

```bash
set MULTI_SCHOOL=1                       # (export on Linux/macOS)
set PLATFORM_ADMIN_PASSWORD=<strong password>
python run.py                            # then open http://127.0.0.1:5000/platform/
```

Or from the command line: `flask --app run create-school`, `list-schools`, `create-platform-admin`, and `--school <code>` on `seed`, `create-admin` and `ledger-rebuild`.

**How schools are kept apart**
- Every school has its **own database**: a SQLite file in `instance/schools/`, or its own PostgreSQL schema when `SCHOOLS_DATABASE_URL` is a PostgreSQL URL. The list of schools is in a separate platform database (`PLATFORM_DATABASE_URL`). A request is connected only to its own school's database, so one school's queries cannot reach another school's rows.
- Logins belong to a school. The same username (e.g. `admin`) can exist in every school with different passwords. Session and remember-me cookies are scoped to the school's address, and a login is bound to its school code, so a cookie from one school is refused by another. Failed-login throttling is per school.
- Platform operators are separate from school users and can't sign in to a school with their console login.
- Suspending a school blocks its address and logins and keeps its data.

**Moving an existing single-school database in.** Your data, users and passwords are kept:

```bash
flask --app run adopt-school --code greenfield --name "Greenfield Academy" --database-url sqlite:///C:/path/to/instance/school.db
```

| Setting | Default | Purpose |
|---|---|---|
| `MULTI_SCHOOL` | off | Turn on multi-school mode |
| `PLATFORM_DATABASE_URL` | `instance/platform.db` | List of schools and platform operators |
| `SCHOOLS_DATABASE_URL` | empty (SQLite files) | PostgreSQL URL to give each school its own schema instead |
| `SCHOOLS_DIR` | `instance/schools` | Folder for the SQLite school databases |
| `PLATFORM_ADMIN_USERNAME` / `PLATFORM_ADMIN_PASSWORD` | `platform` / none | First platform operator, created on start-up |

## Bank reconciliation

**Accounting → Bank Reconciliation** proves each bank, mobile money and cash account in the books against its statement.

1. **Start:** pick the account, then enter the statement's end date and its opening and closing balances.
   - On an account's **first** reconciliation, also enter the statement's start date. Transactions in the books before that date are taken as already included in the opening balance.
   - If the books and the bank disagree at that point, the screen says by how much.
2. **Statement lines:** import the bank's CSV, or type the lines in. Mobile money exports work too.
   - Columns are chosen on import: one amount column, or separate money-in and money-out columns.
   - Dates such as 05/10/2026, 2026-10-05 and 5 Oct 2026 are understood.
3. **Matching:** each line is matched to its transaction in the books.
   - Most are matched automatically: same amount, within 7 days, with a matching reference preferred.
   - The rest are matched by hand from a list of outstanding items.
   - Bank charges, interest and other items only the bank knew about are **posted from the reconciliation** (to Bank Charges, Interest Income or another account) and matched at once.
   - Fee receipts are recorded under Payments as usual, so the student's account is credited, and then matched.
4. **Complete:** this is allowed only when every line is matched, the lines add up from the opening to the closing balance, and:

   bank closing balance + deposits not yet credited − payments not yet presented = balance per books

   Completing clears the matched transactions, and anything still outstanding carries to the next reconciliation.

**Other rules**
- A receipt and its void cancel each other out and are cleared automatically.
- Statements follow on from one another: the next must end after the last one, and its opening balance defaults to the last closing balance.
- Only the latest completed reconciliation of an account can be reopened.
- Each currency's accounts are reconciled in their own currency.
- The printable **bank reconciliation statement** lists every outstanding item, with lines for whoever prepared and reviewed it.
- **Permissions:** banking.view, banking.manage (enter, import, match and post) and banking.approve (complete or reopen). Bursars prepare and an approver completes.
- Once reconciliation has started, the ledger can't be rebuilt, because rebuilding would undo the matches.

## Users, roles and permissions

**School → Users & Permissions** controls what each person can see and do.

- **Permissions** come in three levels per area (students, guardians, staff, academics, fees, payments, expenses, payroll, assets, accounting, reports and school settings):
  - **view**: see the area
  - **manage**: create and change things in it
  - **approve**: sensitive actions such as approving payroll and expenses, voiding receipts and closing periods
  Manage and approve include view. Removing view removes the rest.
- **Roles:**
  - **Administrator** always has every permission, so a school can't lock itself out.
  - **Parent** is the parent portal only (their own children).
  - **Bursar** and **Teacher** are built-in roles whose permissions can be edited; changes apply to everyone with the role.
  - **Custom roles** (for example "Accounts clerk" or "Deputy head") are based on Bursar (office) or Teacher. A Teacher-based role also works with the classes its holder teaches.
- **Per person:** the Permissions button adds or removes individual permissions for one user without changing their role.
- **Rules that always apply:**
  - Nobody approves their own expense or a payroll they prepared.
  - Teachers without wider permissions see only their own classes, and parents only their own children.
  - Only administrators can create, change or reset administrator accounts.
  - Someone who manages users can only grant permissions they hold themselves, and can't change their own.
  - Every change is written to the audit log.

## Library

**Library** in the menu (under Academics) runs the school library.

- **Catalogue:** titles with author, ISBN, category (textbook, fiction, reference...), subject, level and shelf. Each physical copy gets an accession number (LIB-00001...), or keeps the number already on its label. Stick the number on the book: the desk finds books by typing it or scanning it with a USB barcode scanner.
- **Issue & return:** scan a book, then search for the student or staff member and lend it, or take it back, renew it or mark it lost. Students borrow up to 3 books for 14 days and staff 10 for 30 days, with 2 renewals; all of these are adjustable under **Rules**. Nobody can borrow while they have an overdue book.
- **Textbooks for the term:** from a textbook's page, **Issue to a class** lends one copy to every student in the class, due at the end of term. These don't count towards the borrowing limit.
- **Fines:** optional, per day late, in the school currency. A lost book is charged at the copy's replacement cost. A student's fine can be **added to their fees invoice** for the term (it shows on their statement, is paid like any fee and is booked to Sundry Income), marked paid, or waived with a reason.
- **Who sees what:** permissions are library view, manage (issue, return, add books) and approve (waive fines, delete books, change rules). Teachers and bursars can browse the library by default; create a "Librarian" role with Library manage for the librarian. Parents see their children's books on the student page.

## Forgotten passwords

No email is needed; whoever is one level up resets the password and the person then chooses their own.

- **Staff, teachers and parents:** on the sign-in page, **Forgot password?** asks the school's administrators for a reset. They see it in the top bar and on **Users & Permissions**, check it's really that person, and use **Reset password** to set a temporary password.
- **Temporary passwords:** any password set by someone else must be replaced at the next sign-in; nothing else works until the person chooses their own.
- **School administrators:** the platform operator uses **Reset admin password** on the school in the platform console.
- **The platform operator:** set `PLATFORM_ADMIN_RESET=1` and a new `PLATFORM_ADMIN_PASSWORD` (on Render: Environment), redeploy or restart, sign in, then delete `PLATFORM_ADMIN_RESET`.

## Multiple currencies

A school works in its own currency (USD or ZWG, chosen when it is created) and can add any of the currencies used in Zimbabwe's multi-currency system: **ZWG, USD, ZAR, BWP, GBP, EUR, CNY, INR, JPY, AUD**, plus **ZMW** and **MZN** for schools near the borders. The administrator ticks them under **Settings → School → Currencies**. From then on, every money transaction asks which currency it is in, and **each amount is recorded exactly as it happened. Nothing is ever converted.**

- **Accounts:** cash on hand, bank and mobile money each get a separate account per currency, numbered in the same block as the school-currency account (cash 1001-1009, bank 1011-1019, mobile money 1021-1029). A cash account holds only its own currency; income, expense and other accounts hold all of them.
- **Fees:** each fee item has a currency, and a student gets one invoice per term per currency. A ZAR receipt clears ZAR invoices only, and credit is kept per currency. Student balances, statements of account, receipts and debtors reports are shown per currency.
- **Expenses, assets, journals:** each is in one currency and is paid from (or received into) an account in that currency.
- **Books:** every journal entry is in one currency, so each currency has its own complete, balancing books. Financial statements are shown per currency, or **combined in the school currency at the rates on the report date**.
- **Exchange rates:** the bursar records each day's rates against the US dollar under **Exchange Rates** (for example 1 USD = 26.75 ZWG and 18.40 ZAR), as the RBZ quotes them. Any two currencies convert through the dollar, e.g. ZAR to ZWG. Rates are used only for combined figures and for PAYE.
- **Payroll in several currencies:** the basic salary, each allowance and deduction, and each overtime, bonus or one-off deduction has its own currency. Following ZIMRA's rule for remuneration paid in more than one currency:
  - PAYE is worked out on the total, converted into the tax table's currency at the rates for the pay date.
  - PAYE, AIDS levy, NSSA and the employer levies are then withheld from each currency in proportion to the pay in it.
  - Net pay is paid from an account in each currency.
  - Tax withheld in each currency is remitted to ZIMRA in that currency, and the P2 report shows it per currency.
- **Removing a currency** is only possible while nothing has been recorded in it. The school's own currency is always on.
- Existing data is upgraded automatically: everything recorded before is in the school's own currency, and existing rates are ZWG per USD. Schools that used the earlier USD + ZWG switch keep both currencies.

## Zimbabwe school structure

- **Levels:** ECD A, ECD B, Grades 1-7 (primary); Forms 1-4 (O Level) and Forms 5-6 (Lower and Upper Six, A Level). A school is **primary**, **secondary** or **combined**, and can only open classes at the levels it offers. Classes are named Grade 3A, Form 2B, ECD A, and so on.
- **New schools start with:**
  - the classes for their type
  - heritage-based curriculum learning areas for ECD and Grades 1-7
  - common ZIMSEC subjects for Forms 1-4; A Level combinations are allocated by the school
  - a three-term calendar for the current year (Term 1 mid-January to April, Term 2 May to August, Term 3 September to early December)
  - grading scales
  Everything can be edited. The term dates are typical and should be matched to the Ministry's calendar.
- **Grading:** separate scales for primary, O Level (A ≥ 75, B ≥ 65, C ≥ 50, D ≥ 40, E ≥ 30, U; A-C are passes) and A Level (A-E, O, F, with points). Results and report cards use the scale for the class's level.
- **Promotion, once a year after Term 3:**
  - Students move to the next level, same stream where possible, respecting capacity.
  - Grade 7 completes primary school, unless the school also offers Form 1.
  - Form 4 completes O Level; only students the school selects on their results continue to Lower Six.
  - Form 6 completes A Level.
  - Any student can be marked as repeating or leaving.
  - If the school offers the next level but has no class for it yet (e.g. no Grade 7 class), promotion is blocked until one is created.
- Databases from earlier versions are upgraded automatically. Class levels are read from class names ("Grade 4B" → Grade 4, "Form 2" → Form 2, "Lower Six" → Form 5), the existing grading scale becomes the primary scale, and the school type is worked out from the classes.

## WhatsApp chatbot integration

The T-Tech Connect WhatsApp chatbot (the parent repository) gives parents a **🎓 School Fees** menu (option 7). From it they can:

- check each child's balance, overdue amount and unpaid invoices
- see recent payments and receipts
- pay fees with EcoCash or OneMoney through Paynow, which records a receipt here automatically
- read announcements addressed to parents

Parents are identified only by their WhatsApp number. It must match the **guardian phone** recorded on the student. Any format works (`+263 77 123 4567`, `0771234567`), because numbers are compared after normalising.

### Where it runs

The chatbot website serves this app at **`/school`** (for example `https://<chatbot-domain>/school/`). It ships with every chatbot deploy and needs no separate service. See `school_mount.py` in the chatbot repository.

- **Database:** its tables are kept in the `school` schema of the chatbot's `DATABASE_URL`. Set `SCHOOL_DATABASE_URL` to use a different database.
- **First login:** on start-up, if there is no administrator, one is created from `SCHOOL_ADMIN_USERNAME` (default `admin`) and `SCHOOL_ADMIN_PASSWORD`. Without that password nobody can log in.
- **Settings:** `SCHOOL_NAME` and `CURRENCY` are read from the same environment as the chatbot. In multi-school mode, chatbot calls go to a school's own address (`/s/<code>/api/integration/...`) and receipts carry `school_code`.
- **No domain needed:** the chatbot and this app call each other directly inside the process, not over the internet. WhatsApp option 7 and the receipts work the same on `localhost`, ngrok or a hosting provider's default address, whatever `BASE_URL` is set to. The integration key and session secret are derived from the chatbot's `FLASK_SECRET_KEY`, so there is nothing else to configure.

### Running it on its own

`python run.py` still works and serves the app at `/`. To connect a separately hosted copy to the chatbot, set these:

| School system | Chatbot | Purpose |
|---|---|---|
| `CHATBOT_API_KEY` | `SCHOOL_API_KEY` | The same random value on both sides. |
| `CHATBOT_WEBHOOK_URL` | — | `https://<chatbot>/webhooks/school`, for WhatsApp receipts of payments taken at the school. |
| — | `SCHOOL_API_URL` | Base URL of this app. |
| `PHONE_COUNTRY_CODE` | — | Country code for guardian phones stored in local format (default `263`). |

How payments made through the chatbot are handled:

- They are recorded with method `mobile` and the Paynow reference (`SCH-…`). The receiver is a `whatsapp-bot` service account, which is inactive and can never log in.
- Recording is idempotent per reference, so a retry returns the original receipt instead of creating a second one.
- The chatbot records a payment only after Paynow confirms it. It never accepts more than the outstanding balance.
- If this app is unreachable at that moment, the chatbot admin gets an alert and can re-send the payment with `school retry SCH-XXXXXXXX` on WhatsApp.

Endpoints (all require `Authorization: Bearer <CHATBOT_API_KEY>`). Each one only returns students whose guardian phone matches `phone`:

```
GET  /api/integration/guardian?phone=2637...
GET  /api/integration/students/<id>/account?phone=2637...
POST /api/integration/payments        {student_id, phone, amount, reference}
GET  /api/integration/announcements
```

WhatsApp only delivers free-form messages within 24 hours of the parent's last message. A receipt for a payment taken at the school can therefore fail for a parent who hasn't messaged recently, unless an approved message template is set up in Meta.

## Modules

- **Students and guardians:** auto-numbered admissions, siblings matched by guardian phone, status lifecycle (active → graduated/transferred/withdrawn), parent portal accounts
- **Staff:** duties, salaries, linked login accounts
- **Payroll (ZIMRA):** monthly PAYE under the Final Deduction System, AIDS levy, NSSA, ZIMDEF and WCIF; recurring allowances, benefits and deductions; payslips; remittances; ZIMRA P2 monthly returns and annual P6/ITF16 summaries; versioned tax tables
- **Asset register:** fixed assets with custodians and locations, monthly straight-line or reducing-balance depreciation, disposals and write-offs, reconciled to the ledger
- **Classes and subjects:** Zimbabwe levels (ECD A to Form 6), streams, capacity, class teachers, subject–teacher allocation
- **Timetable:** weekly grid with clash detection
- **Attendance:** daily register with a term summary
- **Exams and marks:** weighted assessments, mark sheets, broadsheets, printable report cards with positions
- **Finance:** fee structure, invoice generation, payments with receipts, statements, scholarships, expense approval
- **Financial statements:** income statement, balance sheet, cash flow statement and trial balance, with comparative figures, prepared from a double-entry general ledger (see below)
- **General ledger:** chart of accounts, journals, account ledgers with running balances, manual journal entries, period close
- **Reports:** collection by class, debtor aging, cash flow, academic performance, at-risk students, CSV export
- **Announcements, audit log, academic calendar, grading scales (primary, O Level, A Level), end-of-year promotion**
- **Multi-school:** many schools on one installation, each with its own database, users and address, managed from a platform console

## Business rules enforced by the server

**Finance**
- Money is stored as integer cents, so there is no floating-point drift.
- Each student gets one invoice per term, so invoice generation is safe to re-run.
- Payments are allocated to the oldest outstanding invoice first. Any surplus is kept as credit and applied automatically to the next invoice.
- Receipts are never edited or deleted. A receipt can only be voided, with a reason, and only by an administrator, not by the bursar who received it.
- Voiding an invoice turns any money already applied to it into credit on the student's account.
- Non-cash payments need a unique transaction reference. Payments cannot be dated in the future.
- Scholarships reduce only fees marked "discountable" (tuition, but not exam fees). A student's scholarships are capped at 100% in total.
- A fee item cannot be edited once it has been invoiced. Changes after that are made as charges on individual invoices.
- Expenses follow pending → approved/rejected → paid. The person who requested an expense can never approve it.

**Accounting (double-entry general ledger)**
- Every financial event posts a balanced journal entry automatically, in the same database transaction as the event:

  | Event | Debit | Credit |
  |---|---|---|
  | Invoice issued | Student fees receivable (per student) | Fee income accounts (tuition, levy, exams…) |
  | Scholarship on an invoice | Scholarships & discounts (reduces income) | (part of the invoice entry) |
  | Extra charge | Student fees receivable | Sundry income |
  | Payment received | Cash / Bank / Mobile money, depending on payment method | Student fees receivable |
  | Expense approved | Expense account for its category | Accounts payable (posted on the expense date) |
  | Expense paid | Accounts payable | The cash/bank account chosen when paying |
  | Payroll approved | Salaries & wages, employer payroll contributions | PAYE & AIDS levy, NSSA, ZIMDEF, pension & medical aid, other deductions, net salaries payable (dated month end) |
  | Salaries paid / remittance | The payroll liability | The cash/bank account chosen |
  | Asset purchased / donated | Property, plant & equipment | Cash/bank account, or donations income |
  | Depreciation run | Depreciation | Accumulated depreciation (dated month end) |
  | Asset disposed | Accumulated depreciation, cash proceeds | PPE at cost; difference to gain or loss on disposal |
  | Any void | Exact reversing entry, dated the day of the void | |

- The statements use the accrual basis: fees count as income when invoiced, and expenses count when they are incurred.
- Posted entries are never edited or deleted. Mistakes are corrected with reversing entries. Automatic entries can only be reversed by voiding their source document (invoice, receipt or expense), which keeps the per-student balances in step with the ledger.
- Manual journals are used for opening balances, depreciation, asset purchases, donations and transfers. They must balance, cannot be future-dated, and cannot use the receivables or payables accounts (those change only through invoices, receipts and expenses). A manual journal or an expense payment cannot spend more than a cash account holds on that date.
- **Period close:** an administrator can set a lock date. After that, nothing dated on or before it can be posted, so statements already issued for that period never change.
- Account codes follow the account type: 1xxx assets, 2xxx liabilities, 3xxx net assets, 4xxx income, 5xxx expenses. System accounts can't be deactivated, and other accounts only when their balance is zero.
- Every statement shows a built-in check:
  - Trial balance: debits = credits.
  - Balance sheet: assets = liabilities + net assets. Students who owe are shown as receivables, and students who have overpaid are shown as "fees received in advance".
  - Cash flow (direct method): opening cash + movement = closing cash. Transfers between cash accounts drop out.
- A database created before the ledger existed is upgraded automatically on first start: new columns are added and the ledger is built from existing invoices, receipts and expenses. You can repeat the rebuild at any time with `flask --app run ledger-rebuild` or from General Ledger → Period close.

**Payroll (ZIMRA)**
- PAYE uses ZIMRA's progressive monthly bands. The default table is ZIMRA's USD table for January–December 2025 (0% to US$100, then 20/25/30/35/40% with breaks at 300, 1,000, 2,000 and 3,000). When a Finance Act changes the rates, an administrator adds a new table with its effective date; earlier payrolls keep the table they were calculated with. Tables cannot be back-dated over approved payrolls.
- Taxable income = gross cash pay + taxable non-cash benefits − exempt income (bonus up to the annual exemption, exempt allowances) − employee NSSA − pension contributions (up to the monthly cap).
- Tax credits (elderly from age 55, blind/disabled, 50% of medical aid contributions) reduce tax but never below zero. The AIDS levy is 3% of tax after credits.
- NSSA is 4.5% employee + 4.5% employer on earnings up to the insurable ceiling (US$700). ZIMDEF is 1% of gross. WCIF defaults to 0% and should be set from the school's NSSA assessment.
- Basic salary is pro-rated by calendar days in the month of hire. The bonus exemption is tracked per employee across the tax year (January–December).
- One payroll per month. The bursar prepares it as a draft (overtime, bonuses and one-off deductions can be adjusted). An administrator other than the preparer approves it, which posts the accrual. Approved payrolls can't be edited.
- Salaries and remittances are refused if the paying account lacks funds. PAYE and NSSA are due by the 10th of the following month; the P2 report flags late or outstanding returns.
- An approved payroll can be voided by an administrator (reversing the accrual) only before any salary or remittance has been paid. After that, corrections go through the next month's payroll.
- Payroll liability accounts (21xx) are control accounts: manual journals can't post to them.

**Asset register**
- Assets are registered as purchased (credits the chosen cash account, refused if funds are short), donated (credits donations income) or existing (already in the opening balances; no posting, with depreciation brought forward and remaining life).
- Depreciation is charged for whole months from the month of acquisition, never in the month of disposal, and never below the residual value. Straight line is exact to the cent over the useful life. Land is not depreciated.
- Depreciation runs cover completed months only and must go in order; an asset that missed earlier months is caught up in the next run. Only the latest run can be reversed, by an administrator.
- Disposal first charges depreciation up to the previous month, then removes cost and accumulated depreciation and posts the gain or loss. Cost and depreciation settings are fixed once registered; a registration made in error can be cancelled (reversing its posting) only before any depreciation.
- The register is reconciled against accounts 1500 and 1590, and any difference is shown.

**Academics**
- Exam weights in a term cannot exceed 100%. A term result is the weighted mean of the exams the student actually sat; missed exams are flagged on the report, not counted as zero.
- Positions use competition ranking, so tied students share a position (1, 1, 3).
- Only the assigned subject teacher can enter marks for that subject, and only after the exam date. Scores must be between 0 and the exam's maximum. Locked exams are read-only.
- Only the class teacher takes attendance, and only on school days inside a term, never for future dates. Every student must be marked. Records older than 7 days can only be corrected by an administrator. Excused absences don't lower a student's attendance rate.
- Class capacity is enforced when admitting or moving students.
- A teacher can be class teacher of only one class.
- Staff members with assigned duties cannot be marked as having left. Leavers lose system access.
- Students, classes, subjects, exams and terms that have history cannot be deleted. Students are given a leaving status instead.
- Promotion follows the Zimbabwe rules in "Zimbabwe school structure" above. The administrator can choose students who repeat the year or leave, and which Form 4 students continue to Lower Six.

**Security**
- Passwords are hashed.
- Repeated failed logins are throttled.
- Session cookies are HttpOnly and SameSite.
- Every state-changing request must carry a custom header, which blocks cross-site request forgery (CSRF).
- Access is checked per permission and per record (see "Users, roles and permissions"): parents see only their own children, and teachers see only their own classes.
- Every change is written to the audit log.

## Project layout

```
run.py, config.py
app/
  models.py            database schema
  utils.py             validation, money, permissions, numbering, audit
  tenancy.py           multi-school routing: one database per school
  services/            business logic (finance, ledger & statements, payroll, assets, school structure, academics, access rules,
                       chatbot notifications)
  api/                 JSON REST endpoints (auth, people, academic, finance, dashboard,
                       chatbot integration)
  seed.py              demo data + CLI commands
  templates/index.html single-page app shell
  static/css/app.css   design system (light/dark, responsive, print)
  static/js/           api client, UI kit, SVG charts, router, views/
tests/                 business-rule and accounting tests
```
#   s c h o o l f i n a n c i a l s y s t e m 
 
 
## Deploying on Render

The repository includes a Render Blueprint (`render.yaml`) that creates the web service and a PostgreSQL database.

1. Push this project to GitHub. `.env` and `instance/` are gitignored, so passwords and local data stay on your machine.
2. In Render, go to **New → Blueprint**, pick the repository and enter a value for `PLATFORM_ADMIN_PASSWORD` when asked.
3. Once it is live, sign in at `https://<your-app>.onrender.com/platform/` and create the schools there.

If you set the service up by hand instead:

- **Build command:** `python -m pip install -r requirements.txt`
- **Start command:** `python -m gunicorn wsgi:app --worker-class gthread --workers 1 --threads 8 --bind 0.0.0.0:$PORT --timeout 120`
- **Environment variables:** `MULTI_SCHOOL=1`, `SECRET_KEY` (a long random value), `PLATFORM_ADMIN_PASSWORD`, `DATABASE_URL` (the database's *Internal* connection string) and `PYTHON_VERSION=3.14.6`.

With a PostgreSQL `DATABASE_URL`, the platform tables and every school (each in its own schema) live in that one database. Render's disk is wiped on each deploy, so nothing is kept in local files. Render's free PostgreSQL database expires after a trial period, so use a paid plan for real school data.
