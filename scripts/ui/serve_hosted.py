"""The hosted gateway on this computer (D228): http://127.0.0.1:8090, its accounts and users' folders in a temp
folder (or the one given). Sign up there and a real Clipper studio starts behind it for that account.

    python scripts/ui/serve_hosted.py [root]

admin@example.com is an admin (/admin shows reset links, since no mail is sent here).
"""
import os
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="clipper-hosted-"))
    os.environ["CLIPPER_HOSTED_ROOT"] = str(root)
    os.environ.setdefault("CLIPPER_PUBLIC_URL", "http://127.0.0.1:8090")
    os.environ.setdefault("CLIPPER_ADMINS", "admin@example.com")
    os.environ.pop("SMTP_URL", None)
    import uvicorn

    from clipper.hosted.app import create_app

    print(f"Gateway at http://127.0.0.1:8090/  (data in {root})", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=8090, access_log=False)
