"""Persist Certbot's PEM and update HAProxy through its private runtime socket."""

import os
import socket
from pathlib import Path


def command(request: bytes) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect("/runtime/haproxy.sock")
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        with client.makefile("r") as response:
            return response.read()


def main() -> None:
    lineage = Path(os.environ.get("RENEWED_LINEAGE", "/etc/letsencrypt/live/site"))
    if not lineage.exists():
        return

    pem = (lineage / "fullchain.pem").read_bytes() + (
        lineage / "privkey.pem"
    ).read_bytes()
    os.umask(0o077)
    target = Path("/certificates/site.pem")
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(pem)
    temporary.replace(target)

    response = command(b"set ssl cert /certificates/site.pem <<\n" + pem + b"\n")
    if "Transaction created" not in response and "Transaction updated" not in response:
        raise RuntimeError("HAProxy rejected certificate transaction")
    response = command(b"commit ssl cert /certificates/site.pem\n")
    if "Success" not in response:
        raise RuntimeError("HAProxy did not commit certificate")


if __name__ == "__main__":
    main()
