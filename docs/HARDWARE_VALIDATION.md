# MJWSD05MMC + PVVX hardware validation / 實機驗證

Status: MJWSD05MMC PVVX clock sync reported working by the user on 2026-10-01.
Firmware version and transport were not supplied. The complete checklist below
remains pending; mock tests do not establish its results.

## Prepare

Record model, hardware revision, PVVX version, HA version, local adapter or ESPHome
proxy version, active connection setting, HA timezone, whether GATT PIN is enabled
and whether advertisements are encrypted. Do NOT record actual PINs or bindkeys.
No flashing is performed by this integration. Keep other time-writing clients closed.

PVVX's README documents the MJWSD05MMC top button as its connect control. Enable
connectability according to your firmware. “Visible” is not always “connectable.”

## First sync

Enable integration debug logging before confirming discovery or pressing Sync time.
Expected sequence (timings/values vary):

```text
Device found advertisement matcher=known_local_name
Device found model=MJWSD05MMC connection source=<HA adapter/proxy identifier>
Services discovered: [..., 00001f10-0000-1000-8000-00805f9b34fb, ...]
PVVX service found=True ... control characteristic=00001f1f-0000-1000-8000-00805f9b34fb
MJWSD05MMC detected; firmware family=pvvx clock protocol=pvvx
PVVX Get Time payload sent length=1
PVVX time response received length=9
PVVX current clock time=<decoded instant>
PVVX Set Time payload sent length=5
PVVX time response received length=9
PVVX Get Time payload sent length=1
PVVX time response received length=9
PVVX read-back final error=-0.4 s
MJWSD05MMC clock synchronized successfully; firmware=pvvx protocol=pvvx
```

A 5-byte response is also legitimate; notification logs can precede “sent” because
BLE notifications may arrive before the write coroutine completes. The decoded
instant is normalized for comparison with HA; payloads contain local wall-clock epoch.
Never paste whole unrelated BLE captures containing keys into a public issue.

## Acceptance checks

- Physical display has HA local date/time. Last sync advances only after verification.
- Manual sync works through local Bluetooth and independently through an active proxy.
- Move out of range: Waiting, no repeated active connects. Return: one pending sync.
- Passive-only route: explicit no-active-connection result; no “unsupported device.”
- Simulate unavailable proxy slots: delayed retries at 5/15/30/60 minutes.
- Restart HA: last sync/protocol/history preserved; recent successful sync not repeated.
- Daily/weekly/cron work in HA timezone. Do not use a production HA timezone change
  casually: test DST/zone changes in a disposable HA instance or rely on mock coverage.
- With known PIN protection enabled: Authentication required, no endless retries,
  family/protocol remain PVVX if GATT discovery succeeded. Remove protection using
  your existing trusted tooling if desired, then manually retry (integration does not do this).
- Encrypted BTHome advertisements with open GATT: clock sync still works; sensor
  decryption remains managed by BTHome integration.

## Report template

```text
Model / hardware revision:
PVVX firmware version:
HA version:
Connection: local adapter / ESPHome proxy (version, active connections enabled)
HA timezone:
Advertisement encrypted: yes/no (no key)
GATT protected: yes/no (no PIN)
Protocol detected:
Response length: 5/9/other
Final read-back error:
Physical display correct: yes/no
Offline recovery / restart / schedule result:
Redacted integration DEBUG log:
```

Only after a real report passes these checks should a matrix row be marked
“Verified on real hardware,” specifying the exact firmware/hardware/connection path.
