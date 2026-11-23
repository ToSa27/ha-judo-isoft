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

Residual capacity is shown in percent; raw and residual water hardness are shown
in degrees of German hardness (`°dH`). Salt range, salt quantity and flow rate are
shown as raw integers until their units can be verified. Firmware revisions that do not
return a recognized value leave the sensor unknown, or stale if previously read.

Today's and this month's consumption are read in litres from the device's history
at each regular refresh, using Home Assistant's local calendar. The eight daily
buckets and the month's per-day buckets are summed, skipping `-1` (missing data).
An all-missing reply leaves the last value for that same period stale, or unknown if
no valid value exists; it is not zero. At a calendar change, a value from the
previous day/month is not carried over under the new period's name. Make sure the
JUDO and Home Assistant use the same local date. Period totals are display sensors;
use the existing lifetime counter for Home Assistant long-term consumption statistics.

## Offline tests

In a fresh Python 3.13 virtual environment, install the same dependencies as CI
and run the offline tests:

```sh
python -m pip install -r .github/requirements-ci.txt
python -m unittest discover -s tests -v
```
