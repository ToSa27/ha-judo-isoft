Home Assistant custom component for Judo i-Soft Safe+

**Minimum Home Assistant version: 2025.6.0.** The transport uses its aiohttp
request middleware to prevent a disconnected request from being sent again.

## Local polling and stale readings

The integration polls the legacy local HTTPS API sequentially. If an individual read
fails, the last successful value for that entity is retained in memory. Each measured
entity exposes `last_success` (UTC timestamp) and `stale` (true if a due read did
not produce a valid value). A daily reading is not stale just because the current
poll did not schedule it. Check `stale` before trusting a cached state in automations.
A value that has never been read is unknown; the cache does not survive a restart.

Timed-out requests are not immediately repeated. Debug logs report elapsed time by
operation, not request URLs, tokens or response bodies.

## Extra telemetry sensors

The first refresh keeps the original three requests; the extra read-only sensors
are queried starting with the next regular refresh. Salt range, salt quantity, raw
water hardness and residual hardness are attempted at most once every 24 hours per
integration load. Residual capacity, current flow rate and regeneration status are
read at every regular refresh (normally hourly). Flow and regeneration are snapshots,
not continuous monitoring. The integration only reads the regeneration status; it does
not start or stop a regeneration.

Residual capacity uses the percentage explicitly provided by the API. Salt range,
salt quantity, flow rate and hardness are shown as raw integers without guessed units until
the values can be matched to the appliance display. Firmware revisions that do not
return a recognized value leave the sensor unknown, or stale if previously read.

## Offline tests

In a fresh Python 3.13 virtual environment, install the same dependencies as CI
and run the offline tests:

```sh
python -m pip install -r .github/requirements-ci.txt
python -m unittest discover -s tests -v
```
