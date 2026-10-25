#!/bin/sh
# Runs at every node start: k3d's entrypoint executes /bin/k3d-entrypoint-*.sh in
# alphabetical order before k3s starts, so "zz" runs after k3d's own DNS fix.
#
# Why: Docker Desktop's DNS (192.168.65.254) times out on some queries (AAAA for
# ghcr.io), so containerd fails with "lookup ghcr.io: Try again" and pods sit in
# ImagePullBackOff. Docker rewrites /etc/resolv.conf on every container start, so
# a one-off edit is lost after "k3d cluster stop/start" - this script re-adds it.
# CoreDNS forwards to this file, so pods get the fallback resolver too.
set -o errexit
if ! grep -q "^nameserver 1.1.1.1" /etc/resolv.conf; then
  echo "nameserver 1.1.1.1" >> /etc/resolv.conf
  echo "[dns-fallback] added 1.1.1.1 to /etc/resolv.conf"
fi
