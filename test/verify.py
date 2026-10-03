"""Exercise routing and rollouts in a disposable GitOps checkout with Docker."""

import argparse
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def set_value(path: Path, keys: list[str], value, age_key: Path) -> None:
    """Edit fixture vars, using SOPS to preserve authentication of encrypted files."""
    values = yaml.safe_load(path.read_text())
    encrypted = "sops" in values
    env = dict(os.environ, SOPS_AGE_KEY_FILE=str(age_key))
    if encrypted:
        values = yaml.safe_load(subprocess.check_output(
            ["sops", "decrypt", str(path)], text=True, env=env))
    target = values
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value
    if encrypted:
        subprocess.run(
            ["sops", "set", "--value-stdin", str(path), "[" + json.dumps(keys[0]) + "]"],
            input=json.dumps(values[keys[0]]), text=True, check=True, env=env,
        )
    else:
        path.write_text(yaml.safe_dump(values))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("host", type=Path)
    parser.add_argument("--https-port", type=int, default=18443)
    args = parser.parse_args()
    host = args.host.resolve()
    settings = yaml.safe_load(host.read_text())
    repo = (host.parent / settings["source"]["path"]).resolve()
    state = (host.parent / settings["state_dir"]).resolve()

    def apply() -> dict:
        result = run(
            args.binary, "apply", "--config", str(host), "--wait", "--timeout", "5m"
        )
        return json.loads(result.splitlines()[-1])

    # These tests use one shared branch; keep the stage replica count independent.
    stage_api = repo / "apps/demo/api/vars/stage.yaml"
    set_value(stage_api, ["compose", "services", "api-http", "deploy", "replicas"],
              1, host.parent / settings["age_key_file"])
    run("git", "-C", str(repo), "add", str(stage_api))
    if run("git", "-C", str(repo), "diff", "--cached", "--name-only"):
        run("git", "-C", str(repo), "commit", "-qm", "test independent stage replicas")

    report = apply()
    projects = {p["name"]: p for p in report["projects"] if p["enabled"]}
    proxy_config = next(state.glob("deployments/prod/*/configs/*/configs/edge.conf"))
    token = re.search(r'"([^"\s]+):stage" env_stage;', proxy_config.read_text())[1]
    encrypted = (repo / "apps/edge/gateway/vars/base.yaml").read_text()
    assert "ENC[AES256_GCM," in encrypted and token not in encrypted
    tls = ssl._create_unverified_context()  # Only the local bootstrap certificate.

    def request(path="/api/", cookie=None):
        headers = {} if cookie is None else {"Cookie": f"composer_stage={cookie}:stage"}
        req = urllib.request.Request(
            f"https://localhost:{args.https_port}" + path, headers=headers
        )
        try:
            with urllib.request.urlopen(req, context=tls, timeout=35) as response:
                return response.status, response.headers, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read().decode()

    def wait_response(expected, path="/api/", cookie=None):
        for _ in range(60):
            response = request(path, cookie)
            if response[2].startswith(expected):
                assert response[0] == 200
                return response
            time.sleep(1)
        raise AssertionError(f"Unexpected HTTP response: {response[0]}, {response[2]}")

    def snapshot() -> dict:
        result = {}
        for name in projects:
            ids = run(
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={name}",
            ).split()
            containers = json.loads(run("docker", "inspect", *ids)) if ids else []
            result[name] = sorted(
                (c["Id"], c["State"]["StartedAt"]) for c in containers
            )
        return result

    def project(environment, app):
        return next(
            name
            for name, p in projects.items()
            if p["environment"] == environment and f"-demo-{app}-" in name
        )

    def commit(message):
        run("git", "-C", str(repo), "add", "apps")
        run("git", "-C", str(repo), "commit", "-qm", message)
        apply()

    def unchanged(before, after, changed):
        assert {k: v for k, v in before.items() if k not in changed} == {
            k: v for k, v in after.items() if k not in changed
        }, "unrelated containers changed"

    wait_response("api: production")
    wait_response("api: staging", cookie=token)
    wait_response("api: production", cookie="incorrect")
    assert request("/", token)[0] in (502, 503), (
        "stage unexpectedly reached prod-only web"
    )
    before = snapshot()
    apply()
    assert snapshot() == before, "noop restarted containers"
    print(
        "PASS: bootstrap TLS, prod/stage cookie routing, no fallback, noop", flush=True
    )

    # A stage nginx config change must not restart the prod nginx or any app.
    stage_file = repo / "apps/demo/nginx/vars/stage.yaml"
    stage = yaml.safe_load(stage_file.read_text())
    stage["config_revision"] = "stage-v2"
    stage_file.write_text(yaml.safe_dump(stage))
    commit("test stage nginx config")
    response = wait_response("api: staging", cookie=token)
    assert response[1]["X-Config-Revision"] == "stage-v2"
    assert request()[1]["X-Config-Revision"] == "v1"
    unchanged(before, snapshot(), {project("stage", "nginx")})
    print("PASS: stage nginx config applied, neighbours unchanged", flush=True)

    # Scale the API through deploy.replicas and wait for nginx DNS refresh.
    before = snapshot()
    base_file = repo / "apps/demo/api/vars/base.yaml"
    original = base_file.read_bytes()
    set_value(base_file, ["compose", "services", "api-http", "deploy", "replicas"],
              3, host.parent / settings["age_key_file"])
    commit("test three API replicas")
    scaled = snapshot()
    api = project("prod", "api")
    assert len(scaled[api]) == 3 and set(before[api]) <= set(scaled[api])
    unchanged(before, scaled, {api})
    instances = set()
    for _ in range(60):
        instances.add(wait_response("api: production")[2].splitlines()[-1])
        if len(instances) == 3:
            break
        time.sleep(1)
    assert len(instances) == 3, f"nginx reached {len(instances)} of 3 replicas"
    base_file.write_bytes(original)
    commit("test scale back to one")
    assert len(snapshot()[api]) == 1
    print("PASS: deploy.replicas 1 -> 3 -> 1, DNS load balancing", flush=True)

    before = snapshot()
    api_file = repo / "apps/demo/api/configs/message.txt.tmpl"
    api_file.write_text(
        '{{ if eq .Env "prod" }}api: updated{{ else }}{{ .Vars.message }}{{ end }}\n'
    )
    commit("test config-only app update")
    wait_response("api: updated")
    unchanged(before, snapshot(), {api})
    run("git", "-C", str(repo), "revert", "--no-edit", "HEAD")
    apply()
    wait_response("api: production")
    print("PASS: config-only rollout and Git revert", flush=True)

    stage["enabled"] = False
    stage_file.write_text(yaml.safe_dump(stage))
    commit("test unavailable stage nginx")
    for _ in range(30):
        if request(cookie=token)[0] == 503:
            break
        time.sleep(1)
    assert request(cookie=token)[0] == 503
    wait_response("api: production")
    run("git", "-C", str(repo), "revert", "--no-edit", "HEAD")
    apply()
    wait_response("api: staging", cookie=token)

    prod_nginx = snapshot()[project("prod", "nginx")][0][0]
    stage_nginx = snapshot()[project("stage", "nginx")][0][0]
    details = json.loads(run("docker", "inspect", prod_nginx, stage_nginx))
    assert set(details[0]["NetworkSettings"]["Networks"]).isdisjoint(
        details[1]["NetworkSettings"]["Networks"]
    )
    print(
        "PASS: missing stage returns 503, prod remains available, networks isolated",
        flush=True,
    )


if __name__ == "__main__":
    main()
