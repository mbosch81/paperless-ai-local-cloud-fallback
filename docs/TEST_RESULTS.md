# Executed local checks

Date: 2026-10-14. Environment: macOS, Python 3.15.0. The prepared CI targets Python 3.12; remote CI has not run.

| Check | Result | Scope |
| --- | --- | --- |
| `python3 -m unittest discover -s tests -v` | 22 passed in 29.755 seconds | Reference handler and loopback simulated local/cloud HTTP backends |
| `python3 -m compileall -q adapter tests scripts` | Passed | Python source syntax |
| `docker compose config --quiet` | Passed | Compose parsed with temporary example config and synthetic credential; no Docker services started |
| Publication pattern scan | Passed | Source, docs, example config, and CI files |
| Inspection-specific internal-data denylist | Passed | No deployment addresses, machine identifiers, internal entities, personal names, or deployment paths in publication files |
| Adapter source formatting | AST equivalence confirmed | Formatting only after tests; no control-flow changes |

Initial local socket creation was denied by the execution sandbox with `PermissionError: [Errno 1] Operation not permitted`. The same isolated tests succeeded after authorized execution outside that sandbox. Initial SSH access was also network-sandbox blocked; SSH authentication subsequently succeeded. These were execution-environment restrictions, not production provider failures.

The test fixture uses a synthetic credential solely to assert header construction. No real OpenAI request, production document, upstream hostname resolution, or production outage was used. Outbound cloud TLS is not exercised because the HTTP transport is mapped to loopback.

No performance benchmark, uptime guarantee, hardware verification, classification quality metric, or end-to-end production result is claimed.
