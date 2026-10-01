# CGD1 clock-only setup and hardware validation

CGD1 support was added in v0.2.0. The user reported successful clock synchronization
with a physical CGD1 on 2026-10-01, ahead of v1.0.0. Exact firmware, connection path
and the complete regression checklist were not supplied. The handler also has mocked
protocol/HA tests.
The upstream author reports tests against firmware 1.0.1_0130; that is upstream
evidence, not a validation claim for this new handler.

## Setup

Use BLE Clock Sync's discovered CGD1 entry. If using an already-paired device, enter
its existing 16-byte token as 32 hex characters. For an unpaired clock, submit the
empty token field to generate a random credential. The token is stored before the
first connection so successful binding survives a failed subsequent sync/restart.

A clock bound to another token needs that token or a user-performed unpair/reset.
The integration does not factory-reset or claim to retrieve credentials from other
integrations. Refer to the upstream README for long-press / Qingping+ unpairing.
Keep another app from binding immediately after unpairing. Use an active Bluetooth
proxy or a local HA Bluetooth adapter.

After a token error, Options → Change CGD1 pairing token → enter the correct token
(or generate one after unpairing). This operation preserves existing schedules and
retries immediately after reload. Authentication timeouts can also result from
poor radio conditions; verify connectivity before assuming a reset is needed.

## Acceptance checklist

- Confirm protocol `qingping_cgd1`, family `qingping_stock`, model CGD1.
- Check physical time/date after Sync time; inspect Last sync and Sync status.
- Verify existing alarm enable state, volume, brightness, language and night mode
  have not changed. The handler writes back raw settings with only timezone fields
  and the mandatory write header updated.
- Test +08:00, -05:00, +05:30 in a disposable HA instance, not a production timezone.
  The source format uses 6-minute steps; +05:45 quantizes toward zero to +05:42.
- Confirm repeated sync with saved token and HA restart does not require re-pairing.
- Wrong token must not result in Synced even if authentication writes receive ACKs.
- Confirm connection cleanup after a rejected ACK, timeout and cancellation.
- Check local and proxy paths separately; report which one was actually tested.
- Token must not appear in diagnostics, normal/debug logs or runtime sync-history Store.
  Do not share the config-entry file, which legitimately contains the pairing secret.

Time itself has no source-verified GET command. Verification means the correct
0x09 command ACK succeeded AND the timezone read-back matched; it does not claim a
measured clock error. CGD1 time packets use UTC epoch, unlike PVVX local epoch.

## Safe DEBUG milestones

CGD1 GATT probe → authentication verified by privileged settings read → timezone
ACK (when changed) → time ACK subcommand 0x09 → timezone read-back verified → synced.
No token/settings bytes or unrelated control-channel payloads are logged.

Report model, firmware, HA version, adapter/proxy, timezone, physical display result,
restart/offline outcomes and a redacted log. Never report the actual token.
