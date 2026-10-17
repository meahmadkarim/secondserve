# Architecture

SecondServe is a single Django application with a server-rendered interface and SQLite persistence. `config` provides infrastructure; `core` groups closely related pilot domains instead of adding cross-app indirection. Templates remain presentation-only. Forms parse and validate input, views authenticate and authorise HTTP requests, and named service functions reload authoritative records and enforce business rules.

## Stored records

- Django User → Profile (student/kitchen/admin, optional campus identifier).
- Singleton CampusSettings → name, timezone and forecast assumptions.
- MealService → menu, time window, lifecycle, cumulative quantities and final outcomes. Operational foreign keys use PROTECT so user/meal deletion cannot erase history.
- MealIntention → one response per student/service, with created/updated timestamps.
- ForecastSnapshot → one service's counts, assumptions, capacity, calculations and override, saved when preparation begins. JSON preserves the original arithmetic. The application exposes no snapshot-edit action.
- SurplusListing → one per service; offered and committed quantities, pickup window, confirmation, notes, withdrawal reason, creator.
- Reservation → one-portion records with a random unique code, state and responsible collection staff/time. Cancelled records are retained; a partial unique index blocks simultaneous reserved/collected records for one student/listing.
- AuditEvent → actor, action, object reference, changes/reason and time. These records are reviewed by administrators; codes and individual intentions are not logged.
- PickupAttempt → per-staff invalid-code counter and window start.

Database constraints supplement service validation for timings, capacity, quantities and uniqueness. Cross-table accounting is enforced in transactional services. Local SQLite lacks row-level `select_for_update`, so every service mutation starts an IMMEDIATE transaction. This serialises writers before reads. `reserve` also performs a conditional `committed < offered` update; increment and reservation insert roll back together. Cancellation, withdrawal, expiration and collection follow the same transaction policy. A controlled lock failure produces a retry message. Tests use two actual threads and independent connections for the last portion, rather than mocking an inventory result.

## Boundaries and semantics

An intention forecasts a normal meal; it reserves nothing. A regular serving is recorded by the kitchen, not inferred from an intention. A surplus reservation commits one offered portion; only a staff-confirmed collection contributes to sharing metrics. Expiration checks happen on each request, not only in a scheduled job. Reports compute effective no-shows from deadlines before persistence catches up. Expiring a reservation does not decide what happened to food.

At closure, staff reconcile prepared portions into normal servings, collected surplus, discarded and retained/transferred. No active reservations may remain and the equality must hold exactly. Earlier totals freeze on entering surplus collection; explicit corrections are validated and audited.

## Permissions and request handling

The Profile role is the application's authority, independent of submitted role fields. Public registration exposes no role or staff flags. Students access only their own reservations. Kitchen users see aggregate intentions and the display name needed to hand over a portion. Administrators manage roles, campus settings and audit history and alone can export reports. Login-required/role decorators enforce view boundaries, and services enforce roles again so mutations cannot bypass policy through another view. All mutations use POST and CSRF protection. CSV cells with formula-like prefixes are neutralised.

The app uses campus-local input/display with timezone-aware storage. Assets, charts-as-value-tables and decoration are local. No external runtime service is involved.

## Prototype limits

SQLite is a shared writer bottleneck; contention can require a retry. Large reporting queries are intentionally simple and not optimised for institutional scale. Audit events are not tamper-proof. The staff account throttle does not replace network/login abuse controls. Service state changes are explicit human actions; there is no job queue or automated safety decision. Authentication has no email recovery or SSO. Manual review of real campus procedures, deployment hardening and privacy policy is required before a real pilot. This prototype makes no production-readiness or measured-impact claim.
