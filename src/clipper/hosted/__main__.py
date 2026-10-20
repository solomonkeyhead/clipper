"""`python -m clipper.hosted`: the gateway on 0.0.0.0:$PORT (8080 by default), behind an HTTPS proxy (D228).

No access log: a password reset link carries its token in the address, and an access log would keep it.
"""

import os

import uvicorn

from ..utils.logging import setup_logging
from . import root
from .app import create_app

if __name__ == "__main__":
    root().mkdir(parents=True, exist_ok=True)
    setup_logging(False, log_file=root() / "gateway.log")
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT") or 8080), access_log=False,
                proxy_headers=True)
