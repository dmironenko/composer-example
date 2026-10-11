"""Verify personal environments in a disposable checkout; modifies branches and host YAML."""

import argparse
import json
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from verify import run, set_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("host", type=Path)
    parser.add_argument("--https-port", type=int, default=18443)
    args = parser.parse_args()
    host = args.host.resolve()
    settings = yaml.safe_load(host.read_text())
    age_key = host.parent / settings["age_key_file"] if settings.get("age_key_file") else None
    repo = (host.parent / settings["source"]["path"]).resolve()
    prefix = settings["installation"] + "-"
    master, stage = settings["source"]["branches"]["prod"], settings["source"]["branches"]["stage"]
    assert master != stage, "Use separate prod and stage fixture branches"
    tls = ssl._create_unverified_context()

    def commit(message):
        run("git", "-C", str(repo), "add", "apps")
        run("git", "-C", str(repo), "commit", "-qm", message)

    def save_host():
        temporary = host.with_suffix(".tmp")
        temporary.write_text(yaml.safe_dump(settings))
        temporary.replace(host)

    def apply():
        run(args.binary, "apply", "--config", str(host), "--wait", "--timeout", "5m")

    def snapshot():
        ids = run("docker", "ps", "-aq", "--filter", "label=com.docker.compose.project").split()
        containers = json.loads(run("docker", "inspect", *ids)) if ids else []
        return {c["Config"]["Labels"]["com.docker.compose.project"]: c for c in containers
                if c["Config"]["Labels"]["com.docker.compose.project"].startswith(prefix)}

    def app(env, name):
        return next(c for project, c in snapshot().items() if f"-{env}-demo-{name}-" in project)

    def wait(predicate):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (OSError, StopIteration, urllib.error.URLError):
                pass
            time.sleep(1)
        raise AssertionError("Timed out waiting for the personal environment")

    def response(environment=None):
        headers = {} if environment is None else {"Cookie": f"composer_stage={token}:{environment}"}
        req = urllib.request.Request(f"https://localhost:{args.https_port}/api/", headers=headers)
        try:
            with urllib.request.urlopen(req, context=tls, timeout=5) as result:
                return result.status, result.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def expect(message, environment=None):
        wait(lambda: response(environment)[1].startswith(message))

    def unchanged():
        after = snapshot()
        for name, before in neighbours.items():
            assert (after[name]["Id"], after[name]["State"]["StartedAt"]) == before, name

    # Give every API an ordinary project-scoped volume to verify data isolation and retention.
    run("git", "-C", str(repo), "checkout", "-q", master)
    base = repo / "apps/demo/api/vars/base.yaml"
    values = yaml.safe_load(base.read_text())
    volumes = values["compose"]["services"]["api-http"]["volumes"] + ["personal_data:/personal"]
    set_value(base, ["compose", "services", "api-http", "volumes"], volumes, age_key)
    set_value(base, ["compose", "volumes", "personal_data"], {}, age_key)
    commit("test environment data volumes")
    run("git", "-C", str(repo), "branch", "-f", stage, master)
    run("git", "-C", str(repo), "checkout", "-qb", "users/dan", master)
    apply()
    gateway = next(c for name, c in snapshot().items() if "-edge-gateway-" in name)
    config = run("docker", "exec", gateway["Id"], "cat", "/etc/nginx/user_conf.d/edge.conf")
    token = re.search(r'"([^"\s]+):stage" env_stage;', config)[1]
    neighbours = {n: (c["Id"], c["State"]["StartedAt"]) for n, c in snapshot().items()
                  if "-edge-gateway-" not in n}
    expect("api: production")
    expect("api: staging", "stage")

    with (host.parent / "environments.log").open("w") as log:
        process = subprocess.Popen([args.binary, "run", "--config", str(host)], stdout=log, stderr=log)
        try:
            settings["source"]["branches"]["dan"] = "users/dan"
            save_host()
            expect("api: staging", "dan")
            personal = app("dan", "api")
            assert set(personal["NetworkSettings"]["Networks"]).isdisjoint(app("prod", "api")["NetworkSettings"]["Networks"])
            run("docker", "exec", personal["Id"], "sh", "-c", "echo preserved > /personal/check")
            volume = next(m["Name"] for m in personal["Mounts"] if m["Destination"] == "/personal")
            assert all(volume != m.get("Name") for m in app("prod", "api")["Mounts"])
            unchanged()
            print("PASS: one branches entry creates a routed, isolated personal stage", flush=True)

            config_file = repo / "apps/demo/api/configs/message.txt.tmpl"
            config_file.write_text("api: dan\n")
            commit("personal config update")
            expect("api: dan", "dan")
            updated = app("dan", "api")
            assert updated["Id"] != personal["Id"] and updated["Image"] == personal["Image"]
            unchanged()

            host.write_text("source: [\n")
            config_file.write_text("api: dan-with-invalid-host\n")
            commit("personal update during invalid host config")
            expect("api: dan-with-invalid-host", "dan")
            unchanged()
            save_host()
            print("PASS: config-only update and invalid host YAML preserve other environments", flush=True)

            revision = run("git", "-C", str(repo), "rev-parse", "HEAD")
            run("git", "-C", str(repo), "checkout", "-q", master)
            personal_id = app("dan", "api")["Id"]
            run("git", "-C", str(repo), "branch", "-D", "users/dan")
            wait(lambda: "configured branch does not exist" in (host.parent / "environments.log").read_text())
            assert app("dan", "api")["Id"] == personal_id
            expect("api: dan-with-invalid-host", "dan")
            run("git", "-C", str(repo), "branch", "users/dan", revision)

            settings["source"]["branches"]["ghost"] = "missing-branch"
            save_host()
            wait(lambda: "-ghost-demo-" in run("docker", "network", "ls", "--format", "{{.Name}}"))
            wait(lambda: response("ghost")[0] == 503)
            expect("api: production")
            del settings["source"]["branches"]["ghost"]
            del settings["source"]["branches"]["dan"]
            save_host()
            wait(lambda: not any("-dan-" in name for name in snapshot()))
            wait(lambda: response("dan")[0] == 503)
            run("docker", "volume", "inspect", volume)
            run("docker", "image", "inspect", personal["Image"])
            unchanged()
            assert process.poll() is None
            print("PASS: missing branch retains deployment; removed entry removes containers, keeps volumes/images", flush=True)
        finally:
            process.terminate()
            process.wait(timeout=30)

    # Re-create from retained data, then remove while the agent is stopped.
    settings["source"]["branches"]["dan"] = "users/dan"
    save_host()
    apply()
    assert run("docker", "exec", app("dan", "api")["Id"], "cat", "/personal/check") == "preserved"
    del settings["source"]["branches"]["dan"]
    save_host()
    apply()
    assert not any("-dan-" in name for name in snapshot())
    unchanged()
    print("PASS: retained data reused; removal works after agent restart", flush=True)


if __name__ == "__main__":
    main()
