{
  # rig-radar — Rd-03E HITL runner: Raspberry Pi 3B+ image + live-deploy, an
  # sbc-deploy consumer. The reservation daemon + CLI are the hitl-reserve module
  # (github.com/fughilli/hitl-reserve), built from the `hitl-reserve` input. The
  # ESP32-C6 debug toolbox is reused from fughilli/splanc's pi/hitl rig
  # (nix/container.nix, openocd-esp32.nix, riscv-gdb.nix); the GD32E230 SWD toolbox
  # (pyOCD + CMSIS pack over the RP2350 CMSIS-DAP probe) is added here.
  #
  # Built via `//rig:rig-radar.*` (image_sd / deploy_live / ssh / keys /
  # seed_tailscale). The CLI is `packages.<system>.hitl` (`nix run …#hitl`).
  description = "rig-radar — Rd-03E HITL runner (sbc-deploy consumer)";

  inputs = {
    # Pinned in lockstep with the @sbc_deploy git_override in MODULE.bazel. These
    # are the revs fughilli/splanc's rig fleet builds with (known-good together).
    sbc-deploy.url = "github:fughilli/sbc-deploy/23c03bdcefa0cd4b90ea1ec611456984d91a389a?dir=nix";
    nixpkgs.follows = "sbc-deploy/nixpkgs";

    # The generalized reservation system (plain Go source; buildGoModule consumes
    # it in nix/packages.nix). Not a flake.
    hitl-reserve = {
      url = "github:fughilli/hitl-reserve/d5316982b10668b73affed44a2c8f155f751d081";
      flake = false;
    };
  };

  outputs = { self, nixpkgs, sbc-deploy, hitl-reserve, ... }:
    let
      project = sbc-deploy.lib.mkSbcProject {
        hostName = "rig-radar";
        # Overridden from Bazel via $SBC_BOARD (the sbc_application `board` attr =
        # @sbc_deploy//deploy/boards:raspberry-pi-3); set here for direct-nix use.
        board = "raspberry-pi-3";
        appModules = [ (import ./nix/hitl-app.nix { hitlSrc = hitl-reserve; }) ];
        systemModules = [ ./nix/network.nix ];
      };
      systems = [ "aarch64-linux" "x86_64-linux" "aarch64-darwin" "x86_64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems f;
    in
    project // {
      # The hitl CLI for agents (claude-container): `nix run 'path:rig#hitl' -- reserve`.
      packages = forAll (system:
        let hitl = nixpkgs.legacyPackages.${system}.callPackage ./nix/packages.nix { src = hitl-reserve; };
        in { inherit hitl; default = hitl; });
    };
}
