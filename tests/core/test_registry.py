from rlgate.core.registry import discover


def test_discovery_finds_milestone_probes():
    probes = discover()
    for pid in [
        "forgery.world_readable_tests",
        "leakage.git_unreachable_objects",
        "grader_strength.revert_probe",
    ]:
        assert pid in probes, f"{pid} not discovered"


def test_every_probe_has_stable_metadata():
    for pid, cls in discover().items():
        assert cls.meta.id == pid
        assert cls.meta.family
        assert cls.meta.doc, f"{pid} missing a one-line doc"
