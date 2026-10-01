# Protocol evidence and support matrix

Research date: 2026-10-01. MJWSD05MMC PVVX clock sync has user-reported real-hardware
success on this date; firmware version, transport and full regression results were not supplied.
“Protocol tested only” means mocked GATT and payload tests, never hardware certification.

| Model | Firmware | Protocol | Service UUID | Characteristic UUID | Read Time | Set Time | Payload | Advertisement Encryption | GATT Authentication | Verification | Test Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| LYWSD02 | Stock | Xiaomi Stock | EBE0CCB0… | EBE0CCB7… | Yes | Yes | 5-byte `<Ib` | Irrelevant to clock | Requires open GATT | Read-back | Protocol tested only |
| LYWSD02MMC | Stock | Xiaomi Stock | EBE0CCB0… | EBE0CCB7… | Yes, require 5 bytes | Yes | 5-byte; reject other layouts | Irrelevant to clock | Requires open GATT | Read-back | Protocol tested only; firmware-dependent |
| MHO-C303 | Stock | Xiaomi Stock | EBE0CCB0… | EBE0CCB7… | Probe 5-byte | 5-byte only | Same service/layout, hardware validation required | Irrelevant to clock | Requires open GATT | Read-back | Protocol tested only; hardware confirmation required |
| MJWSD05MMC | Stock | Xiaomi Stock candidate | EBE0CCB0… | EBE0CCB7… | Inspect length | Disabled for unverified format | Reported 6 bytes; semantics unconfirmed | Irrelevant to clock | May require authentication | Not implemented for 6 bytes | Planned / unsupported |
| MJWSD05MMC | PVVX / BTHome | PVVX | 1F10 | 1F1F | Yes | Yes | GET `23`; SET `23` + uint32 LE; response 5 or 9 bytes | Encrypted BTHome is independent | Open GATT supported; PIN detected, not authenticated | SET response + fresh GET, <=10 s | User-reported hardware sync success (2026-10-01); full checklist pending |
| Other clock-capable PVVX devices | PVVX | PVVX | 1F10 | 1F1F | Yes when command supported | Yes when command supported | Same command | Independent | Same policy | Response + GET | Protocol tested only; model not inferred from protocol |
| CGD1 | Stock | Qingping CGD1 | 22210000… | 0001/0002/000b/000c | No verified time GET | Yes | 05 09 + UTC uint32 LE | Separate from clock | 16-byte pairing token | Matching command ACK + timezone read-back | Protocol tested only; hardware pending |

Full Xiaomi UUID suffix: `-7a0a-4b0c-8a1a-6ff2997da3a6`.
Full PVVX UUID: `00001f10-0000-1000-8000-00805f9b34fb` and
`00001f1f-0000-1000-8000-00805f9b34fb`.

## Pinned primary evidence

- [HA source/protocol](https://github.com/drslid/LYWSD02_BLE_Dashboard/tree/cd83b73f23aefc173b713457bc63f5f4cc4fe9f7/custom_components/lywsd02_sync): fractional offset handling; schedule and manager reference.
- [bluetooth-clocks Xiaomi](https://github.com/koenvervloesem/bluetooth-clocks/blob/5085e7a9fea384e04b2a2e472ad37b8759c17f52/src/bluetooth_clocks/devices/xiaomi.py): 5-byte `<Lb`; write without response.
- [bluetooth-clocks PVVX](https://github.com/koenvervloesem/bluetooth-clocks/blob/5085e7a9fea384e04b2a2e472ad37b8759c17f52/src/bluetooth_clocks/devices/pvvx.py): local epoch and notification reference. Its fixed 9-byte decoder is incomplete for current firmware. Its connection layer is NOT reused.
- [PVVX command parser](https://github.com/pvvx/ATC_MiThermometer/blob/5c92fcfde3e20aece0af49b651b9a1ee0ed95ea3/src/cmd_parser.c): CMD_ID_UTC_TIME, 5-byte response or 9 bytes with SERVICE_TIME_ADJUST; last uint32 is last set time, not ACK status. Unknown command responds `[command, ff]`.
- [PVVX GATT/security](https://github.com/pvvx/ATC_MiThermometer/blob/5c92fcfde3e20aece0af49b651b9a1ee0ed95ea3/src/app_att.c): 1F10/1F1F; PIN enables ATT_PERMISSIONS_SECURE_CONN_RDWR.
- [MJWSD05MMC display](https://github.com/pvvx/ATC_MiThermometer/blob/5c92fcfde3e20aece0af49b651b9a1ee0ed95ea3/src/lcd_mjwsd05mmc.c) and rtc_pcf85163.c: firmware displays counter/RTC directly, without a timezone field. Despite `utc_time_sec` naming, send local wall-clock epoch, matching bluetooth-clocks.
- [LYWSD02MMC reference](https://github.com/fildunsky/LYWSD02MMC-widget) and upstream dashboard docs distinguish time from optional 7-byte display-format commands; we do not change display format.

- [ESPHome BLE Client official example](https://esphome.io/components/ble_client/#ble_clientconnect-action): MHO-C303 uses the same CCB0/CCB7 service and five-byte local-time-plus-zero-offset format. This is protocol evidence, not a hardware test of this integration.

## Xiaomi encoders

XiaomiTime5: timestamp uint32 LE, timezone signed int8 hours. Floor the HA offset
hours and add remaining fractional seconds to timestamp. Decode displayed epoch as
`timestamp + hours*3600`. Compare with `HA timestamp + current HA UTC offset`.
This supports negative offsets, +05:30, +05:45 and DST without host-timezone reliance.
Require exactly 5 bytes. Read failures do not suppress authentication errors.
The implementation deliberately requires a valid read before write: safe fallback
is optional and not enabled without per-firmware evidence.

Six-byte encoding is deferred per the updated user scope. Length 6 alone does not
establish trailing-byte meaning; no packing/preserving of an assumed “reserved” byte.
MJWSD05MMC stock is blocked even if configured by model and a 5-byte value appears,
until that firmware has evidence. Unknown 6-byte stock always fails safely.

## PVVX exchange

Subscribe before sending. Accept only notifications for 0x23; ignore unrelated
notifications without logging their bytes (other commands could contain secrets).
GET = one byte 0x23. SET = 0x23 + uint32 little-endian local epoch.
Responses must have 5 or 9 bytes; 0x23ff means unsupported, not authentication.
SET response is an echo of device time, not a generic ACK. Validate it, then issue
fresh GET and compare <=10 seconds against fresh HA-local time. Use receipt time,
not the pre-connection timestamp. Clean up notification subscriptions in finally.
No guessed PIN, pairing or advertisement-key reuse.

Scope update: user explicitly deferred MJWSD05MMC stock firmware. Only its PVVX variant is in v1 scope; six-byte stock codec implementation is deferred.

## CGD1 addition (v0.2.0)

References inspected before implementation:
- [HA integration d9c072b](https://github.com/rjocoleman/ha-qingping-cgd1/tree/d9c072b258236b8b26c68414630d08fc6ac16cd0).
- [Protocol library 855a955](https://github.com/rjocoleman/qingping-cgd1/tree/855a955ea6b3452819c7f974157da383e987a971), especially const.py, client.py, codec.py and tests.

Vendor service `22210000-554a-4546-5542-46534450464d`; short characteristics
0001 auth/time write, 0002 auth notify, 000b data write, 000c data notify (Bluetooth
base UUID). Authenticate with `11 01 + token` then `11 02 + token` (16-byte token).
Auth ACK alone is insufficient; privileged settings read `01 02` must succeed.
Settings response is exactly 20 bytes, header `13 01` or `13 02`. Preserve the raw
settings byte-for-byte except write header, offset magnitude byte 6 and sign byte 13.
Offset magnitude is abs(minutes)//6; sign 1 positive, 0 negative. Like the reference,
rare non-six-minute zones (e.g. +05:45) quantize; do not invent a different protocol.
Time write is `05 09 + uint32 UTC epoch LE` on 0001. ACK `04 ff subcommand ? status`
must match subcommand 09, with status 00 or 09. Verify timezone via fresh settings
read; there is no source-backed clock-time read-back, so ACK is the time verification.
Discovery fdcd service data must match model byte 0c, not every Qingping sensor.
Token is a CGD1 pairing credential stored only in HA config-entry data, never a
sensor bindkey, log payload or diagnostic field. New pairing uses a random token;
existing pairing requires its token or a user-performed reset. No reset command is sent.
