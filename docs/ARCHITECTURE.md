# BLE Clock Sync architecture

Research date: 2026-10-01. Implementation starts after these design documents.

## Sources and retained design

The requested HA source lives on `feature/home-assistant-clock-sync`, not `main`:
[drslid/LYWSD02_BLE_Dashboard at cd83b73](https://github.com/drslid/LYWSD02_BLE_Dashboard/tree/cd83b73f23aefc173b713457bc63f5f4cc4fe9f7/custom_components/lywsd02_sync).
Retain its HA Bluetooth callbacks, bleak-retry-connector transport, shared connection
lock, daily/weekly/cron options, Store history, offset monitoring and advertisement
backoff. Extend these with protocol handlers, read-back verification, explicit error
classes, durable retry/cache state and firmware changes. Preserve upstream MIT notice.

## Data flow

HA advertisements -> candidate matcher -> user confirmation -> config entry by address
-> clock manager -> HA connectable BLEDevice -> bleak-retry-connector -> GATT probe
-> XiaomiStockProtocol / PVVXProtocol / CGD1Protocol -> entities and storage.

Name/service/product matching only selects candidates. Never connect unsolicited to
all nearby BTHome devices. BTHome UUID alone is not proof of PVVX; manually selecting
a known HA-discovered address is allowed. GATT service AND characteristic confirm the
protocol. Only read-only probes occur until the selected device has a known protocol.
Hardware model is independent metadata, from known name/product or user selection;
ATC_/BTH_ names do not prove MJWSD05MMC hardware. Address remains device identity
across flashing. No raw advertisements, bindkeys or PINs in storage/diagnostics. CGD1 pairing
tokens live only in HA config-entry data and are excluded from runtime Store/diagnostics.

## Ownership

Manager: connection, lock, retry, schedule, status, storage, entity notifications.
Protocol: UUIDs, length validation, encode/decode, GET/SET, notifications, verification.
Each operation receives fresh HA-local time; epoch conversion stays in protocols.
Clock drift measurement was removed in v0.2.0. Read operations remain for safe
format detection, CGD1 authentication and protocol-specific verification.

Local Bluetooth and ESPHome proxy use exactly the same HA BLEDevice path. Resolve
again when the lock is acquired, and supply a BLEDevice callback to the connector.
No scanner and no BleakClient(address). Missing connectable path with passive
visibility has its own error, distinct from out of range. Proxy active connections
must be enabled; a device may also require its physical connect button.

## Lifecycle and scheduling

One integration-wide asyncio lock serializes connections; per-manager in-flight task
coalesces manual/background requests. Always disconnect, including failures/cancel.
One scheduled callback plus one retry callback per device; unload cancels both and
awaits the in-flight task. Persist last sync, protocol/family, timezone/offset,
status/result, pending state, failures and retry deadline. Restart restores history,
resumes pending retries and catches missed schedules.

Manual/daily/weekly/5-field cron. Minimum automatic interval one hour is enforced at
runtime as well as options validation (not just a short sample of cron dates).
Manual button and timezone corrections are deliberate exceptions; failed attempts
use 5/15/30/60-minute backoff. Offline requests wait for HA advertisements without
polling. Retry deadline can initiate a connection only if HA still has a path.
Offset/timezone checks reschedule and correct DST, including changes while HA stopped.

## Cache and errors

Cache the successful protocol ID and firmware family, not GATT handles. Reuse handler
selection but validate its service/characteristic presence. Missing characteristic,
service changed or unsupported command clears persisted protocol and BLE service
cache, disconnects, then reconnects and probes once. Never loop indefinitely.
Authentication errors at connect/notify/read/write/verify become
`authentication_required`, preserving protocol metadata where known and stopping
automatic retries until a manual attempt. Do not infer PIN specifically from generic
BLE auth errors: report authentication required (PVVX PIN is a possible cause).
Unsupported layouts stop automatic retries and never write guessed bytes.

## Entities

One HA device (Bluetooth connection + stable identifier). Button: Sync Time.
Sensors: Last Sync, Next Sync, Clock Protocol, Firmware Family,
Sync Status and Last Sync Result. Diagnostics use lowercase enum values and English /
Traditional Chinese translations. No temperature/humidity/battery duplication.

Scope update: user explicitly deferred MJWSD05MMC stock firmware. Only its PVVX variant is in v1 scope; six-byte stock codec implementation is deferred.

CGD1 uses the same HA connection/global lock. After token auth writes, a privileged
settings read proves authentication; auth ACK alone is insufficient. A password field
accepts an existing token or explicitly generates one for an unpaired device. Options
can replace the credential while preserving the schedule. A non-secret revision counter
allows a previously blocked manager to retry after token changes. Registry migration
removes only this entry's obsolete drift sensor.
