#!/usr/bin/env bash
# Join this container to the user's tailnet when TS_AUTHKEY is set at launch
# (a no-op otherwise, so launches without the key are unaffected). Installed +
# granted sudo by the overlay Dockerfile; NET_ADMIN, /dev/net/tun and the
# TS_AUTHKEY / TS_HOSTNAME env come from overlay.json.
#
# Runs as the mapped non-root runtime user, to completion (tailscaled is
# backgrounded here; `tailscale up` returns once the node is authenticated).
set -u

HOSTNAME_TS="${TS_HOSTNAME:-claude-rd03e}"

if [ -z "${TS_AUTHKEY:-}" ]; then
  echo "[tailnet] TS_AUTHKEY not set — not joining the tailnet (export it before"
  echo "[tailnet]   claude-container to enable; see .claude-container-overlay)." >&2
  exit 0
fi

if [ ! -c /dev/net/tun ]; then
  echo "[tailnet] /dev/net/tun is missing — the overlay's devices/capabilities" >&2
  echo "[tailnet]   didn't apply. Relaunch claude-container so overlay.json takes effect." >&2
  exit 0
fi

sudo mkdir -p /var/lib/tailscale /var/run/tailscale

# tailscaled with a real TUN so a tailscale0 interface appears and ordinary
# connections (ssh, the hitl CLI, gdbserver ports) route transparently.
# --netfilter-mode=off: a client-only node needn't own the container's iptables.
if ! pgrep -x tailscaled >/dev/null 2>&1; then
  # --netfilter-mode is an `up` flag, NOT a tailscaled flag (tailscaled rejects it
  # and dumps usage). Keep the daemon invocation to real daemon flags only.
  sudo tailscaled \
      --state=/var/lib/tailscale/tailscaled.state \
      --socket=/var/run/tailscale/tailscaled.sock \
      --tun=tailscale0 >/tmp/tailscaled.log 2>&1 &
fi

# Wait for the control socket before bringing the node up.
for _ in $(seq 1 30); do
  [ -S /var/run/tailscale/tailscaled.sock ] && break
  sleep 0.3
done

# --operator=<runtime user> lets me run `tailscale ...` without sudo afterwards.
if ! sudo tailscale up \
      --authkey="$TS_AUTHKEY" \
      --hostname="$HOSTNAME_TS" \
      --operator="$(id -un)" \
      --netfilter-mode=off \
      --accept-routes; then
  echo "[tailnet] 'tailscale up' failed — see /tmp/tailscaled.log (key spent/expired," >&2
  echo "[tailnet]   ACL approval needed, or no egress to the control plane)." >&2
  exit 0
fi

echo "[tailnet] joined as '$HOSTNAME_TS'." >&2
tailscale status 2>/dev/null | head -20 >&2 || true
