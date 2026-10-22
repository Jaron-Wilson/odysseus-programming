"""python -m programming: serve the mod on MOD_HOST:MOD_PORT (the core picks
them; standalone 127.0.0.1:8770)."""
import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=os.environ.get("PROGRAMMING_LOG", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from programming import auth
    if not auth.under_core():
        auth.password()          # prints a made-up one when MOD_PASSWORD is not set
    uvicorn.run("programming.app:app", host=os.environ.get("MOD_HOST", "127.0.0.1"),
                port=int(os.environ.get("MOD_PORT", "8770")), log_level="warning",
                proxy_headers=False, server_header=False, ws_ping_interval=None)


if __name__ == "__main__":
    main()
