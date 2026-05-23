"""
DEPRECATED — Worker adapter moved to blenderworker package.

All worker functionality has been relocated to the ``blenderworker`` package:

- **Entry point**: ``blenderworker/src/worker_main.py``
- **Blender lifecycle**: ``blenderworker/src/worker_main.py`` (launch_blender, wait_for_socket, stop_blender)
- **TCP client**: ``blenderworker/src/transport/blender_client.py`` (``BlenderTCPClient``)
- **Config**: ``blenderworker/src/worker_main.py`` (``cfg()``)
- **Task loop**: ``blenderworker/src/worker_main.py`` (``run_once()``, ``main()``)
- **Pipeline orchestration**: ``blenderworker/src/worker_main.py`` (``run_pipeline()``)
- **Callback helpers**: ``blenderworker/src/worker_main.py`` (``_callback()``, ``_result_url()``)
- **Blender boot script**: ``blenderworker/blender_launcher.py`` (runs inside Blender)
- **Pool integration** (server-side): ``blenderserver/worker/pool.py``
- **Callback routes** (server-side): ``blenderserver/worker/callback.py``

Usage (new)::

    cd blenderworker
    python -m src.worker_main

This stub exists only for backward compatibility and will be removed in a future release.
"""

from __future__ import annotations

import logging
import warnings

logger = logging.getLogger("blenderserver.adapter")

warnings.warn(
    "blenderserver.worker.adapter is deprecated. Use blenderworker.src.worker_main instead.",
    DeprecationWarning,
    stacklevel=2,
)

logger.warning(
    "blenderserver/worker/adapter.py is deprecated — use blenderworker/src/worker_main.py"
)
