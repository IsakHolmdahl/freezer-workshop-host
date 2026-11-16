# Freezer workshop host

The hosted side of the agent software factory workshop. One Docker Compose stack that
gives every team an Ica store with ten simulated freezers, attacks the stores at the
right moment, and shows the organisers where every team is.

**Keep this repository private.** It contains the attack. The teams work in the other
repository (the store cloud service) and only see `docs/store-api.md`.

## What a team sees

1. They open the site, enter the shared password and a team name.
2. They get a store, "Ica <Swedish city>" (every city is used once), with a store
   endpoint and a bearer token that works for that store only.
3. Their cloud service talks to the store API (`docs/store-api.md`).

## The three stages

| Stage | The team's factory builds | Detected by |
| --- | --- | --- |
| 1 Remote software installation | A rollout of software 2.0.0 to the ten freezers | All ten freezers report 2.0.0 |
| 2 Security fix | Fixes in how their service approves technician requests | All four waves repelled, genuine requests still approved |
| 3 New UI element | A new monitoring screen on the energy data | The store has fetched `/energy`; you press **Stage 3 done** after seeing the screen |

Stage 1 obstacles, the same in every store: `frys-07` is offline until 3 minutes after
the first install command; `frys-03` fails its first install (`checksum_mismatch`);
`frys-05` starts a defrost when the first command arrives; the door of `frys-09` is
open 45 seconds of every two minutes.

Stage 2 starts 60 seconds after stage 1 is done. Freezers on 2.0.0 forward technician
requests to the team's service, which must approve or reject each. Genuine requests
arrive every 45 seconds. The attacker adds a new wave every 4 minutes:

| Wave | The pass | Starting code lets it through because |
| --- | --- | --- |
| 1 | Not signed (`alg: none`) | The algorithm is taken from the pass itself |
| 2 | Signed, expired a day ago | Expiry is not checked |
| 3 | Signed, issued for another store | The audience is not checked |
| 4 | Fully valid | The request (70-day defrost, +55 °C) is not range-checked |

Waves 1 to 3 alternate a +50 °C request with a harmless-looking one, so a range check
alone does not repel them. A wave is **repelled** when the team's service has rejected
both of its variants, **breached** when it approved one. Released waves keep trying,
so a regression shows. An approved attack sets the freezer to +50 °C and the store
pulses red on the map; when the wave is repelled the freezer is put right again.
Not answering is no defence: requests expire after 90 seconds and count as nothing.

## Briefs and the factory twist

Stage 1's brief is in the team repository (`docs/features/remote-software-installation.md`).
The briefs for stages 2 and 3 are here, in `src/freezerhost/briefs/`, because the team
repository must not give them away. They are delivered automatically: a team's page
(`/`, opened by signing up or by entering the store token) shows the stage 2 brief when
the first wave is released and the stage 3 brief when all four waves are repelled.

In stage 2 the stock factory refuses the work. Every role's prompt ends with a visible
rule: security work only through a skill named `security-change-procedure`, otherwise
stop and escalate. The factory ships without that skill and without a security role.
The team has to write the skill and attach it to the roles that need it (or decide to
remove the rule, which is also a factory change worth discussing). The escalations
show up as friction on the Run comparison dashboard.

## The map (`/map`)

Everyone with the shared password sees the map and the list (waves are numbered
only; their names are shown to organisers). Logged in with the
organiser password you also get, per store: **Release next wave** (do not wait for
the timer), **Skip stage 1** (for a stuck team), **Stage 3 done**, **Delete**, and
the store's endpoint and token (for a team that lost them).

## Run it

Locally, without Docker: `make install`, then `make dev`, then http://localhost:8080
(passwords: defaults in `src/freezerhost/config.py`, or a `.env`).

On a server: `cp .env.example .env`, fill it in, `make up`. Step by step for AWS:
`docs/deploy-aws.md`.

`make test` and `make lint` check the code. Settings: `.env.example` and
`src/freezerhost/config.py`.

## Layout

| Path | What |
| --- | --- |
| `src/freezerhost/world.py` | Stores, freezers, installs, maintenance requests, the attacker. No HTTP; time is passed in. |
| `src/freezerhost/app.py` | The store API and the site (login, sign-up, map, organiser actions). |
| `src/freezerhost/passes.py` | The service desk's signing key and the technician passes. |
| `src/freezerhost/cities.py` | The fifty cities. |
| `src/freezerhost/static/` | The three pages. |
| `docs/store-api.md` | The contract with the teams' service. Keep it identical to the copy in the team repository. |

State (stores, tokens, signing key) is kept in the `host-data` volume and survives a
restart. Measurements of the last hour are kept in memory only.
