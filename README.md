Home Assistant custom component for Judo i-Soft Safe+

**Minimum Home Assistant version: 2025.6.0.** The transport uses its aiohttp
request middleware to prevent a disconnected request from being sent again.

## Local polling and stale readings

The integration polls the legacy local HTTPS API sequentially. If an individual read
fails, the last successful value for that entity is retained in memory. Each measured
entity exposes `last_success` (UTC timestamp) and `stale` (true when the entity was not
refreshed on the last poll). Check `stale` before trusting a cached state in automations.
A value that has never been read is unknown; the cache does not survive a restart.

Timed-out requests are not immediately repeated. Debug logs report elapsed time by
operation, not request URLs, tokens or response bodies.

## Offline tests

With the project dependencies installed, run `python -m unittest discover -s tests -v`.
