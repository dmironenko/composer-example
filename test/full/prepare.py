"""Create a disposable full-stack fixture; requires local SOPS, age, Git and PyYAML."""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

POSTGRES = "postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
GUEST = "/var/tmp/composer-acceptance"


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.directory.resolve()
    root.mkdir()  # Refuse to overwrite an existing fixture.
    example = Path(__file__).resolve().parents[2]
    repo = root / "repo"
    shutil.copytree(
        example, repo,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".ruff_cache", ".DS_Store"),
    )
    subprocess.run([sys.executable, str(repo / "scripts/configure.py"), str(root / "age.txt")], check=True)
    recipient = run("age-keygen", "-y", str(root / "age.txt"))

    def encrypted(values):
        return subprocess.run(
            ["sops", "--config", os.devnull, "encrypt", "--input-type", "yaml", "--output-type", "yaml",
             "--age", recipient, "--encrypted-regex", "^secrets$", "/dev/stdin"],
            input=yaml.safe_dump(values, sort_keys=False), text=True, capture_output=True, check=True,
        ).stdout

    source = root / "source"
    shutil.copytree(example / "test/full/source", source)
    for directory in (source, repo):
        run("git", "-C", str(directory), "init", "-q", "-b", "master")
        run("git", "-C", str(directory), "config", "user.name", "Composer acceptance")
        run("git", "-C", str(directory), "config", "user.email", "test@example.invalid")
    run("git", "-C", str(source), "config", "uploadpack.allowReachableSHA1InWant", "true")
    commits = []
    for version in ("source-one", "source-two"):
        (source / "version").write_text(version + "\n")
        run("git", "-C", str(source), "add", ".")
        run("git", "-C", str(source), "commit", "-qm", version)
        commits.append(run("git", "-C", str(source), "rev-parse", "HEAD"))
    # serve both repositories with a dedicated loopback-only SSH daemon in the VM.
    for name in ("client", "server"):
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / name))
    (root / "known_hosts").write_text("[127.0.0.1]:22222 " + (root / "server.pub").read_text())
    (root / "sshd_config").write_text(f"""Port 22222
ListenAddress 127.0.0.1
HostKey {GUEST}/server
AuthorizedKeysFile /root/composer-acceptance.pub
PidFile {GUEST}/sshd.pid
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowUsers root
""")
    password = secrets.token_hex(24) + "$literal"
    api = repo / "apps/demo/api"
    base = yaml.safe_load((api / "vars/base.yaml").read_text())
    service = base["compose"]["services"]["api-http"]
    del service["image"], service["command"]
    service["volumes"].append("./configs/password:/etc/app/password:ro")
    service["healthcheck"]["test"] = ["CMD", "/usr/local/bin/busybox", "wget", "-qO", "/dev/null", "http://127.0.0.1:8080"]
    service["healthcheck"]["retries"] = 60
    base["build"] = {"repository": f"ssh://root@127.0.0.1:22222{GUEST}/source", "commit": commits[0]}
    base["secrets"] = {"password": password}
    (api / "vars/base.yaml").write_text(encrypted(base))
    (api / "configs/password.tmpl").write_text("{{ .Vars.secrets.password }}")
    shutil.copyfile(example / "test/full/Dockerfile.tmpl", api / "Dockerfile.tmpl")
    database = repo / "apps/demo/database"
    (database / "vars").mkdir(parents=True)
    (database / "configs").mkdir()
    db = {"secrets": {"password": password}, "compose": {
        "services": {"database": {
            "image": POSTGRES,
            "environment": {"POSTGRES_USER": "composer", "POSTGRES_DB": "composer", "POSTGRES_PASSWORD_FILE": "/config/password"},
            "volumes": ["data:/var/lib/postgresql/data", "./configs/password:/config/password:ro", "./configs/init.sql:/docker-entrypoint-initdb.d/init.sql:ro"],
            "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U composer -d composer"], "interval": "2s", "timeout": "1s", "retries": 30},
        }}, "volumes": {"data": {}}}}
    (database / "vars/base.yaml").write_text(encrypted(db))
    (database / "vars/stage.yaml").write_text("{}\n")
    (database / "configs/password.tmpl").write_text("{{ .Vars.secrets.password }}")
    (database / "configs/init.sql").write_text("CREATE TABLE acceptance (id integer PRIMARY KEY, value text NOT NULL);\nINSERT INTO acceptance VALUES (1, 'seeded');\n")
    # Pre-encrypted alternatives exercise identical plaintext and secret rotation.
    gateway = repo / "apps/edge/gateway/vars/base.yaml"
    env = dict(os.environ, SOPS_AGE_KEY_FILE=str(root / "age.txt"))
    values = yaml.safe_load(subprocess.check_output(["sops", "decrypt", str(gateway)], text=True, env=env))
    (root / "gateway-reencrypted.yaml").write_text(encrypted(values))
    values["secrets"]["stage_token"] = secrets.token_hex(32)
    (root / "gateway-rotated.yaml").write_text(encrypted(values))
    run("git", "-C", str(repo), "add", ".")
    run("git", "-C", str(repo), "commit", "-qm", "full acceptance fixture")
    auth = {"type": "ssh", "key_file": "client", "known_hosts_file": "known_hosts"}
    host = {"version": 1, "installation": "full-check", "source": {
        "mode": "local", "path": "repo", "branches": {"prod": "master", "stage": "master"}, "poll_interval": "1s"},
        "state_dir": "state", "age_key_file": "age.txt",
        "build_auth": auth, "docker": {"host": "unix:///var/run/docker.sock"},
        "reconcile": {"parallelism": 4, "deploy_timeout": "5m"}}
    (root / "host.yaml").write_text(yaml.safe_dump(host))
    remote = dict(host, source={"mode": "remote", "url": f"ssh://root@127.0.0.1:22222{GUEST}/repo", "branches": {"prod": "master", "stage": "master"}, "poll_interval": "1s", "auth": auth})
    (root / "remote.yaml").write_text(yaml.safe_dump(remote))
    (root / "commits.json").write_text(json.dumps(commits))
    print(f"Prepared {root}; copy its contents to {GUEST} in a fresh VM")


if __name__ == "__main__":
    main()
