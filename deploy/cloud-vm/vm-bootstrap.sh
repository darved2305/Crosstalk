#!/usr/bin/env bash
# GCE startup script: installs Docker Engine and uv once.
set -euo pipefail
[[ -f /var/lib/crosstalk-bootstrapped ]] && exit 0

apt-get update
apt-get install -y ca-certificates curl ethtool tshark
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  >/etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
for home in /home/*; do usermod -aG docker "$(basename "$home")" || true; done

curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

touch /var/lib/crosstalk-bootstrapped
