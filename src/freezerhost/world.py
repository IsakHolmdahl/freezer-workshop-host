"""The simulated world: stores, their freezers, installs, maintenance requests and the attacker.

No HTTP here. Time is a float (epoch seconds) handed in by the caller, so tests control it.
"""
from __future__ import annotations

import logging
import os
import random
import secrets
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .cities import CITIES
from .config import Settings
from .passes import ALGORITHM, ISSUER, PassIssuer

log = logging.getLogger(__name__)

NORMAL_SETPOINT = -18.0
ACTIVE = ("downloading", "installing")
TECHNICIANS = ["tech-0417", "tech-0233", "tech-0581", "tech-0126"]

# Scripted obstacles of stage 1, the same in every store.
OFFLINE, FAILS_FIRST, DEFROSTS, DOOR = "frys-07", "frys-03", "frys-05", "frys-09"

# Stage 2. Per wave: the kind of pass, and two request variants sent in turn. For waves 1-3 the
# second variant is within the allowed ranges, so range checks alone do not repel the wave.
WAVES = [
    ("unsigned", [("set_setpoint", {"setpoint_c": 55.0}), ("set_setpoint", {"setpoint_c": -19.0})]),
    ("expired", [("set_setpoint", {"setpoint_c": 52.0}), ("defrost", {"duration_minutes": 20})]),
    ("other_store", [("set_setpoint", {"setpoint_c": 58.0}), ("set_setpoint", {"setpoint_c": -17.5})]),
    ("valid", [("defrost", {"duration_minutes": 100800}), ("set_setpoint", {"setpoint_c": 55.0})]),
]
WAVE_NAMES = ["Unsigned pass", "Expired pass", "Pass for another store", "Valid pass, absurd request"]
BRIEFS = {stage: (Path(__file__).parent / "briefs" / f"stage-{stage}.md").read_text() for stage in (1, 2, 3)}
STAGES = {1: "Remote software installation", 2: "Security fix", 3: "New UI element", 4: "Done"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Install(BaseModel):
    id: str
    freezer_id: str
    version: str
    state: str = "downloading"
    progress_pct: int = 0
    error: str | None = None
    started_at: float
    finished_at: float | None = None
    will_fail: bool = False

    def view(self) -> dict:
        return {"id": self.id, "freezer_id": self.freezer_id, "version": self.version, "state": self.state,
                "progress_pct": self.progress_pct, "error": self.error, "started_at": iso(self.started_at),
                "finished_at": iso(self.finished_at)}


class Freezer(BaseModel):
    id: str
    name: str
    online: bool = True
    last_seen: float
    software_version: str
    temperature_c: float = NORMAL_SETPOINT
    setpoint_c: float = NORMAL_SETPOINT
    door_open: bool = False
    defrost_until: float | None = None
    install: Install | None = None
    tried_versions: list[str] = Field(default_factory=list)
    breached_by: int | None = None  # wave index whose request this freezer carried out

    def view(self) -> dict:
        return {"id": self.id, "name": self.name, "online": self.online, "last_seen": iso(self.last_seen),
                "software_version": self.software_version, "temperature_c": round(self.temperature_c, 1),
                "setpoint_c": self.setpoint_c, "door_open": self.door_open,
                "defrosting": self.defrost_until is not None,
                "install": self.install.view() if self.install else None}


class Request(BaseModel):
    id: str
    freezer_id: str
    action: str
    params: dict
    technician_pass: str
    status: str = "pending"
    created_at: float
    expires_at: float
    decided_at: float | None = None
    reason: str | None = None
    wave: int | None = None      # None: a genuine technician
    variant: int | None = None

    def view(self) -> dict:
        return {"id": self.id, "freezer_id": self.freezer_id, "action": self.action, "params": self.params,
                "pass": self.technician_pass, "status": self.status, "created_at": iso(self.created_at),
                "expires_at": iso(self.expires_at), "decided_at": iso(self.decided_at), "reason": self.reason}


class Wave(BaseModel):
    released_at: float | None = None
    next_at: float = 0.0
    attempts: int = 0
    results: dict[str, str] = Field(default_factory=dict)  # variant -> last decision

    @property
    def state(self) -> str:
        if self.released_at is None:
            return "waiting"
        if "approved" in self.results.values():
            return "breached"
        if len(self.results) == 2:
            return "repelled"
        return "attacking"


class Store(BaseModel):
    id: str
    name: str
    city: str
    lat: float
    lon: float
    team: str
    token: str
    created_at: float
    freezers: list[Freezer]
    installs: list[Install] = Field(default_factory=list)
    requests: list[Request] = Field(default_factory=list)
    waves: list[Wave] = Field(default_factory=lambda: [Wave() for _ in WAVES])
    sequence: int = 0
    last_call_at: float | None = None
    first_install_at: float | None = None
    stage1_done_at: float | None = None
    stage2_done_at: float | None = None
    stage3_seen_at: float | None = None
    stage3_done_at: float | None = None
    next_legit_at: float = 0.0
    last_legit: str | None = None
    legit_approved: int = 0
    legit_rejected: int = 0

    def freezer(self, freezer_id: str) -> Freezer:
        for freezer in self.freezers:
            if freezer.id == freezer_id:
                return freezer
        raise ApiError(404, "unknown_freezer", f"No freezer {freezer_id} in this store.")

    def next_id(self, prefix: str) -> str:
        self.sequence += 1
        return f"{prefix}-{self.sequence:04d}"

    @property
    def stage(self) -> int:
        done = (self.stage1_done_at, self.stage2_done_at, self.stage3_done_at)
        return 4 if done[2] else 3 if done[1] else 2 if done[0] else 1


class Snapshot(BaseModel):
    stores: list[Store] = Field(default_factory=list)


class World:
    def __init__(self, settings: Settings, issuer: PassIssuer, rng: random.Random | None = None):
        self.settings, self.issuer, self.rng = settings, issuer, rng or random.Random()
        self.stores: dict[str, Store] = {}
        self.readings: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=720))
        self._last_tick: float | None = None
        self._last_sample = 0.0

    # ---- persistence -------------------------------------------------------------------

    def save(self, path: Path) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(Snapshot(stores=list(self.stores.values())).model_dump_json())
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path, settings: Settings, issuer: PassIssuer) -> World:
        world = cls(settings, issuer)
        if path.exists():
            for store in Snapshot.model_validate_json(path.read_text()).stores:
                by_id = {install.id: install for install in store.installs}
                for freezer in store.freezers:  # one object per install again
                    if freezer.install and freezer.install.id in by_id:
                        freezer.install = by_id[freezer.install.id]
                world.stores[store.id] = store
        return world

    # ---- sign-up -----------------------------------------------------------------------

    def signup(self, team: str, now: float) -> Store:
        team = " ".join(team.split())
        if not 2 <= len(team) <= 40:
            raise ApiError(422, "invalid_team", "The team name must be 2 to 40 characters.")
        if any(store.team.casefold() == team.casefold() for store in self.stores.values()):
            raise ApiError(409, "team_taken", "That team name is already in use.")
        free = [city for city in CITIES if f"ica-{city[0]}" not in self.stores]
        if not free:
            raise ApiError(409, "no_city_left", "Every store is taken. Ask the organisers.")
        slug, city, lat, lon = self.rng.choice(free)
        freezers = [Freezer(id=f"frys-{n:02d}", name=f"Frys {n}", last_seen=now, online=f"frys-{n:02d}" != OFFLINE,
                            software_version=self.settings.current_version,
                            temperature_c=NORMAL_SETPOINT + self.rng.uniform(-0.5, 0.5))
                    for n in range(1, self.settings.freezers_per_store + 1)]
        store = Store(id=f"ica-{slug}", name=f"Ica {city}", city=city, lat=lat, lon=lon, team=team,
                      token=secrets.token_urlsafe(32), created_at=now, freezers=freezers)
        self.stores[store.id] = store
        return store

    def by_token(self, token: str) -> Store | None:
        for store in self.stores.values():
            if secrets.compare_digest(store.token, token):
                return store
        return None

    # ---- software ----------------------------------------------------------------------

    def software(self) -> list[dict]:
        return [{"version": self.settings.current_version, "released": "2026-03-02", "notes": "Current software."},
                {"version": self.settings.target_version, "released": "2026-11-10",
                 "notes": "Adds remote maintenance by service technicians."}]

    def install(self, store: Store, freezer_id: str, version: str, now: float) -> Install:
        freezer = store.freezer(freezer_id)
        if version not in [item["version"] for item in self.software()]:
            raise ApiError(404, "unknown_version", f"Software {version} cannot be installed.")
        if store.first_install_at is None:
            store.first_install_at = now
            for other in store.freezers:  # scripted: one freezer happens to start a defrost
                if other.id == DEFROSTS:
                    other.defrost_until = now + 90
        if not freezer.online:
            raise ApiError(409, "offline", f"{freezer.id} cannot be reached.")
        if freezer.install and freezer.install.state in ACTIVE:
            raise ApiError(409, "install_in_progress", f"{freezer.id} is already installing.")
        if freezer.software_version == version:
            raise ApiError(409, "already_installed", f"{freezer.id} already runs {version}.")
        if freezer.door_open or freezer.defrost_until is not None:
            raise ApiError(409, "busy", f"{freezer.id} is busy (door open or defrosting).")
        install = Install(id=store.next_id("inst"), freezer_id=freezer.id, version=version, started_at=now,
                          will_fail=freezer.id == FAILS_FIRST and version not in freezer.tried_versions)
        freezer.tried_versions.append(version)
        freezer.install = install
        store.installs.append(install)
        return install

    def _advance_install(self, freezer: Freezer, now: float) -> None:
        install = freezer.install
        if not install or install.state not in ACTIVE:
            return
        download, total = self.settings.download_seconds, self.settings.download_seconds + self.settings.install_seconds
        elapsed = now - install.started_at
        install.progress_pct = min(100, int(elapsed / download * 100))
        if install.will_fail and install.progress_pct >= 60:
            install.state, install.error = "failed", "checksum_mismatch"
            install.finished_at, install.progress_pct = now, 60
        elif elapsed >= total:
            install.state, install.finished_at = "installed", now
            freezer.software_version = install.version
        elif elapsed >= download:
            install.state = "installing"

    # ---- time --------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        elapsed = 1.0 if self._last_tick is None else min(max(now - self._last_tick, 0.0), 5.0)
        self._last_tick = now
        sample = now - self._last_sample >= self.settings.reading_interval_seconds
        if sample:
            self._last_sample = now
        for store in list(self.stores.values()):
            try:
                self._tick_store(store, now, elapsed, sample)
            except Exception:  # one broken store must not stop the others
                log.exception("tick failed for %s", store.id)

    def _tick_store(self, store: Store, now: float, elapsed: float, sample: bool) -> None:
        settings = self.settings
        for freezer in store.freezers:
            if freezer.id == OFFLINE:
                freezer.online = (store.first_install_at is not None
                                  and now >= store.first_install_at + settings.offline_return_seconds)
            if freezer.id == DOOR:
                freezer.door_open = (now - store.created_at) % 120 < 45
            if freezer.defrost_until is not None and now >= freezer.defrost_until:
                freezer.defrost_until = None
            self._advance_install(freezer, now)
            if not freezer.online:
                continue
            target = freezer.setpoint_c + (8 if freezer.defrost_until else 0) + (5 if freezer.door_open else 0)
            freezer.temperature_c += ((target - freezer.temperature_c) * min(1.0, 0.03 * elapsed)
                                      + self.rng.uniform(-0.05, 0.05))
            freezer.last_seen = now
            if sample:
                self.readings[store.id, freezer.id].append(
                    {"ts": iso(now), "at": now, "temperature_c": round(freezer.temperature_c, 1),
                     "setpoint_c": freezer.setpoint_c, "door_open": freezer.door_open,
                     "defrosting": freezer.defrost_until is not None})
        if store.stage1_done_at is None and all(
                freezer.software_version == settings.target_version for freezer in store.freezers):
            store.stage1_done_at = now
            store.next_legit_at = now + 10
        if store.stage1_done_at is not None:
            self._maintenance(store, now)

    # ---- maintenance requests and the attacker -------------------------------------------

    def _maintenance(self, store: Store, now: float) -> None:
        settings = self.settings
        for request in store.requests:
            if request.status == "pending" and now >= request.expires_at:
                request.status = "expired"
        capable = [f for f in store.freezers if f.online and f.software_version == settings.target_version]
        if not capable:
            return
        if now >= store.next_legit_at:
            store.next_legit_at = now + settings.legit_interval_seconds
            if self.rng.random() < 0.6:
                action, params = "set_setpoint", {"setpoint_c": round(self.rng.uniform(-24, -16) * 2) / 2}
            else:
                action, params = "defrost", {"duration_minutes": self.rng.randint(15, 45)}
            self._request(store, self.rng.choice(capable), action, params, "valid", now)
        for index, wave in enumerate(store.waves):
            if wave.released_at is None:
                due = store.stage1_done_at + settings.attack_delay_seconds + index * settings.wave_interval_seconds
                if not (settings.attack_auto and now >= due):
                    continue
                wave.released_at = wave.next_at = now
            if now >= wave.next_at:
                kind, variants = WAVES[index]
                variant = wave.attempts % 2
                wave.attempts += 1
                wave.next_at = now + settings.attack_retry_seconds * (3 if wave.state == "repelled" else 1)
                action, params = variants[variant]
                self._request(store, self.rng.choice(capable), action, dict(params), kind, now, index, variant)
        del store.requests[:-300]

    def _request(self, store: Store, freezer: Freezer, action: str, params: dict, kind: str, now: float,
                 wave: int | None = None, variant: int | None = None) -> Request:
        other = next(f"ica-{city[0]}" for city in CITIES if f"ica-{city[0]}" != store.id)
        technician_pass = self.issuer.mint(store.id, now, kind, self.rng.choice(TECHNICIANS), other)
        request = Request(id=store.next_id("mr"), freezer_id=freezer.id, action=action, params=params,
                          technician_pass=technician_pass, created_at=now,
                          expires_at=now + self.settings.request_ttl_seconds, wave=wave, variant=variant)
        store.requests.append(request)
        return request

    def decide(self, store: Store, request_id: str, decision: str, reason: str | None, now: float) -> Request:
        request = next((r for r in store.requests if r.id == request_id), None)
        if request is None:
            raise ApiError(404, "unknown_request", f"No maintenance request {request_id}.")
        if request.status != "pending":
            raise ApiError(409, "already_decided", f"{request_id} is {request.status}.")
        request.status = "approved" if decision == "approve" else "rejected"
        request.decided_at, request.reason = now, reason
        freezer = store.freezer(request.freezer_id)
        if request.status == "approved":
            if request.action == "set_setpoint":
                freezer.setpoint_c = float(request.params["setpoint_c"])
            else:
                freezer.defrost_until = now + float(request.params["duration_minutes"]) * 60
        if request.wave is None:
            store.last_legit = request.status
            store.legit_approved += request.status == "approved"
            store.legit_rejected += request.status == "rejected"
        else:
            wave = store.waves[request.wave]
            wave.results[str(request.variant)] = request.status
            if request.status == "approved":
                freezer.breached_by = request.wave
            elif wave.state == "repelled":  # the service desk puts the freezers right again
                for hit in store.freezers:
                    if hit.breached_by == request.wave:
                        hit.setpoint_c, hit.defrost_until, hit.breached_by = NORMAL_SETPOINT, None, None
        if (store.stage2_done_at is None and store.last_legit == "approved"
                and all(wave.state == "repelled" for wave in store.waves)):
            store.stage2_done_at = now
        return request

    def pass_key(self, store: Store) -> dict:
        return {"algorithm": ALGORITHM, "issuer": ISSUER, "audience": store.id,
                "public_key_pem": self.issuer.public_key_pem}

    # ---- energy (the data behind the stage 3 screen) ---------------------------------------

    def energy(self, store: Store, hours: int, now: float) -> list[dict]:
        if store.stage2_done_at is not None and store.stage3_seen_at is None:
            store.stage3_seen_at = now
        top = int(now // 3600) * 3600
        rows = []
        for back in range(hours, 0, -1):
            start = top - back * 3600
            open_hours = 6 <= datetime.fromtimestamp(start, UTC).hour <= 20
            for freezer in store.freezers:
                rng = random.Random(f"{store.id}/{freezer.id}/{start}")
                openings = int(rng.uniform(4, 30) * (1.0 if open_hours else 0.1) * (3 if freezer.id == DOOR else 1))
                rows.append({"freezer_id": freezer.id, "hour": iso(start),
                             "energy_kwh": round(0.9 + openings * 0.02 + rng.uniform(0, 0.3), 2),
                             "door_openings": openings,
                             "avg_temperature_c": round(NORMAL_SETPOINT + rng.uniform(-0.6, 0.6) + openings * 0.02, 1)})
        return rows

    # ---- organiser ---------------------------------------------------------------------

    def briefs(self, store: Store) -> list[dict]:
        """The briefs this store has reached, newest first. Stage 2's appears when the attack starts."""
        reached = [1]
        if any(wave.released_at is not None for wave in store.waves):
            reached.append(2)
        if store.stage2_done_at is not None:
            reached.append(3)
        return [{"stage": stage, "title": BRIEFS[stage].splitlines()[0].lstrip("# "), "markdown": BRIEFS[stage]}
                for stage in reversed(reached)]

    def overview(self, store: Store, with_token: bool = False) -> dict:
        target = self.settings.target_version
        updated = sum(freezer.software_version == target for freezer in store.freezers)
        states = [wave.state for wave in store.waves]
        detail = {1: f"{updated}/{len(store.freezers)} freezers updated",
                  2: f"{states.count('repelled')}/{len(states)} waves repelled",
                  3: "Energy data in use" if store.stage3_seen_at else "Energy data not used yet",
                  4: "All three stages complete"}[store.stage]
        data = {"id": store.id, "name": store.name, "city": store.city, "lat": store.lat, "lon": store.lon,
                "team": store.team, "created_at": iso(store.created_at), "last_call_at": iso(store.last_call_at),
                "stage": store.stage, "stage_label": STAGES[store.stage], "detail": detail,
                "freezers_updated": updated, "freezers_total": len(store.freezers),
                # The names give the weaknesses away: organisers only.
                "waves": [{"name": name if with_token else f"Wave {number}", "state": state}
                          for number, (name, state) in enumerate(zip(WAVE_NAMES, states, strict=True), 1)],
                "legit_rejected": store.legit_rejected,
                "warm_freezers": sum(freezer.temperature_c > 0 for freezer in store.freezers),
                "hottest_c": round(max(freezer.temperature_c for freezer in store.freezers), 1)}
        if with_token:
            data["token"] = store.token
        return data

    def release_next_wave(self, store: Store, now: float) -> int:
        if store.stage1_done_at is None:
            raise ApiError(409, "stage1_not_done", "The freezers do not all run the new software yet.")
        for index, wave in enumerate(store.waves):
            if wave.released_at is None:
                wave.released_at = wave.next_at = now
                return index + 1
        raise ApiError(409, "all_released", "All waves are already released.")

    def force_stage1(self, store: Store) -> None:
        """For a team that is stuck: put every freezer on the new software."""
        for freezer in store.freezers:
            freezer.software_version = self.settings.target_version
            if freezer.install and freezer.install.state in ACTIVE:
                freezer.install.state, freezer.install.error = "failed", "cancelled_by_service_desk"
        store.first_install_at = store.first_install_at or 0.0
