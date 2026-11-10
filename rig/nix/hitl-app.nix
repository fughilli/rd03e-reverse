# rig-radar — Pi 3B+ HITL runner system config: Podman, Tailscale, and the
# hitl-reserve reservation daemon serving the radar bench (an ESP32-C6 + the
# RP2350 SWD probe on the Rd-03E's GD32E230, reserved together).
#
# A FUNCTION of the hitl-reserve module source (`hitlSrc`); flake.nix applies it.
# Trimmed from fughilli/splanc's pi/hitl/nix/hitl-app.nix: this bench has no LED
# players, so no provisioning AP / logic-analyzer / Grafana — just the reservation
# daemon + the podman reservation env carrying the debug toolbox (nix/container.nix).
{ hitlSrc }:
{ config, pkgs, lib, ... }:
let
  hitl = pkgs.callPackage ./packages.nix { src = hitlSrc; };
  image = pkgs.callPackage ./container.nix { inherit pkgs; };
  imageRef = "hitl-test:latest"; # matches container.nix name:tag
  catalogFile = ../reserve/catalog.json;

  apiPort = 8087; # daemon API (reached over the tailnet)
  sshPort = 2222; # first published container sshd port
  maxDuts = 4; # firewall range 2222.. (plenty for a one-bench rig)
  dutPorts = lib.genList (i: sshPort + i) maxDuts;
in
{
  # Podman runs the per-reservation environments (the hitl-test image).
  virtualisation.podman.enable = true;
  virtualisation.containers.enable = true;

  # Tailscale — reach the rig over the tailnet so a move/reflash can't strand it.
  # The auth key is seeded out of band to authKeyFile (never in git / the nix
  # store); tailscaled-autoconnect brings it up on a fresh state dir. --ssh lets
  # agents SSH to the rig with tailnet identity. Tailnet name = system hostname.
  services.tailscale = {
    enable = true;
    authKeyFile = "/var/lib/tailscale/authkey";
    extraUpFlags = [ "--ssh" "--hostname=${config.networking.hostName}" ];
  };

  # Resolve LAN *.local (handy while commissioning before the tailnet is up).
  services.avahi = {
    enable = true;
    nssmdns4 = true;
  };

  # Pin the tailscale device name to the system hostname on every boot (a no-op
  # once correct), so the name sticks across redeploys without a reflash.
  systemd.services.tailscale-hostname = {
    description = "Pin the tailscale device hostname to the system hostname";
    after = [ "tailscaled.service" "tailscaled-autoconnect.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    script = ''
      ts=${config.services.tailscale.package}/bin/tailscale
      for _ in $(seq 1 30); do "$ts" status >/dev/null 2>&1 && break; sleep 2; done
      "$ts" set --hostname=${config.networking.hostName} 2>/dev/null || true
    '';
  };

  # Load the reservation container image into podman at boot and on every deploy
  # (its store path changes with container.nix, so switch-to-configuration re-runs
  # this). Clear stale hitl containers + untag first so the tag moves to the new
  # image. Reservation containers are ephemeral (recreated per reservation).
  systemd.services.hitl-image-load = {
    description = "Load the HITL test container image into podman";
    wantedBy = [ "multi-user.target" ];
    before = [ "hitl-manager.service" ];
    after = [ "network.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "hitl-image-load" ''
        set -u
        pm=${pkgs.podman}/bin/podman
        ids=$($pm ps -aq --filter label=hitl=1 2>/dev/null || true)
        [ -n "$ids" ] && $pm rm -f $ids 2>/dev/null || true
        $pm untag ${imageRef} 2>/dev/null || true
        $pm load -i ${image}
      '';
    };
  };

  # Hardware watchdog: a wedged Pi 3 (USB-bus stall under load, kernel/PID1 hang)
  # self-heals instead of staying dead on the tailnet until a human power-cycles.
  systemd.watchdog.runtimeTime = "15s";

  # Pi 3B+ has a SINGLE shared USB2 bus: serialize flash/USB-serial access so two
  # DUT ops can't contend. The lock dir's presence in the container arms
  # hitl_client's flash serialization.
  systemd.tmpfiles.rules = [ "d /run/hitl-flash 0777 root root -" ];

  # The reservation daemon (hitl-reserved). Reads the baked catalog for its units
  # (the radar bench) + serves per-reservation podman envs from the hitl-test image.
  systemd.services.hitl-manager = {
    description = "HITL reservation manager (rig-radar)";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" "tailscaled.service" "hitl-image-load.service" ];
    wants = [ "network-online.target" "hitl-image-load.service" ];
    restartTriggers = [ "${hitl}/bin/hitl-reserved" "${catalogFile}" ];
    unitConfig.StartLimitIntervalSec = 0;
    path = [ pkgs.podman pkgs.iproute2 pkgs.openssh pkgs.getent ];
    serviceConfig = {
      ExecStart = lib.concatStringsSep " " [
        "${hitl}/bin/hitl-reserved"
        "--addr :${toString apiPort}"
        "--catalog ${catalogFile}"
        "--workspace rig-radar"
        "--host ${config.networking.hostName}"
        "--image ${imageRef}"
        "--podman ${pkgs.podman}/bin/podman"
        "--privileged=false"
        "--state-dir /var/lib/hitl"
        "--max-concurrent 1"
        # NB: this hitl-reserve build has no --ssh-port (only --darwin-ssh-port);
        # the podman runner publishes container sshd from the catalog's
        # ssh_port_base. sshPort/dutPorts below still drive the firewall range.
        "--broker-url http://host.containers.internal:${toString apiPort}"
        # Serialize flash/USB-serial on the Pi 3's single USB bus.
        "--mount /run/hitl-flash:/run/hitl/flash-lock"
      ];
      StateDirectory = "hitl";
      Restart = "on-failure";
      RestartSec = 3;
    };
  };

  # Reach the daemon API + the published container sshd ports over the tailnet
  # (and the LAN while commissioning).
  networking.firewall.trustedInterfaces = [ "tailscale0" ];
  networking.firewall.allowedTCPPorts = [ apiPort ] ++ dutPorts;
}
