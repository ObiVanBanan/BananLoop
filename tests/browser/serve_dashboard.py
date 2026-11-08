from __future__ import annotations

import tempfile

from bananloop.api import create_server


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bananloop-dashboard-browser-") as repo:
        server = create_server(repo, host="127.0.0.1", port=8765, token="browser-test-token")
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
