"""Run the gateway while the forum server is running: python -m y_server.gateway --server URL --token-file F [--port P]."""

import argparse
import sys
from pathlib import Path

from .core import ForumHumanGateway
from .http_app import create_gateway_app, server_call


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", required=True, help="base URL of the forum server, e.g. http://127.0.0.1:5010")
    ap.add_argument("--token-file", required=True, help="file with the token every request must carry")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5110)
    args = ap.parse_args(argv)
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    app = create_gateway_app(ForumHumanGateway(server_call(args.server)), token)
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
