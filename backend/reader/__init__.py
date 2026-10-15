"""PAW Reader Fidelity Layer.

The package is deliberately evidence-first: readers never call a model and
never mutate mirror state.  All derived evidence is linked to the immutable
raw artifact hash and source version in :class:`ReaderResult`.
"""

from .contract import ReaderResult, ReaderInput, artifact_sha256, content_fingerprint
from .dispatch import read_artifact
from .gate import evaluate_gate

__all__ = [
    "ReaderInput", "ReaderResult", "artifact_sha256", "content_fingerprint",
    "read_artifact", "evaluate_gate",
]
