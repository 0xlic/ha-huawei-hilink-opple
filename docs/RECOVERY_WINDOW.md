# Short recovery window

Each lamp coordinator keeps a monotonic 30-second recovery deadline. An explicit
`homeassistant.update_entity` refresh opens/extends the deadline **before** waiting
for the lamp's lock. No additional polling task or network concurrency is created.

During recovery, regular polls use the same fresh-session, single-attempt read as
explicit refresh, with a 1-second timeout per socket request. The policy is checked
in the worker after lock acquisition. A regular poll already waiting for the lock
therefore sees the latest recovery deadline.

A normal poll already making a long request cannot be interrupted safely. If its
attempt fails after recovery starts, its second long attempt is suppressed. If its
session succeeds after recovery starts, the following state read uses the shorter
timeout. An already running multi-step handshake can still take longer than one
second; this is not a total-operation timeout.

Successful reads (including an off lamp) or confirmed control clear the window.
Failures keep it active until the deadline. Each explicit refresh renews it; if
refreshes stop, the policy expires naturally. Healthy-session timeouts return to
3.5 seconds. Normal poll interval remains 15 seconds and normal polling still has
up to two attempts. Control timeout/retry behavior is unchanged.

The existing automation YAML needs no changes. This addresses extra HA-side lock
waiting, not the lamp's power-on/Wi-Fi startup time.

## Logging

The verbose diagnostic code has been removed. Only ordinary HA coordinator error
logging remains. No request-by-request or timing log is emitted by this version.

## Install

Upload `opple-recovery-window.zip` to `/config`, then use Terminal & SSH:

```sh
cd /config &&
tar -czf "opple-backup-$(date +%Y%m%d-%H%M%S).tar.gz" custom_components/huawei_hilink_opple &&
unzip -o /config/opple-recovery-window.zip -d /config &&
ha core check &&
ha core restart
```

This package includes only integration files plus this guide, not runtime data,
credentials, tests, or standalone probes. Restart HA to load the new Python code.
