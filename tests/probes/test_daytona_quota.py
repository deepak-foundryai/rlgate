"""The Daytona snapshot/room budget, against a fake account (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from rlgate.sandbox import daytona_quota as dq

api = pytest.importorskip("daytona_api_client")


def _snap(name, state="active", minutes_ago=60, general=False):
    return NS(name=name, state=state, general=general, organization_id="org1",
              last_used_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))


class FakeSnapshots:
    def __init__(self, snaps):
        self.snaps = list(snaps)
        self.deleted: list[str] = []

    def list(self, page=1, limit=100):
        return NS(items=self.snaps[(page - 1) * limit: page * limit], total=len(self.snaps),
                  page=page, total_pages=max(1, -(-len(self.snaps) // limit)))

    def delete(self, snap):
        self.deleted.append(snap.name)
        self.snaps = [s for s in self.snaps if s.name != snap.name]


class FakeClient:
    def __init__(self, snaps):
        self.snapshot = FakeSnapshots(snaps)
        self._api_client = object()


@pytest.fixture
def overview(monkeypatch):
    """Install a fake ``daytona_api_client`` whose usage overview the test controls."""
    box: dict = {"quota": 3, "regions": [], "error": None}

    class ApiKeysApi:
        def __init__(self, _client):
            pass

        def get_current_api_key(self):
            return NS(organization_id="org1")

    class OrganizationsApi:
        def __init__(self, _client):
            pass

        def get_organization_usage_overview(self, org):
            assert org == "org1"
            if box["error"]:
                raise box["error"]
            return NS(total_snapshot_quota=box["quota"], current_snapshot_usage=0,
                      region_usage=box["regions"])

    monkeypatch.setattr(api, "OrganizationsApi", OrganizationsApi)
    monkeypatch.setattr(api, "ApiKeysApi", ApiKeysApi)
    monkeypatch.setattr(dq, "POLL_S", 0.01)
    return box


def test_free_slot_is_reserved_without_deleting(overview):
    c = FakeClient([_snap("rlgate-a")])
    b = dq.Budget(c)
    assert b.reserve("rlgate-new", wait_s=1)
    assert c.snapshot.deleted == []


def test_full_account_frees_least_recently_used_of_ours_only(overview):
    c = FakeClient([_snap("mine-not-rlgate", minutes_ago=999),
                    _snap("rlgate-old", minutes_ago=300),
                    _snap("rlgate-newer", minutes_ago=100)])
    b = dq.Budget(c, idle_minutes=10)
    assert b.reserve("rlgate-new", wait_s=1)
    assert c.snapshot.deleted == ["rlgate-old"]      # never the user's own snapshot


def test_recently_used_snapshots_are_left_alone(overview):
    """Maybe another rlgate run on the account is using them: build per sandbox instead."""
    c = FakeClient([_snap("rlgate-a", minutes_ago=1), _snap("rlgate-b", minutes_ago=2),
                    _snap("x", minutes_ago=999)])
    b = dq.Budget(c, idle_minutes=10)
    assert not b.reserve("rlgate-new", wait_s=0.05)
    assert c.snapshot.deleted == []


def test_snapshot_in_use_by_this_run_is_never_freed(overview):
    c = FakeClient([_snap("rlgate-a"), _snap("rlgate-b"), _snap("x")])
    b = dq.Budget(c, idle_minutes=0)
    b.use("rlgate-a")
    b.use("rlgate-b")
    assert not b.reserve("rlgate-new", wait_s=0.05)   # both held, waits, then gives up
    assert c.snapshot.deleted == []
    b.done("rlgate-a")                                  # released: evictable at once
    assert b.reserve("rlgate-new", wait_s=1)
    assert c.snapshot.deleted == ["rlgate-a"]


def test_released_by_this_run_is_freed_even_if_recent(overview):
    c = FakeClient([_snap("rlgate-a", minutes_ago=0), _snap("y"), _snap("x")])
    b = dq.Budget(c, idle_minutes=60)
    b.use("rlgate-a")
    b.done("rlgate-a")
    assert b.reserve("rlgate-new", wait_s=1)
    assert c.snapshot.deleted == ["rlgate-a"]


def test_removing_counts_and_is_waited_for_not_cascaded(overview):
    c = FakeClient([_snap("rlgate-gone", state="removing"), _snap("rlgate-b"), _snap("x")])
    b = dq.Budget(c, idle_minutes=0)
    assert not b.reserve("rlgate-new", wait_s=0.05)
    assert c.snapshot.deleted == []                     # did not delete a second one


def test_daytona_shared_snapshots_do_not_count(overview):
    c = FakeClient([_snap("daytona-default", general=True)] * 5 + [_snap("x")])
    assert dq.Budget(c).reserve("rlgate-new", wait_s=1)


def test_unreadable_usage_degrades_to_setting_or_ungated(overview):
    overview["error"] = PermissionError("401")
    c = FakeClient([_snap("rlgate-a"), _snap("x")])
    assert dq.Budget(c).reserve("rlgate-new", wait_s=1)          # limit unknown: not gated
    capped = dq.Budget(FakeClient([_snap("rlgate-a", minutes_ago=999), _snap("x")]),
                       max_snapshots=2)
    assert capped.reserve("rlgate-new", wait_s=1)                # frees one of ours
    assert capped.client.snapshot.deleted == ["rlgate-a"]


def test_make_room_after_a_refusal(overview):
    overview["error"] = PermissionError("401")
    c = FakeClient([_snap("rlgate-a", minutes_ago=999), _snap("x")])
    b = dq.Budget(c)
    assert b.make_room()
    assert c.snapshot.deleted == ["rlgate-a"]
    assert not b.make_room()                                    # nothing of ours left


def test_region_room(overview):
    overview["regions"] = [NS(region_id="small", current_cpu_usage=0, total_cpu_quota=4,
                              current_memory_usage=0, total_memory_quota=8,
                              current_disk_usage=0, total_disk_quota=30),
                           NS(region_id="big", current_cpu_usage=9, total_cpu_quota=10,
                              current_memory_usage=0, total_memory_quota=100,
                              current_disk_usage=0, total_disk_quota=300)]
    b = dq.Budget(FakeClient([_snap("x")]))
    b.room(1, 1, 3, wait_s=1)                    # big region: 9+1 <= 10 fits
    b.room(1, 1, 3, wait_s=0.05)                 # 9+1+1 > 10: waits, then goes ahead
    assert len(b._starting) == 2
    b.started(1, 1, 3)
    b.started(1, 1, 3)
    assert b._starting == []
    assert "region big" in b.describe()
    assert dq.Budget(FakeClient([]), region="small")._region(b.usage()).id == "small"


def test_one_budget_per_account():
    a = NS(_api_url="u", _api_key="k1")
    assert dq.budget_for(a) is dq.budget_for(NS(_api_url="u", _api_key="k1"))
    assert dq.budget_for(a) is not dq.budget_for(NS(_api_url="u", _api_key="k2"))


# -- wiring in DaytonaSandbox._ensure_snapshot -------------------------------------------

daytona = pytest.importorskip("daytona")


class BuildingSnapshots(FakeSnapshots):
    """create() is refused (as a full account would) until ``refusals`` runs out."""

    def __init__(self, snaps, refusals=0):
        super().__init__(snaps)
        self.refusals = refusals
        self.created: list[str] = []

    def get(self, name):
        from daytona.common import errors as de
        for s in self.snaps:
            if s.name == name:
                return s
        raise de.DaytonaNotFoundError("not found")

    def create(self, params, on_logs=None, timeout=None):
        from daytona.common import errors as de
        if self.refusals:
            self.refusals -= 1
            raise de.DaytonaError("whatever wording this server uses")
        self.created.append(params.name)
        self.snaps.append(_snap(params.name, minutes_ago=0))


def _sandbox(tmp_path, client, **opts):
    from rlgate.sandbox.daytona import DaytonaOptions, DaytonaSandbox
    (tmp_path / "env").mkdir()
    df = tmp_path / "env" / "Dockerfile"
    df.write_text("FROM debian:12.9\n")
    sb = DaytonaSandbox(None, dockerfile=df, options=DaytonaOptions(**opts),
                        client_factory=lambda: client)
    sb._client = client
    sb._budget = dq.Budget(client, max_snapshots=opts.get("max_snapshots"))
    return sb


def test_refused_build_frees_one_of_ours_and_retries(overview, tmp_path):
    overview["error"] = PermissionError("401")          # limit unknown to rlgate
    c = FakeClient([])
    c.snapshot = BuildingSnapshots([_snap("rlgate-stale", minutes_ago=999)], refusals=1)
    sb = _sandbox(tmp_path, c)
    name = sb._ensure_snapshot(image="debian:12.9")
    assert name and name.startswith("rlgate-") and c.snapshot.created == [name]
    assert c.snapshot.deleted == ["rlgate-stale"]
    assert sb._budget._using == {name: 1}
    sb.close()
    assert sb._budget._using == {} and name in sb._budget._ours_done


def test_no_slot_builds_per_sandbox_instead(overview, tmp_path):
    overview["error"] = PermissionError("401")
    c = FakeClient([])
    c.snapshot = BuildingSnapshots([_snap("theirs", minutes_ago=999)], refusals=5)
    sb = _sandbox(tmp_path, c)
    assert sb._ensure_snapshot(image="debian:12.9") is None    # degrade, don't fail the task
    assert c.snapshot.deleted == []


def test_backend_failures_are_not_checked_not_broken(tmp_path):
    from daytona.common import errors as de

    from rlgate.cli.engine import is_infrastructure
    from rlgate.core import questions as Q
    from rlgate.core.report import TaskView, verdict_slug
    from rlgate.sandbox.errors import BackendUnavailable, BuildFailed

    try:
        try:
            raise de.DaytonaError("snapshot limit, in whatever words")
        except de.DaytonaError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as e:
        wrapped = e
    assert is_infrastructure(wrapped)
    assert is_infrastructure(BackendUnavailable("no key"))
    assert not is_infrastructure(BuildFailed([("image", "log")]))   # the task's own defect
    assert not is_infrastructure(ValueError("bad task.toml"))

    v = TaskView("t", "t", tmp_path, None, None, error="x", infra=True)
    assert v.verdict == Q.NOT_CHECKED and verdict_slug(v.verdict) == "not_checked"
    assert v.exit_code == 2                                          # still not a pass
    assert TaskView("t", "t", tmp_path, None, None, error="x").verdict == Q.BROKEN
