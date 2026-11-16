# Store API

The freezers of your store are reached through this HTTPS API. It is hosted by the
workshop organisers; you cannot change it. Your cloud service is the only client.

- Base URL: the **store endpoint** you got at sign-up, for example
  `https://<host>/api/v1/stores/ica-kiruna`. All paths below are relative to it.
- Authentication: `Authorization: Bearer <store token>` on every request. The token
  does not expire and works for your store only.
- Bodies are JSON. Times are ISO 8601 in UTC (`2026-11-20T09:15:02Z`).
- Errors: `{"detail": {"code": "<machine code>", "message": "<text>"}}`.
  `401` missing or wrong token, `403` token belongs to another store, `404` unknown
  resource (codes `unknown_freezer`, `unknown_request`, `unknown_version`), `409` the request cannot be done right now (see the code), `422` invalid body.

## Store

`GET /` returns the store.

```json
{"id": "ica-kiruna", "name": "Ica Kiruna", "city": "Kiruna", "team": "Team Rocket",
 "freezer_count": 10, "current_software": "1.4.2"}
```

## Freezers

`GET /freezers` returns all freezers; `GET /freezers/{freezer_id}` returns one.

```json
{
  "id": "frys-03",
  "name": "Frys 3",
  "online": true,
  "last_seen": "2026-11-20T09:15:00Z",
  "software_version": "1.4.2",
  "temperature_c": -18.4,
  "setpoint_c": -18.0,
  "door_open": false,
  "defrosting": false,
  "install": null
}
```

An offline freezer keeps its last known values; `last_seen` shows how old they are.

`GET /freezers/{freezer_id}/readings?since=<time>&limit=<n>` returns measurements,
oldest first, one every 5 seconds, at most the last hour. `since` is exclusive;
`limit` defaults to 200 (maximum 1000).

```json
[{"ts": "2026-11-20T09:15:00Z", "temperature_c": -18.4, "setpoint_c": -18.0,
  "door_open": false, "defrosting": false}]
```

## Software

`GET /software` lists the software that can be installed.

```json
[{"version": "1.4.2", "released": "2026-03-02", "notes": "Current software."},
 {"version": "2.0.0", "released": "2026-11-10",
  "notes": "Adds remote maintenance by service technicians."}]
```

`POST /freezers/{freezer_id}/install` with `{"version": "2.0.0"}` tells one freezer to
install a version. Answer `202` with the install:

```json
{"id": "inst-0007", "freezer_id": "frys-03", "version": "2.0.0",
 "state": "downloading", "progress_pct": 0, "error": null,
 "started_at": "2026-11-20T09:16:00Z", "finished_at": null}
```

The freezer then works by itself. Follow it in the `install` field of the freezer
(the latest install of that freezer), or with `GET /installs` (all installs of the
store, oldest first).

| `state` | Meaning |
| --- | --- |
| `downloading` | The freezer fetches the software. `progress_pct` rises 0–100. |
| `installing` | The freezer installs and restarts. |
| `installed` | Done. `software_version` of the freezer is now the new version. |
| `failed` | Not installed; `error` says why. The freezer keeps its old version and can be told to install again. |

The command is refused with `409` and one of these codes:

| `code` | Meaning |
| --- | --- |
| `offline` | The freezer cannot be reached. Try again later. |
| `busy` | The door is open or the freezer is defrosting. Try again later. |
| `install_in_progress` | The freezer is already installing something. |
| `already_installed` | The freezer already runs that version. |

`404` with code `unknown_version` when the version is not in `GET /software`.

## Maintenance requests

Freezers with software 2.0.0 or newer accept remote maintenance by service
technicians. A freezer does not decide by itself: it forwards every request to the
store's cloud service, which must approve or reject it. A request nobody decides
within 90 seconds expires and is not carried out.

`GET /maintenance-requests?status=pending` lists requests (`status` is optional:
`pending`, `approved`, `rejected`, `expired`), oldest first, at most the last 200.

```json
{
  "id": "mr-0042",
  "freezer_id": "frys-03",
  "action": "set_setpoint",
  "params": {"setpoint_c": -20.0},
  "pass": "<technician pass>",
  "status": "pending",
  "created_at": "2026-11-20T10:02:11Z",
  "expires_at": "2026-11-20T10:03:41Z",
  "decided_at": null,
  "reason": null
}
```

| `action` | `params` | Allowed range (freezer manual) |
| --- | --- | --- |
| `set_setpoint` | `{"setpoint_c": <number>}` | −30.0 to −12.0 °C |
| `defrost` | `{"duration_minutes": <integer>}` | 5 to 60 minutes |

`POST /maintenance-requests/{request_id}/decision` with
`{"decision": "approve"}` or `{"decision": "reject", "reason": "<text>"}` decides a
request. An approved request is carried out by the freezer at once. `409` with code
`already_decided` when the request is no longer pending.

### Technician pass

Every request carries a pass: a JSON Web Token issued by the service desk to one
technician for one store, valid for a short time.

`GET /technician-pass-key` returns what is needed to check a pass:

```json
{"algorithm": "EdDSA", "issuer": "service-desk", "audience": "ica-kiruna",
 "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n"}
```

Claims of a pass: `iss` (always `service-desk`), `aud` (the id of the store the pass
is for), `sub` (the technician, for example `tech-0417`), `iat`, `exp`, `jti`.

## Energy

`GET /energy?hours=<n>` returns energy use and door openings per freezer and hour,
oldest first, for the last `n` hours (default 24, maximum 168).

```json
[{"freezer_id": "frys-03", "hour": "2026-11-20T08:00:00Z",
  "energy_kwh": 1.42, "door_openings": 17, "avg_temperature_c": -18.2}]
```
