"""Runtime tuning for CPU-bound FunASR inference."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_configured_intra_op: int | None = None
_DEFAULT_INTRA_OP_THREADS = 2
_DEFAULT_INTER_OP_THREADS = 1


def _positive_int_from_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r 不是整数，使用默认值 %d", name, raw, default)
        return default
    return max(1, value)


def configure_inference_threads() -> int:
    """Constrain CPU inference so it does not starve capture/UI work.

    FunASR's streaming model is small.  On a typical 8-core machine it is
    actually faster with two intra-op threads than with four because thread
    coordination overhead dominates.  Keeping the default low also leaves CPU
    headroom for the browser, WebSocket I/O and the rest of the operating
    system.  Deployments with larger CPUs or offline-only workloads can raise
    the value with FUNASR_TORCH_NUM_THREADS.
    """
    global _configured_intra_op
    if _configured_intra_op is not None:
        return _configured_intra_op

    intra_op = _positive_int_from_env(
        "FUNASR_TORCH_NUM_THREADS", _DEFAULT_INTRA_OP_THREADS
    )
    inter_op = _positive_int_from_env(
        "FUNASR_TORCH_INTEROP_THREADS", _DEFAULT_INTER_OP_THREADS
    )

    # These variables are read while OpenMP/BLAS initializes.  setdefault keeps
    # explicit deployment overrides intact.
    for name in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(name, str(intra_op))

    import torch

    torch.set_num_threads(intra_op)
    try:
        torch.set_num_interop_threads(inter_op)
    except RuntimeError:
        # PyTorch only permits this before the first parallel operation.
        logger.debug("PyTorch inter-op threads were already initialized", exc_info=True)

    _configured_intra_op = intra_op
    logger.info(
        "FunASR inference threads constrained to intra-op=%d inter-op=%d",
        intra_op,
        inter_op,
    )
    return intra_op
