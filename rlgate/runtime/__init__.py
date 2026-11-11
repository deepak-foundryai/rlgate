"""Runtime layer: how a task is stood up and graded (the counterpart to ``rlgate.ingest``).

``RuntimeSpec`` / ``detect_runtime`` describe the environment; ``precheck`` resolves the
adapter that would run it and whether this host can. Execution adapters (Phase 2+) register
under the ``rlgate.runtimes`` entry-point group, mirroring probes and ingest adapters.
"""

from rlgate.runtime.precheck import HostCapabilities, RuntimePlan, precheck, probe_host
from rlgate.runtime.spec import RuntimeKind, RuntimeSpec, detect_runtime

__all__ = [
    "HostCapabilities",
    "RuntimeKind",
    "RuntimePlan",
    "RuntimeSpec",
    "detect_runtime",
    "precheck",
    "probe_host",
]
