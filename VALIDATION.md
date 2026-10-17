# Validation record

Validated on 17 October 2026 with Python 3.12.14, Django 5.2.18 and SQLite in the provided Linux environment.

| Check | Outcome |
| --- | --- |
| Fresh migrations | All migrations applied successfully |
| Repeat setup script | Pinned dependency installation, migrations, seed and Django check completed again without rewriting demo records |
| Django system check | No issues |
| Migration drift check | No changes detected |
| Dependency check | No broken requirements |
| Automated tests | 52 passed, no skips or expected failures |
| File-backed SQLite test run | 52 passed, including two concurrent claims on the last portion using independent connections |
| Seed repeatability | Second run created zero new meals; user records, reservations and intentions preserved |
| Seed invariants | Test validates all historical closed meals reconcile, listing counters match reserved/collected rows, and a listing is immediately reservable/collectible |
| Chromium end-to-end | Student intention → kitchen forecast snapshot → prepare 20 → serve 19 → offer 1 → reserve → verify code → confirm collection → reconcile 20 = 19 + 1 → administrator report/CSV passed |
| Browser persistence | Code survives refresh and a separate authenticated browser session; collection and code also verified after stopping and restarting the actual server |
| Responsive browser checks | Desktop 1440px and mobile 390px screenshots inspected; student/kitchen/admin pages stayed within viewport; additional pickup-page checks at 768px and 1024px passed |
| Browser JavaScript errors | None during the complete journey |
| Local readiness | Landing, login, CSS and local SVG favicon return HTTP 200 |

Browser mutations used an isolated SQLite copy under `/tmp`, preserving the main seeded cook-along for presentation. Screenshot inspection found missing decorative glyphs; these were replaced with supported arrows and a local SVG logo. Heading and muted-text colours were adjusted for contrast. Responsive tables scroll inside their containers and can receive keyboard focus.

The initial 48-test suite passed before four additional edge-case checks were added. The final 52-test suite includes past-deadline withdrawal preserving no-show records, early publication rejection, missing-record publication rejection and service-level role checks. No required check remains failed or skipped.

`check --deploy` was also run under the local development defaults. It reported six expected deployment warnings: development debug/secret, insecure-cookie settings, HTTPS redirect and HSTS. The README documents deployment configuration; this validation does not establish production readiness, load capacity, campus food safety compliance, or measured environmental impact.

The reusable cloud `install_script` and `start_skill` were saved to the environment configuration draft. Saving is separate from publishing and does not establish restoration in a fresh cloud task. Review/save the draft in environment settings, then publish the environment through the product when desired.
