#!/usr/bin/env bash
# Cloud Shell entry point (free, no billing): unpacks the bundle and runs the
# Docker validation ledger. View the console afterwards with Web Preview on 3000.
set -euo pipefail
rm -rf ~/crosstalk && mkdir ~/crosstalk && tar -xzf ~/crosstalk.tar.gz -C ~/crosstalk
# Lab provenance reads the git state; snapshot the bundle (its real commit is in COMMIT).
git -C ~/crosstalk init -q && git -C ~/crosstalk add -A &&
  git -C ~/crosstalk -c user.email=lab@cloudshell -c user.name=cloudshell \
    commit -qm "snapshot of $(cat ~/crosstalk/COMMIT)"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
CT_CODE_COMMIT="$(cat ~/crosstalk/COMMIT)" bash ~/crosstalk/deploy/cloud-vm/validate.sh
