"""Test real HTTP-01 issuance and hot renewal against a local Pebble CA."""

import argparse
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml

from verify import set_value

PEBBLE = (
    "ghcr.io/letsencrypt/pebble@sha256:"
    "ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199"
)


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("host", type=Path)
    parser.add_argument("--https-port", type=int, default=18443)
    args = parser.parse_args()
    host = args.host.resolve()
    settings = yaml.safe_load(host.read_text())
    repo = (host.parent / settings["source"]["path"]).resolve()
    # Create the namespace networks and gateway before attaching the test CA.
    report = json.loads(
        run(args.binary, "apply", "--config", str(host), "--wait").splitlines()[-1]
    )
    project = next(
        p["name"]
        for p in report["projects"]
        if p["enabled"] and "-edge-gateway-" in p["name"]
    )
    gateway_id = run(
        "docker",
        "ps",
        "-q",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        "label=com.docker.compose.service=nginx",
    )
    networks = json.loads(run("docker", "inspect", gateway_id))[0]["NetworkSettings"][
        "Networks"
    ]
    network = next(n for n in networks if "-prod-edge-" in n)
    fixture = host.parent / "acme-test"
    fixture.mkdir()
    ca = settings["installation"] + "-acme-test"
    run("docker", "create", "--name", ca, PEBBLE)
    run(
        "docker",
        "cp",
        f"{ca}:/test/config/pebble-config.json",
        str(fixture / "pebble.json"),
    )
    run("docker", "cp", f"{ca}:/test/certs/pebble.minica.pem", str(fixture / "ca.pem"))
    run("docker", "rm", ca)
    config = json.loads((fixture / "pebble.json").read_text())
    config["pebble"]["httpPort"] = 80
    (fixture / "pebble.json").write_text(json.dumps(config))
    run(
        "docker",
        "run",
        "-d",
        "--name",
        ca,
        "--network",
        network,
        "--network-alias",
        "pebble",
        "-p",
        "127.0.0.1::15000",
        "-e",
        "PEBBLE_VA_NOSLEEP=1",
        "-e",
        "PEBBLE_AUTHZREUSE=0",
        "-e",
        "PEBBLE_WFE_NONCEREJECT=0",
        "-v",
        f"{fixture}/pebble.json:/test/config/check.json:ro",
        PEBBLE,
        "-config",
        "/test/config/check.json",
    )
    binding = json.loads(run("docker", "inspect", ca))[0]["NetworkSettings"]["Ports"][
        "15000/tcp"
    ][0]
    management_port = binding["HostPort"]
    trust = ssl.create_default_context(cafile=str(fixture / "ca.pem"))
    for _ in range(30):
        try:
            with urllib.request.urlopen(
                f"https://localhost:{management_port}/roots/0", context=trust
            ) as response:
                (fixture / "root.pem").write_bytes(response.read())
            break
        except OSError:
            time.sleep(1)

    gateway = repo / "apps/edge/gateway"
    shutil.copyfile(fixture / "ca.pem", gateway / "configs/pebble-ca.pem")
    base = gateway / "vars/base.yaml"
    values = yaml.safe_load(base.read_text())
    values.setdefault("tls", {})["domain"] = "composer.test"
    compose = values.setdefault("compose", {})
    nginx = compose.setdefault("services", {}).setdefault("nginx", {})
    nginx.update(
        {
            "networks": {
                "default": {"aliases": ["composer.test"]},
            },
            "volumes": [
                "./configs:/etc/nginx/user_conf.d:ro",
                "./configs/redirector.conf:/etc/nginx/conf.d/redirector.conf:ro",
                "acme_certificates:/etc/letsencrypt",
            ],
            "environment": {
                "REQUESTS_CA_BUNDLE": "/etc/nginx/user_conf.d/pebble-ca.pem",
                "USE_LOCAL_CA": "0",
                "CERTBOT_EMAIL": "test@example.test",
                "CERTBOT_PRODUCTION_URL": "https://pebble:14000/dir",
            },
        }
    )
    compose["volumes"] = {"acme_certificates": {}}
    for key in ("tls", "compose"):
        set_value(base, [key], values[key], host.parent / settings["age_key_file"])
    run("git", "-C", str(repo), "add", "apps")
    run("git", "-C", str(repo), "commit", "-qm", "test local ACME")
    result = run(
        args.binary, "apply", "--config", str(host), "--wait", "--timeout", "5m"
    )
    projects = json.loads(result.splitlines()[-1])["projects"]
    project = next(
        p["name"] for p in projects if p["enabled"] and "-edge-gateway-" in p["name"]
    )

    def service(name):
        return run(
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            f"label=com.docker.compose.service={name}",
        )

    def fingerprint():
        context = ssl.create_default_context(cafile=str(fixture / "root.pem"))
        with socket.create_connection(
            ("127.0.0.1", args.https_port), timeout=5
        ) as connection:
            with context.wrap_socket(
                connection, server_hostname="composer.test"
            ) as secure:
                return hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()

    def issued(previous=None):
        for _ in range(120):
            try:
                current = fingerprint()
                if current != previous:
                    return current
            except (OSError, ssl.SSLError):
                pass
            time.sleep(1)
        raise AssertionError("nginx did not serve a trusted, renewed certificate")

    first = issued()
    ids = run("docker", "ps", "-q").split()
    before = {
        c["Id"]: c["State"]["StartedAt"]
        for c in json.loads(run("docker", "inspect", *ids))
    }
    run(
        "docker",
        "exec",
        service("nginx"),
        "/scripts/run_certbot.sh",
        "force",
    )
    issued(first)
    after = {
        c["Id"]: c["State"]["StartedAt"]
        for c in json.loads(run("docker", "inspect", *ids))
    }
    assert before == after, "certificate renewal restarted a container"
    print(
        "PASS: HTTP-01, trusted TLS, certificate changed without restarts",
        flush=True,
    )

    # Persisted PEM must also be used after nginx itself restarts.
    current = fingerprint()
    run("docker", "restart", service("nginx"))
    assert issued() == current
    print("PASS: renewed certificate persisted across nginx restart", flush=True)
    run("docker", "rm", "-f", ca)


if __name__ == "__main__":
    main()
