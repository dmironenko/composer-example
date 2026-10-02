"""Replace the example routing secret and encrypt it for a local age identity."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key_file", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    key_file = args.key_file.resolve()
    if key_file.is_relative_to(repo):
        parser.error("age identity must be outside the GitOps repository")

    os.umask(0o077)
    if not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["age-keygen", "-o", str(key_file)], check=True, capture_output=True
        )
    recipient = subprocess.check_output(
        ["age-keygen", "-y", str(key_file)], text=True
    ).strip()
    path = repo / "apps/edge/gateway/vars/base.yaml"
    values = yaml.safe_load(path.read_text())
    values.pop("sops", None)
    values["secrets"] = {"stage_token": secrets.token_hex(32)}
    encrypted = subprocess.run(
        [
            "sops",
            "--config",
            os.devnull,
            "encrypt",
            "--input-type",
            "yaml",
            "--output-type",
            "yaml",
            "--age",
            recipient,
            "--encrypted-regex",
            "^secrets$",
            "/dev/stdin",
        ],
        input=yaml.safe_dump(values, sort_keys=False),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    path.write_text(encrypted)
    (repo / ".sops.yaml").write_text(
        yaml.safe_dump(
            {
                "creation_rules": [
                    {
                        "path_regex": r"apps/edge/gateway/vars/.*\.yaml$",
                        "encrypted_regex": "^secrets$",
                        "age": recipient,
                    },
                ]
            }
        )
    )
    print(f"Encrypted routing secret. Set host age_key_file to {key_file}")


if __name__ == "__main__":
    main()
