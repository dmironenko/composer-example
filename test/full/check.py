"""Full-stack acceptance against the disposable fixture produced by prepare.py."""

import argparse
import json
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify import set_value


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("host", type=Path)
    parser.add_argument("action", choices=["exercise", "checkpoint", "reboot-check"], default="exercise", nargs="?")
    args = parser.parse_args()
    host = args.host.resolve()
    root, repo = host.parent, host.parent / "repo"
    settings = yaml.safe_load(host.read_text())
    prefix = settings["installation"] + "-"
    base_file = repo / "apps/demo/api/vars/base.yaml"
    age_key = root / settings["age_key_file"]
    first, second = json.loads((root / "commits.json").read_text())
    tls = ssl._create_unverified_context()  # Test bootstrap certificate only.

    def apply(fail=False):
        result = subprocess.run([args.binary, "apply", "--config", str(host), "--wait",
                                 "--timeout", "25s" if fail else "5m"], capture_output=True, text=True)
        (root / "last-apply.log").write_text(result.stdout + result.stderr)
        assert (result.returncode != 0) == fail, "Unexpected apply result; see last-apply.log"
        if not fail:
            return json.loads(result.stdout.splitlines()[-1])

    def commit(message):
        run("git", "-C", str(repo), "add", "apps")
        run("git", "-C", str(repo), "commit", "-qm", message)

    def revert():
        run("git", "-C", str(repo), "revert", "--no-edit", "HEAD")
        apply()

    def snapshot():
        ids = run("docker", "ps", "-aq").split()
        containers = json.loads(run("docker", "inspect", *ids)) if ids else []
        return {c["Name"]: c for c in containers
                if c["Config"].get("Labels", {}).get("com.docker.compose.project", "").startswith(prefix)}

    def app(environment, name):
        return next(c for c in snapshot().values()
                    if f"-{environment}-demo-{name}-" in c["Config"]["Labels"]["com.docker.compose.project"])

    def identity(items):
        return {name: (c["Id"], c["State"]["StartedAt"]) for name, c in items.items()}

    def neighbours(before, allowed):
        after = snapshot()
        assert {n: v for n, v in identity(before).items() if not allowed(before[n])} == {
            n: v for n, v in identity(after).items() if not allowed(after[n])}, "Unrelated containers changed"

    def token():
        # Read the active version inside Docker; Desktop may expose VM-only mount paths.
        gateway = next(c for c in snapshot().values() if c["Config"]["Labels"].get("com.docker.compose.service") == "nginx" and "-edge-gateway-" in c["Config"]["Labels"]["com.docker.compose.project"])
        config = run("docker", "exec", gateway["Id"], "cat", "/etc/nginx/user_conf.d/edge.conf")
        return re.search(r'"([^"\s]+):stage" env_stage;' , config)[1]

    def response(cookie=None):
        req = urllib.request.Request("https://localhost:18443/api/",
                                     headers={} if cookie is None else {"Cookie": f"composer_stage={cookie}:stage"})
        with urllib.request.urlopen(req, context=tls, timeout=5) as result:
            return result.read().decode()

    def expect(*parts, cookie=None):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                value = response(cookie)
                if all(part in value for part in parts):
                    return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise AssertionError(f"HTTP response did not contain {parts}")

    def sql(environment, statement):
        return run("docker", "exec", app(environment, "database")["Id"], "psql", "-U", "composer", "-d", "composer", "-Atqc", statement)

    if args.action == "checkpoint":
        assert sql("prod", "SELECT value FROM acceptance WHERE id=2") == "preserved"
        (root / "checkpoint.json").write_text(json.dumps({"boot": Path("/proc/sys/kernel/random/boot_id").read_text(),
            "ids": {n: c["Id"] for n, c in snapshot().items()}}))
        print("PASS: saved full-stack reboot checkpoint", flush=True)
        return
    if args.action == "reboot-check":
        saved = json.loads((root / "checkpoint.json").read_text())
        assert saved["boot"] != Path("/proc/sys/kernel/random/boot_id").read_text()
        run("systemctl", "is-active", "--quiet", "composer-full-check.service")
        expect("api: production", "source-one", "database: seeded")
        expect("api: staging", "source-one", cookie=token())
        assert saved["ids"] == {n: c["Id"] for n, c in snapshot().items()}
        assert sql("prod", "SELECT value FROM acceptance WHERE id=2") == "preserved"
        set_value(base_file, ["message"], "api: reboot-polled", age_key)
        commit("polling after reboot")
        expect("api: reboot-polled")
        run("git", "-C", str(repo), "revert", "--no-edit", "HEAD")
        expect("api: production")
        assert sql("prod", "SELECT value FROM acceptance WHERE id=2") == "preserved"
        print("PASS: full-stack reboot, same container IDs, persistent database/TLS, systemd polling commit and revert", flush=True)
        return

    expect("api: production", "source-one", "database: seeded")
    expect("api: staging", "source-one", "database: seeded", cookie=token())
    prod, stage = app("prod", "api"), app("stage", "api")
    assert prod["Image"] == stage["Image"]
    assert json.loads(run("docker", "image", "inspect", prod["Image"]))[0]["Config"]["Labels"]["org.opencontainers.image.revision"] == first
    assert "ENC[AES256_GCM," in (repo / "apps/demo/api/vars/base.yaml").read_text()
    sql("prod", "INSERT INTO acceptance VALUES (2, 'preserved')")
    assert sql("stage", "SELECT count(*) FROM acceptance WHERE id=2") == "0"
    print("PASS: source build, shared image, encrypted DB password with literal $, schema and isolated data", flush=True)
    subprocess.run([sys.executable, str(repo / "test/verify.py"), args.binary, str(host)], check=True)

    before = snapshot()
    gateway = repo / "apps/edge/gateway/vars/base.yaml"
    shutil.copyfile(root / "gateway-reencrypted.yaml", gateway)
    commit("reencrypt identical secret")
    assert all(p["action"] == "noop" for p in apply()["projects"] if p["enabled"])
    assert identity(snapshot()) == identity(before)
    old_token = token()
    shutil.copyfile(root / "gateway-rotated.yaml", gateway)
    commit("rotate routing secret")
    apply()
    new_token = token()
    assert old_token != new_token
    expect("api: production", cookie=old_token)
    expect("api: staging", cookie=new_token)
    neighbours(before, lambda c: "-edge-gateway-" in c["Config"]["Labels"]["com.docker.compose.project"])
    print("PASS: re-encryption is noop; secret-only rotation changes live routing and preserves neighbours", flush=True)

    stage_file = repo / "apps/demo/api/vars/stage.yaml"
    stage_vars = yaml.safe_load(stage_file.read_text())
    stage_vars["build"] = {"commit": second}
    stage_file.write_text(yaml.safe_dump(stage_vars))
    before = snapshot()
    commit("stage source-two")
    apply()
    expect("source-two", cookie=token())
    expect("source-one")
    neighbours(before, lambda c: "-stage-demo-api-" in c["Config"]["Labels"]["com.docker.compose.project"])
    second_image = app("stage", "api")["Image"]
    assert second_image != prod["Image"]

    # Removing the checkout credential proves these operations use Docker images.
    key = root / settings["build_auth"]["key_file"]
    key.rename(key.with_suffix(".disabled"))
    try:
        set_value(base_file, ["build", "commit"], second, age_key)
        commit("promote source-two")
        apply()
        expect("source-two")
        assert app("prod", "api")["Image"] == second_image
        revert()
        expect("source-one")
        assert app("prod", "api")["Image"] == prod["Image"]
        before = snapshot()
        set_value(base_file, ["message"], "api: cached-config", age_key)
        commit("config-only without source access")
        apply()
        expect("api: cached-config", "source-one")
        assert app("prod", "api")["Image"] == prod["Image"]
        assert app("prod", "api")["Id"] != next(c["Id"] for c in before.values() if "-prod-demo-api-" in c["Name"])
        neighbours(before, lambda c: "-prod-demo-api-" in c["Config"]["Labels"]["com.docker.compose.project"])
        revert()
    finally:
        key.with_suffix(".disabled").rename(key)
    print("PASS: independent stage SHA, promotion, Git revert and config-only without source credentials", flush=True)

    # Failed build leaves the old API alive; another app can still roll out.
    before = snapshot()
    dockerfile = repo / "apps/demo/api/Dockerfile.tmpl"
    dockerfile.write_text(dockerfile.read_text() + '\n{{ if eq .Env "prod" }}RUN exit 1{{ end }}\n')
    nginx = repo / "apps/demo/nginx/vars/stage.yaml"
    values = yaml.safe_load(nginx.read_text())
    values["config_revision"] = "alongside-failed-build"
    nginx.write_text(yaml.safe_dump(values))
    commit("failed build with independent nginx update")
    apply(fail=True)
    expect("source-one")
    assert app("prod", "api")["Id"] == next(c["Id"] for c in before.values() if "-prod-demo-api-" in c["Name"])
    assert app("stage", "nginx")["Id"] != next(c["Id"] for c in before.values() if "-stage-demo-nginx-" in c["Name"])
    revert()
    set_value(base_file, ["compose", "services", "api-http", "entrypoint"], ["sh", "-c", "exit 1"], age_key)
    commit("failed runtime rollout")
    apply(fail=True)
    revert()
    expect("source-one")
    assert app("prod", "api")["Image"] == prod["Image"]
    print("PASS: failed build preserves API, independent rollout succeeds, revert repairs partial runtime failure", flush=True)

    # Restore stage to the shared base commit and exercise real SSH fetch polling.
    stage_vars.pop("build")
    stage_file.write_text(yaml.safe_dump(stage_vars))
    commit("stage inherits base commit")
    apply()
    before = snapshot()
    with (root / "run.log").open("w") as log:
        process = subprocess.Popen([args.binary, "run", "--config", str(root / "remote.yaml")], stdout=log, stderr=log)
        try:
            time.sleep(3)
            assert process.poll() is None
            assert identity(snapshot()) == identity(before)
            set_value(base_file, ["message"], "api: polled", age_key)
            commit("remote polling update")
            expect("api: polled")
            neighbours(before, lambda c: "-prod-demo-api-" in c["Config"]["Labels"]["com.docker.compose.project"])
            run("git", "-C", str(repo), "revert", "--no-edit", "HEAD")
            expect("api: production")
        finally:
            process.terminate()
            process.wait(timeout=30)
    assert sql("prod", "SELECT value FROM acceptance WHERE id=2") == "preserved"
    print("PASS: GitOps polling, commit and revert, stop preserves containers and database", flush=True)


if __name__ == "__main__":
    main()
