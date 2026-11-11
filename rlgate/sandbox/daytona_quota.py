"""Daytona account limits, read from the account itself (any user's, any plan).

A bank of many tasks needs one snapshot per distinct environment, and each task runs several
sandboxes. Every Daytona organization has its own caps (snapshots; CPU, memory and disk per
region), and they differ by plan, so nothing here assumes a number. Instead:

  - **Snapshots.** Before building one, rlgate reserves a slot. Free slots = the account's
    snapshot quota (from the usage API, or ``[environment] max_snapshots``) minus the
    snapshots that exist (listed, not counted from the usage figure, which can lag) minus
    builds in flight in this process. When none is free, rlgate makes room by deleting the
    least recently used snapshot it created itself (``rlgate-*``) that nothing is using:
    never a snapshot of yours, never one a task of this run still needs, and never one used
    in the last ``snapshot_idle_minutes`` (another rlgate run on the same account may be
    using it). When nothing can be freed it waits for tasks of this run to finish; when no
    task of this run holds one, it builds the image per sandbox instead (no snapshot).
  - **Sandboxes.** Before creating one, rlgate checks the region's free CPU, memory and
    disk, counting its own sandboxes still starting, and waits while they don't fit.
  - **Unknown limits.** An API key without permission to read usage gives no quota: then
    nothing is gated up front, and a refused snapshot build is answered the same way as a
    full account (free one of ours, retry once, else build per sandbox). Error texts are
    never parsed.

One ``Budget`` per account (API URL + key) per process, shared by every task's sandbox.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rlgate import ui

log = logging.getLogger(__name__)

OURS = "rlgate-"                 # the only snapshots rlgate ever deletes
GONE = {"removed", "deleted"}   # everything else listed still takes a slot (incl. removing)
REFRESH_S = 10.0                 # usage is re-read at most this often
POLL_S = 15.0


@dataclass
class Region:
    id: str
    cpu: tuple[float, float]       # (used, quota)
    memory: tuple[float, float]    # GiB
    disk: tuple[float, float]      # GiB

    def fits(self, cpu: float, memory: float, disk: float) -> bool:
        return all(q <= 0 or used + need <= q for (used, q), need in
                   ((self.cpu, cpu), (self.memory, memory), (self.disk, disk)))


@dataclass
class Usage:
    snapshot_quota: int | None             # None: unknown (no permission, no setting)
    snapshots: list[Any] = field(default_factory=list)   # every one taking a slot
    regions: list[Region] = field(default_factory=list)
    counted: int = 0                       # the account's own figure (0 when unreadable)

    @property
    def used(self) -> int:
        return max(len(self.snapshots), self.counted)

    @property
    def removing(self) -> int:
        return sum(_state(s) == "removing" for s in self.snapshots)


class Budget:
    """Snapshot slots and sandbox room for one Daytona account, shared by every task."""

    def __init__(self, client: Any, *, max_snapshots: int | None = None,
                 idle_minutes: float = 10.0, region: str | None = None) -> None:
        self.client = client
        self.max_snapshots = max_snapshots
        self.idle_s = idle_minutes * 60
        self.region = region
        self._cond = threading.Condition()
        self._building: set[str] = set()        # snapshot builds in flight (this process)
        self._using: dict[str, int] = {}        # snapshot → tasks of this run using it
        self._ours_done: set[str] = set()       # used by this run and released
        self._starting: list[tuple[float, float, float]] = []   # sandboxes being created
        self._usage: Usage | None = None
        self._read_at = 0.0
        self._org: str | None = None
        self._usage_api_ok = True

    # -- reading the account -------------------------------------------------------------

    def usage(self, *, fresh: bool = False) -> Usage:
        if not fresh and self._usage is not None and \
                time.monotonic() - self._read_at < REFRESH_S:
            return self._usage
        snaps = self._list_snapshots()
        quota, regions, counted = self.max_snapshots, [], 0
        overview = self._overview(snaps)
        if overview is not None:
            counted = int(getattr(overview, "current_snapshot_usage", 0) or 0)
            if quota is None and getattr(overview, "total_snapshot_quota", None):
                quota = int(overview.total_snapshot_quota)
            for r in getattr(overview, "region_usage", None) or []:
                regions.append(Region(
                    str(r.region_id),
                    (float(r.current_cpu_usage or 0), float(r.total_cpu_quota or 0)),
                    (float(r.current_memory_usage or 0), float(r.total_memory_quota or 0)),
                    (float(r.current_disk_usage or 0), float(r.total_disk_quota or 0))))
        self._usage = Usage(quota, snaps, regions, counted)
        self._read_at = time.monotonic()
        return self._usage

    def _list_snapshots(self) -> list[Any]:
        out, page = [], 1
        try:
            while True:
                res = self.client.snapshot.list(page=page, limit=100)
                items = list(getattr(res, "items", None) or [])
                out += [s for s in items if _state(s) not in GONE
                        and not getattr(s, "general", False)]   # Daytona's shared ones
                total_pages = getattr(res, "total_pages", None) or 1
                if page >= total_pages or not items:
                    break
                page += 1
        except Exception as e:                # noqa: BLE001 - unknown, not fatal
            log.debug("could not list Daytona snapshots: %s", e)
        return out

    def _overview(self, snaps: list[Any]):
        if not self._usage_api_ok:
            return None
        try:
            import daytona_api_client as api
            org = self._org or self._find_org(api, snaps)
            if not org:
                return None
            self._org = org
            return api.OrganizationsApi(self.client._api_client) \
                .get_organization_usage_overview(org)
        except Exception as e:                # noqa: BLE001 - this key can't read usage
            log.info("Daytona usage is not readable with this key (%s); limits are "
                     "learned from refusals instead", type(e).__name__)
            self._usage_api_ok = False
            return None

    def _find_org(self, api, snaps: list[Any]) -> str | None:
        """The organization this key belongs to: the SDK's setting, the key itself, or one of
        the org's own snapshots (never a shared one: those belong to Daytona's orgs)."""
        org = getattr(self.client, "_organization_id", None)
        if org:
            return str(org)
        try:
            key = api.ApiKeysApi(self.client._api_client).get_current_api_key()
            if getattr(key, "organization_id", None):
                return str(key.organization_id)
        except Exception as e:                # noqa: BLE001 - try the next source
            log.debug("could not read the current API key: %s", e)
        return next((str(s.organization_id) for s in snaps
                     if getattr(s, "organization_id", None)), None)

    def describe(self) -> str:
        """One line for ``rlgate doctor``: the limits rlgate will work within."""
        u = self.usage(fresh=True)
        snap = (f"{u.used} of {u.snapshot_quota} snapshots in use"
                if u.snapshot_quota else f"{u.used} snapshots (limit unknown)")
        r = self._region(u)
        if r is None:
            return snap + "; sandbox room unknown"
        return (f"{snap}; region {r.id}: CPU {r.cpu[0]:g} of {r.cpu[1]:g}, memory "
                f"{r.memory[0]:g} of {r.memory[1]:g} GiB, disk {r.disk[0]:g} of "
                f"{r.disk[1]:g} GiB")

    # -- snapshots -----------------------------------------------------------------------

    def use(self, name: str) -> None:
        """A task of this run uses snapshot ``name`` (never evicted while it does)."""
        with self._cond:
            self._using[name] = self._using.get(name, 0) + 1

    def done(self, name: str) -> None:
        with self._cond:
            n = self._using.get(name, 0) - 1
            if n > 0:
                self._using[name] = n
            else:
                self._using.pop(name, None)
                self._ours_done.add(name)     # this run is done with it: evictable at once
            self._cond.notify_all()

    def reserve(self, name: str, wait_s: float) -> bool:
        """A slot to build snapshot ``name``. False: none could be had (no quota known and
        nothing to free, or nothing of this run will free one) → build per sandbox."""
        deadline = time.monotonic() + wait_s
        while True:
            with self._cond:
                u = self.usage(fresh=True)
                if u.snapshot_quota is None:
                    self._building.add(name)
                    return True
                used = u.used + len(self._building - {_n(s) for s in u.snapshots})
                if used < u.snapshot_quota:
                    self._building.add(name)
                    return True
                # a slot being freed (deletion in progress) comes back by itself: wait for
                # it rather than delete more
                if not u.removing and self._evict(u):
                    continue
                held = bool(self._using) or bool(self._building) or bool(u.removing)
                if not held or time.monotonic() >= deadline:
                    return False
                why = ("a deleted snapshot is being removed" if u.removing
                       else "tasks of this run will free one")
                ui.progress(f"waiting for a Daytona snapshot slot ({used} of "
                            f"{u.snapshot_quota} in use; {why})")
                self._cond.wait(POLL_S)

    def built(self, name: str) -> None:
        with self._cond:
            self._building.discard(name)
            self._cond.notify_all()

    def make_room(self) -> bool:
        """A build was refused (limit unknown or stale): free one of ours if possible."""
        deadline = time.monotonic() + 300
        with self._cond:
            while True:
                u = self.usage(fresh=True)
                if not u.removing:
                    return self._evict(u)
                if time.monotonic() >= deadline:
                    return False
                ui.progress("waiting for a deleted Daytona snapshot to be removed")
                self._cond.wait(POLL_S)

    def _evict(self, u: Usage) -> bool:
        """Delete the least recently used ``rlgate-*`` snapshot nobody needs."""
        now = datetime.now(UTC)
        idle = []
        for s in u.snapshots:
            name = _n(s)
            if not name.startswith(OURS) or name in self._using or name in self._building:
                continue
            if _state(s) in ("error", "build_failed"):
                idle.append((datetime.min.replace(tzinfo=UTC), name))   # broken: first
                continue
            if _state(s) not in ("active", "inactive"):
                continue
            last = _when(s, "last_used_at") or _when(s, "updated_at") or _when(s, "created_at")
            if last is not None and (now - last).total_seconds() < self.idle_s and \
                    name not in self._ours_done:
                continue            # maybe in use by another rlgate run on this account
            idle.append((last or now, name))
        if not idle:
            return False
        _, name = min(idle)
        try:
            self.client.snapshot.delete(next(s for s in u.snapshots if _n(s) == name))
        except Exception as e:                # noqa: BLE001
            log.warning("could not delete snapshot %s to make room: %s", name, e)
            return False
        log.info("deleted snapshot %s (least recently used) to stay within the limit", name)
        ui.progress(f"made room: deleted the unused snapshot {name}")
        self._usage = None
        return True

    # -- sandboxes -----------------------------------------------------------------------

    def room(self, cpu: float, memory: float, disk: float, wait_s: float) -> None:
        """Wait until the region has room for a sandbox of this size (when usage is
        readable), counting this run's sandboxes still starting. Call ``started`` after."""
        deadline = time.monotonic() + wait_s
        while True:
            with self._cond:
                r = self._region(self.usage())
                pending = [sum(x) for x in zip(*self._starting, strict=False)] or [0, 0, 0]
                if r is None or r.fits(cpu + pending[0], memory + pending[1],
                                       disk + pending[2]) or time.monotonic() >= deadline:
                    self._starting.append((cpu, memory, disk))
                    return
                ui.progress(f"waiting for room on Daytona (region {r.id}: CPU "
                            f"{r.cpu[0]:g} of {r.cpu[1]:g}, memory {r.memory[0]:g} of "
                            f"{r.memory[1]:g} GiB, disk {r.disk[0]:g} of {r.disk[1]:g} GiB)")
                self._cond.wait(POLL_S)
                self._usage = None

    def started(self, cpu: float, memory: float, disk: float) -> None:
        with self._cond:
            try:
                self._starting.remove((cpu, memory, disk))
            except ValueError:
                pass
            self._usage = None
            self._cond.notify_all()

    def _region(self, u: Usage) -> Region | None:
        regions = [r for r in u.regions if r.cpu[1] > 0]
        if not regions:
            return None
        if self.region:
            return next((r for r in regions if r.id == self.region), None)
        return max(regions, key=lambda r: r.cpu[1])


_BUDGETS: dict[str, Budget] = {}
_BUDGETS_LOCK = threading.Lock()


def budget_for(client: Any, **kw) -> Budget:
    """The shared budget of the account ``client`` talks to."""
    key = hashlib.sha256(f"{getattr(client, '_api_url', '')}\0"
                         f"{getattr(client, '_api_key', '') or ''}".encode()).hexdigest()
    with _BUDGETS_LOCK:
        if key not in _BUDGETS:
            _BUDGETS[key] = Budget(client, **kw)
        return _BUDGETS[key]


def _n(s: Any) -> str:
    return str(getattr(s, "name", "") or "")


def _state(s: Any) -> str:
    st = getattr(s, "state", None)
    return str(getattr(st, "value", st) or "").lower()


def _when(s: Any, attr: str) -> datetime | None:
    v = getattr(s, attr, None)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(v))   # 3.11+ reads a trailing Z
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None
