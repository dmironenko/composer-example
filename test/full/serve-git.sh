#!/usr/bin/env bash
# Only in the disposable Ubuntu VM, after copying the prepared fixture.
set -euo pipefail
test "$(id -u)" = 0
work=/var/tmp/composer-acceptance
chown -R root:root "$work"
chmod 700 "$work"
install -m 600 "$work/client.pub" /root/composer-acceptance.pub
cat > /etc/systemd/system/composer-acceptance-git.service <<'EOF'
[Unit]
Description=Composer acceptance SSH Git fixture
After=network.target
[Service]
ExecStartPre=/usr/bin/install -d -m 755 /run/sshd
ExecStart=/usr/sbin/sshd -D -f /var/tmp/composer-acceptance/sshd_config
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now composer-acceptance-git.service
