# SecondServe

**Plan better. Share surplus. Waste less.**

Developed by Ahmad Karim. Repository: [meahmadkarim/secondserve](https://github.com/meahmadkarim/secondserve).

Portfolio verification: all 52 Django tests passed, system checks reported no issues, and migration consistency checks found no changes during the November 2026 maintenance review. Documentation maintenance used AI assistance; this verification is not a claim of deployment or real-world impact.

SecondServe is a functional Django hackathon prototype for a small, single-campus pilot. Students share meal intentions; kitchens use an explainable demand estimate, record preparation and normal servings, and offer suitable unserved portions for free collection. Administrators see recorded outcomes rather than invented carbon, water, or financial savings.

The SolarPunk connection is practical: accessible technology supports community care, fair access to shared food, and more thoughtful resource use. There are no income labels, public rankings, or shaming.

## What works

- Django authentication, persistent sessions, student-only registration, and three server-enforced roles.
- Upcoming meals, editable intentions until cutoff, individual reservation history and private participation counts.
- Kitchen meal creation/editing, visible menu-change notices, validated lifecycle transitions, saved forecast snapshots, reasoned overrides, cumulative preparation/serving totals, and audited corrections.
- One surplus listing per meal, staff handling confirmation, atomic inventory, cancellations, expiry, withdrawal, private pickup codes, one-time collection, and persisted invalid-code throttling.
- Final portion reconciliation; dated operational reports; administrator CSV export with spreadsheet formula neutralisation; user role management; campus settings; audit history.
- SolarPunk interface, responsive layouts, local SVG illustration/assets, keyboard focus, labelled forms, status text, and tables with readable values.
- Synthetic historical demo, a no-wait cook-along service, and automated tests including competing claims on the final portion.

## Stack and layout

Python **3.12+**, **Django 5.2.18 LTS**, SQLite, Django templates/ORM/migrations/tests, custom CSS, and a few lines of local JavaScript. All direct and transitive runtime dependencies are pinned in `requirements.txt`. `tzdata` provides timezones on Windows. No API key, paid service, Node build, Redis, or background worker is needed.

```text
config/                     settings, URLs, WSGI/ASGI
core/models.py              database schema and constraints
core/services.py            authoritative business rules and transactions
core/forms.py               input validation and labelled forms
core/views.py               role/ownership checks and HTTP workflow
core/tests.py               Django test suite and real competing threads
core/migrations/            versioned database migrations
core/management/commands/   seed_demo, expire_reservations
templates/                  public, student, kitchen, administrator pages
static/                     local CSS, JS and SVG icon
ARCHITECTURE.md              design, invariants and concurrency strategy
DEMO.md                      three-minute presentation journey
```

The suggested domain apps are consolidated in `core` to keep this undergraduate-scale prototype easy to navigate. Models and service functions retain explicit domain names; this is one application, not microservices. Django's built-in User plus a Profile stores roles. Forecast assumptions are captured as immutable-by-workflow JSON snapshots; reservation quantity is database-constrained to one. `committed` is a transactionally maintained inventory counter, and `PickupAttempt` provides a lightweight persisted throttle.

## Fresh local setup — macOS / Linux

From the checkout root (the directory containing `manage.py`):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open the development server at `127.0.0.1:8000` in your local browser. `python` refers to the activated virtual environment for every command below.

## Fresh local setup — Windows PowerShell

Install Python 3.12 or newer, then:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

If activation is restricted by your local policy, use `.\.venv\Scripts\python.exe` in place of `python`; activation is optional. In Command Prompt use `.venv\Scripts\activate.bat`.

## Configuration and persistence

`.env` is ignored by Git. A small reader accepts simple `NAME=value` lines; existing process variables take precedence. Do not put secrets in source control. Local defaults deliberately make a fresh demo work without keys.

| Variable | Local default | Purpose |
| --- | --- | --- |
| `DJANGO_DEBUG` | `1` | Development only; use `0` outside local development |
| `DJANGO_SECRET_KEY` | Explicit local-only fallback | Supply a random secret outside local development; startup fails without one when debug is off |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma-separated hosts; `.env.example` narrows this to local hosts |
| `SECOND_SERVE_DB` | `db.sqlite3` | Optional SQLite file path |
| `SECOND_SERVE_TEST_DB` | Django temporary in-memory test DB | Optional dedicated file path for exercising file-backed SQLite tests; never point to valuable data |

The application uses timezone-aware timestamps; campus timezone defaults to **Asia/Bishkek**. An administrator can change its IANA timezone and forecast assumptions in Campus Settings. Form dates/times are interpreted in that campus timezone. SQLite records and Django sessions survive server restarts. Back up the database file using SQLite's backup facility before migration or moving real pilot data.

## Local demonstration accounts

Run `seed_demo` explicitly to create them. These are public, **development-only demonstration credentials**, never production credentials.

| Role | Username | Password |
| --- | --- | --- |
| Student | `student01` (through `student32`) | `SecondServe!2026` |
| Kitchen | `kitchen1`, `kitchen2` | `SecondServe!2026` |
| Administrator | `admin` | `SecondServe!2026` |

The seed creates 32 synthetic students, two staff, one administrator, 14 days of lunch/dinner history, current-day services, and four days of upcoming meals. It includes all reservation outcomes, reconciled historical totals, saved forecasts, and a constrained observation. The current lunch is immediately reservable and collectible for four hours from initial creation. Current dinner is a **synthetic no-wait cook-along**: its timings have elapsed but staff still perform each lifecycle action. See [DEMO.md](DEMO.md).

The command is transactional and idempotent: it creates missing slots and never deletes unrelated data, resets existing demo passwords/roles, or rewrites operational records. It refuses collisions with non-demo usernames. Rerunning on a later campus date adds that day's data. Rerunning on the same date deliberately does **not** reopen an expired/closed listing or extend safety deadlines; staff can create another genuinely new service with appropriate timings. Demo creation is refused with `DJANGO_DEBUG=0`. Do not copy the demo database into a deployment with real users.

## Tests and useful checks

```bash
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
python -m pip check
python manage.py expire_reservations
```

`expire_reservations` is idempotent. It persists past-deadline status and releases committed reservation counts, without classifying food outcomes. Correctness never depends on running it: reserve, cancel and collect validate deadlines on every request; dashboard queries treat elapsed reservations as expired immediately.

The test suite covers permissions/ownership, registration escalation, CSRF and POST-only mutations, cutoff boundaries, forecast arithmetic and immutability, inventory and pickup rules, concurrent last-portion claims, reconciliation, report filters/denominators and formula-safe CSV, lifecycle, and seed idempotence/invariants. Controlled timestamps avoid fragile waiting.

For a file-backed concurrency run on macOS/Linux:

```bash
SECOND_SERVE_TEST_DB=/tmp/secondserve-tests.sqlite3 python manage.py test --noinput
```

On Windows, set `$env:SECOND_SERVE_TEST_DB` to a **disposable dedicated test path**, then run the tests. Django creates and destroys that database. Never use the application's database path. The concurrency test launches two threads with independent connections and a barrier; exactly one reservation must succeed and inventory must stay at one consumed portion.

Validation performed during implementation: Django checks and migrations, the automated suite (including a file-backed SQLite run), repeat seeding, and a Chromium journey through all three roles at desktop and mobile widths. Browser verification used an isolated database copy, leaving the seeded demonstration journey available. Exact final results are in [VALIDATION.md](VALIDATION.md).

## Forecast: transparent starting assumptions

Let A = attending, U = unsure, N = eligible active student accounts with no response. Explicitly not attending contributes zero. Staff/admin accounts and inactive students are excluded from eligibility.

```text
estimated_demand     = ceil(A × 0.90 + U × 0.50 + N × 0.30)
buffered_quantity    = ceil(estimated_demand × (1 + 0.05))
suggested_preparation = min(planned_capacity, buffered_quantity)
```

Rates/buffer are configurable fractions in [0, 1], defensively clamped by the calculation. Decimal arithmetic avoids float rounding surprises. With 100 attending, 20 unsure and 30 no response, demand is 109, buffer gives 115, and capacity 110 caps the recommendation at 110. The UI shows every count and operation.

These are **demonstration assumptions, not scientifically validated probabilities, AI, or machine learning**. Starting preparation freezes counts, rates, buffer, capacity, unbuffered estimate, recommendation, optional override/reason, and timestamp. Later settings do not rewrite history. Staff can override preparation within capacity with a reason; capacity can be explicitly edited with an audit entry. Recorded actual preparation may differ from a suggestion.

A future calibrated model would need consented, appropriately minimised observations linking intentions to actual normal attendance, repeated services, campus calendar/events, and explicit unmet-demand observations. Aggregate serving totals cannot identify individual attendance rates. No individual attendance learning is implemented.

## Lifecycle and food inventory

```text
planned → preparing → regular_service → surplus_collection → closed
                                  └──────────────────────→ closed
planned / preparing (zero prepared) → cancelled
```

Beginning preparation locks intentions, even if staff begin early. Staff record prepared totals before starting regular service. Publication requires explicit preparation and regular-serving records, completed regular service, a valid pickup window, and handling confirmation. Draft/open/paused listings are editable; withdrawn or closed listings cannot reopen. Pickup times cannot change after reservations exist. A listing cannot be reopened or extended after its deadline.

- `regular served ≤ prepared ≤ capacity` and all quantities are nonnegative whole numbers.
- One listing per meal. `offered ≤ prepared − regular served`.
- `availability = offered − reserved − collected`, while the listing is open and before deadline.
- Collected portions remain consumed permanently. Cancelling before expiry restores inventory; a paused listing still accepts no new reservations until reopened.
- At most one reserved/collected portion per student per meal. A valid cancellation allows reserving again.
- Reservation codes are random, private, unique, and collection is one-time, during the window only. Pausing blocks new reservations but permits existing valid pickups.
- Opening the surplus phase freezes preparation/serving totals. Later corrections need an explicit checkbox, reason, validation against the current offer, and an audit record.
- Withdrawal invalidates outstanding reservations with a visible reason; completed handovers stay recorded.

SQLite uses **BEGIN IMMEDIATE** for service transactions, acquiring the write reservation before inventory reads. A conditional counter update and reservation creation commit together. Database constraints also enforce nonnegative committed capacity and uniqueness. Competing claims cannot silently overbook; contention is returned as a retry message. SQLite serialises writers across the database; throughput is limited and busy requests may need a user retry. This is suitable for a small demo, not a claim of production scale. Migrating to another database requires reviewing this strategy rather than assuming SQLite's locking transfers unchanged.

At closure:

```text
prepared = regular served + surplus collected + discarded + retained/transferred
```

Uncollected reservations must expire or be explicitly withdrawn first. Staff decide and record the destination of the food. Uncollected reservations never automatically become discarded food. Open services display the remaining quantity as **Awaiting final outcome**.

**Safe handling and redistribution decisions belong to authorised kitchen staff under the campus's applicable procedures. The app records decisions and deadlines; it does not certify food safety or detect freshness. Food returned from students' plates must not be offered.** Missing ingredient/allergen notes display “Information not provided—ask kitchen staff”; food is never inferred to be allergen-free.

## Honest metrics

- **Surplus portions collected:** reservations actually marked handed over by staff. Primary sharing metric. Reservations alone contribute zero.
- **Prepared / served normally / offered / discarded / retained-transferred:** recorded quantities for the selected services, with cancellations excluded.
- **Discard rate:** discarded ÷ prepared × 100. The dashboard includes selected open services and labels awaiting outcomes, so this is provisional until those close.
- **Collection rate:** collected ÷ (collected + expired no-shows) × 100, using only completed pickup windows. Cancellations are shown separately; withdrawn reservations are not no-shows. Zero denominators display 0%.
- **Forecast MAE:** mean absolute difference between original **unbuffered** saved demand and actual regular servings for closed, unconstrained services. Cancelled services and sold-out/capacity-constrained observations are excluded and flagged. A service that served every prepared portion is conservatively treated as constrained even without a manual sold-out flag. No eligible history displays “No eligible records”.

All synthetic records and historical presentation are visibly labelled **Synthetic demonstration data**. Collected surplus might otherwise have been safely reused; it is not proof of waste prevented. Retained/transferred portions are not automatically waste prevented either. No estimated environmental savings or weights are displayed.

## Security and deployment notes

Django hashes passwords and enforces sessions, CSRF, escaping, host validation, ORM queries, role/ownership checks, and POST mutations. Pickup codes are not present in public/listing responses or report CSVs. The custom role manager never lets an administrator remove their own access. Important mutations have audit records.

The pickup throttle persists in SQLite per authenticated staff account: ten invalid codes within a five-minute window prevent further lookups until it expires. This is a lightweight pilot control, not a network-wide abuse protection system. Before a real deployment, add login throttling, operational monitoring, HTTPS, backups, account recovery and campus identity provisioning, a suitable deployment server, and a reviewed database/concurrency policy. Run `python manage.py check --deploy` with actual deployment settings. Set a random `DJANGO_SECRET_KEY`, turn debug off, configure exact hosts, run `collectstatic`, and serve static assets with the deployment server. Secure session/CSRF cookies are enabled when debug is off. Do not use Django runserver outside development. Audit logs are application records, not tamper-proof forensic storage.

No email/SMS, payments, deliveries, hardware, face recognition, freshness detection, multiple campuses, or trained models are included. Practical future improvements: campus SSO, accessibility feedback from students, kitchen-approved destination categories, forecast calibration using ethically collected data, reminder opt-ins, pagination, and PostgreSQL load testing. These do not block the working campus prototype.


## Technical implementation for reviewers

**Django 5.2** supplies authentication, sessions, forms, ORM models, migrations and the test framework. **SQLite** stores meals, intentions, forecast snapshots, reservations and audit records. **Python service functions** enforce lifecycle rules and transactional inventory handling; Django templates with **HTML, CSS and JavaScript** provide the interface.

Demand estimation is an explicit weighted formula over attending, unsure and unanswered intentions, followed by a buffer and capacity cap. It does not use scikit-learn, neural networks or a trained prediction model. This project demonstrates Python/web engineering alongside the portfolio's machine-learning projects.
