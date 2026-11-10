# claude-container overlay — appended to the base image Dockerfile by the
# launcher. The build runs as root; the container then runs as a non-root user
# the entrypoint creates at start, mapped to the host UID/GID (named `claude`).
# See .claude/skills/container-overlay.
#
# Nix blocks adapted from fughilli/splanc's overlay (the known-good pattern);
# only the Nix install + per-user-profile repair are kept here — this RE harness
# needs neither tailscale, prek, nor the GUI/playwright libs splanc carries.

# Nix with flakes — required by the Ghidra / ARM-binutils run targets. `bazel
# run //ghidra:analyze_fw`, `nix develop`, and generating flake.lock all shell
# out to `nix`, which is absent from the base image. This is the container-
# overlay skill's "project uses Nix" case: install once here; per-package
# additions go in the repo's flake.nix, not this overlay.
#
# Uses the Determinate Systems nix-installer: the upstream installer's
# single-user path shells out to `sudo` (absent from the base image), so it
# fails even when the build itself is already running as root. Determinate's
# installer needs no sudo and exposes `--init none` to skip the systemd daemon
# setup that has no place in a container.
#
# build-users-group is cleared so builds run as the calling user (no nixbld
# group is created), and the sandbox is disabled since the container can't set
# up build-sandbox user namespaces without privilege.
RUN curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix \
        -o /tmp/nix-installer.sh \
    && sh /tmp/nix-installer.sh install linux \
        --init none \
        --no-confirm \
        --extra-conf "experimental-features = nix-command flakes" \
        --extra-conf "build-users-group =" \
        --extra-conf "sandbox = false" \
    && rm /tmp/nix-installer.sh
ENV PATH=/nix/var/nix/profiles/default/bin:$PATH

# The base image has no `claude` user at build time: the entrypoint fabricates
# it at container start, mapping the *host* UID/GID in (so its uid/gid — and
# even its group name — vary per host). The Determinate installer leaves /nix
# owned by root, so daemon-less Nix as that runtime user fails trying to chmod
# the root-owned `/nix/var/nix/profiles/per-user` ("Operation not permitted"),
# which blocks every `nix` invocation.
# We can't chown to a name/uid that doesn't exist yet and isn't known at build
# time, so instead make the single-user store group/other-writable: any runtime
# uid can then manage profiles, the DB, and gcroots without a daemon. Portable
# across hosts. (Store stays in /nix; the prebuilt paths and DB are preserved.)
#
# The per-user profile/gcroots dirs need extra care: Nix re-chmods them to 0755
# on *every* invocation and fails with EPERM when it doesn't own them — and
# go+rwX leaves them 0777, which guarantees that failed re-chmod. So delete them
# here; the runtime user recreates and owns them on its first `nix` call (the
# go+rwX parents above make that mkdir possible).
#
# BUT deleting per-user also severs the `default` profile: the installer makes
# /nix/var/nix/profiles/default a symlink INTO per-user/root/profile, so after
# the rm -rf the ENV PATH above points through a dangling link and no `nix` is
# callable at all. Resolve default to its store path FIRST and re-point it
# directly at the store — profile MUTATION by the runtime user was never
# supported here; PATH access to the pinned binaries is all `default` provides.
RUN PROFILE_STORE_PATH="$(readlink -f /nix/var/nix/profiles/default)" \
    && chmod -R go+rwX /nix \
    && rm -rf /nix/var/nix/profiles/per-user /nix/var/nix/gcroots/per-user \
    && ln -sfn "$PROFILE_STORE_PATH" /nix/var/nix/profiles/default

# Mark /workspace safe for git. The bind mount is owned by the host uid, which
# can differ from the runtime user's, tripping git's dubious-ownership check.
# Must be --system (/etc/gitconfig): --global at build time would write
# /root/.gitconfig, which the runtime `claude` user never sees.
RUN git config --system --add safe.directory /workspace

# 2026-11-10: tailscale (static arm64 binaries) + scoped sudo, so launching
# claude-container with TS_AUTHKEY set joins this container to the user's tailnet
# and I can drive the rig-radar HITL bench directly. Matches fughilli/splanc's
# overlay pattern. Runtime also needs NET_ADMIN + /dev/net/tun + the TS_AUTHKEY
# env, and startup.sh to bring the node up — all declared in overlay.json /
# startup.sh (both excluded from the image hash, so they never force a rebuild).
RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

# Passwordless sudo for the runtime user `claude` (the entrypoint always names it
# `claude`, mapping the host uid/gid in). startup.sh needs more than the tailscale
# binaries — `sudo mkdir` for the state/socket dirs, etc. — so a command-scoped
# rule is too narrow (it denies `sudo mkdir` and the daemon never starts). Broad
# NOPASSWD is the norm for this single-user dev container.
RUN printf '%s\n' 'claude ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/claude \
    && chmod 0440 /etc/sudoers.d/claude

# Tailscale 1.104.4 static binaries (statically linked; no systemd — startup.sh
# runs tailscaled directly). Pinned to the version nixpkgs ships here.
RUN curl -fsSL -o /tmp/ts.tgz \
        https://pkgs.tailscale.com/stable/tailscale_1.104.4_arm64.tgz \
    && tar -xzf /tmp/ts.tgz -C /tmp \
    && install -m755 /tmp/tailscale_1.104.4_arm64/tailscale  /usr/local/bin/tailscale \
    && install -m755 /tmp/tailscale_1.104.4_arm64/tailscaled /usr/local/bin/tailscaled \
    && rm -rf /tmp/ts.tgz /tmp/tailscale_1.104.4_arm64 \
    && tailscale version
