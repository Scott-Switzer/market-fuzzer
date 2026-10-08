"""Leakage-safe synthetic training corpus export (M10.7).

The benchmark side generates evaluation worlds; this package turns the *same*
world machinery into versioned, immutable training datasets. The security model
is structural, not procedural: a corpus is built from an explicitly
``TRAINABLE`` plan, the build path refuses any plan containing evaluation worlds
before generating anything, and every persisted artifact is hash-committed and
independently re-validated on read.

The session seam (:mod:`app.corpus.recorder`) imports eagerly because the
benchmark session references it; the heavy ``builder`` / ``validate`` modules
(pyarrow, file IO) are imported lazily so a plain benchmark run pays nothing.
"""

from app.corpus.recorder import NullSessionRecorder, RecorderHooks, SessionRecorder
from app.corpus.schema import CORPUS_SCHEMA_VERSION, TABLE_SCHEMAS, CorpusTable

__all__ = [
    "CORPUS_SCHEMA_VERSION",
    "CorpusTable",
    "NullSessionRecorder",
    "RecorderHooks",
    "SessionRecorder",
    "TABLE_SCHEMAS",
]
