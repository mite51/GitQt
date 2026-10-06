"""Standalone askpass client. Secrets travel only over an authenticated local socket."""

import json
import os
import socket
import sys


def main():
    try:
        with socket.create_connection(
            ("127.0.0.1", int(os.environ["GITQT_ASKPASS_PORT"])), timeout=610
        ) as connection:
            connection.sendall(
                (
                    json.dumps(
                        {
                            "token": os.environ["GITQT_ASKPASS_TOKEN"],
                            "prompt": sys.argv[1] if len(sys.argv) > 1 else "Credential",
                        }
                    )
                    + "\n"
                ).encode()
            )
            response = json.loads(connection.makefile("rb").readline(65536))
            if response is None:
                return 1
            print(response)
            return 0
    except (OSError, ValueError, KeyError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
