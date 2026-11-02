# Notes for Karim

## Resume bullets

- Built a multi-tenant Kanban SaaS (Django, PostgreSQL, React) with tenant isolation enforced twice, by ORM scoping and PostgreSQL row-level security on 6 tables, verified by 83 backend tests that run as an unprivileged database role.
- Implemented drag-and-drop ordering with fractional ranks and optimistic concurrency; in a race of 16 clients over 20 rounds on one card, exactly 20 moves succeeded and 300 received HTTP 409, with no lost updates.
- Delivered real-time board updates with Django Channels and Redis at 105 ms p50 from request to WebSocket message, and found and fixed a production-only Redis timeout that dropped idle connections about every 5 seconds, adding a regression test against real Redis in CI.

Be ready to say the throughput was low (9 moves/s on one client on a busy laptop) if someone asks. It's in the README, and explaining what you'd change is a good answer.

## Five likely interview questions

**1. Why shared-schema multi-tenancy? What are the alternatives?**
Compare a database per tenant (best isolation, painful migrations and connections), a schema per tenant (a middle ground, and migrations still multiply) and shared tables (cheapest, but one missing filter leaks data). You kept shared tables and added RLS to cover the weakness. Be honest about the costs: noisy neighbours, and restoring one tenant is harder. See `core/migrations/0002_row_level_security.py`.

**2. How does RLS know which tenant is current?**
After JWT auth, `TenantViewMixin.initial()` checks membership and calls `tenancy.activate()`, which runs `set_config('app.tenant_id', ...)`. The policy compares each row with that setting. Then explain the trap: superusers and table owners bypass RLS, so the app connects as `pm_app`. Empty means `NULLIF` gives NULL, the comparison is never true, and no rows match.

**3. What happens when two people move the same card?**
`move_issue` takes `SELECT ... FOR UPDATE` on that issue, then compares versions. The second request sees a newer version and gets a 409 with the current state, and the client reloads. Know why you don't hold a lock during the drag (it can last seconds). Also know why `of=("self",)` matters: without it, the join would lock the project row too and every move in the project would queue.

**4. Why send the WebSocket message on commit rather than straight after saving?**
If you send first and the transaction rolls back, everyone sees a change that never happened. Another client could also fetch before commit and see old data. `transaction.on_commit` solves both, and a test proves a rollback sends nothing. The catch is that a crash between commit and publish loses that message, so clients reload the board on reconnect. An outbox table would be the stronger design.

**5. How does fractional ranking work, and what's its weakness?**
`between(low, high)` makes a string that sorts between two neighbours, so a move rewrites one row. Keys never end in the lowest digit, so there's always room before any key. Sorting needs the `C` collation, or the database order won't match Python's. The weakness is that keys grow with repeated inserts in one spot, and two concurrent moves into one gap can produce the same key, which is why `_rebalance` exists.

## The trickiest parts

**The tenant is stored in two places.** `tenancy.py` has a contextvar for Python and a session setting for PostgreSQL. `deactivate()` always clears the database setting, even when the contextvar looks empty. Under ASGI, the signal can run in a different context from the view, so an empty contextvar proves nothing about the connection.

**Tests had to run as the app role.** The test database is created by `postgres`, a superuser that would silently bypass RLS, and then every RLS test would be meaningless. The autouse fixture in `tests/conftest.py` runs `SET ROLE pm_app`. If you ever want to show this off, remove that fixture and watch the RLS tests fail.

**The Redis bug only showed up in the real stack.** All tests passed with the in-memory layer, but the browser kept saying "Connecting". The logs showed `redis.exceptions.TimeoutError`. The channel layer blocks on Redis for 5 seconds, and the socket timeout was around the same length, so each idle read could crash the consumer and drop the socket. The fix is one line in `settings.py`. This is a great interview story: use it for "tell me about a hard bug". The lesson is to test against real dependencies.

**The audit log is append-only in the database.** The migration revokes UPDATE and DELETE from `pm_app`, then grants UPDATE on only `actor_id`, because deleting a user has to set that column to NULL. So even the application can't rewrite history. The database owner still can, so this isn't tamper-proof against an administrator.

**`placementFor` in `boardState.ts` is easy to get wrong.** Dragging down means landing below the card under the pointer, and dragging up means above, because the dragged card's own slot disappears. Read the tests alongside it.

## Known limits to be upfront about

- Removing a member doesn't close their open WebSocket; access is checked on connect.
- Session-level `set_config` doesn't work with PgBouncer in transaction mode.
- Reminders are at-least-once: a crash just after sending an email can send it again.
- The throughput numbers are low and I haven't profiled them yet. They came from one Daphne process on a busy laptop on battery.
