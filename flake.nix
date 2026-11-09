{
  description = "Rd-03E (GD32E230 host MCU / S3KM111L radar SoC) firmware RE harness — Ghidra (via rules_reverse_engineering) + ARM Cortex-M binutils";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-24.11";
  # The generic Ghidra harness + scanners; Ghidra itself is exposed there as
  # `analyzeHeadless`. This repo adds only the Rd-03E / ARM specifics.
  inputs.rules_re.url = "github:fughilli/rules_reverse_engineering";
  inputs.rules_re.inputs.nixpkgs.follows = "nixpkgs";

  outputs = { self, nixpkgs, rules_re }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
      pkgsFor = system: import nixpkgs { inherit system; };

      # GNU binutils for the ARM Cortex-M target: `arm-none-eabi-objdump`/`nm`/
      # `readelf`, which disassemble the GD32E230K8 firmware (Cortex-M23, ARMv8-M
      # Baseline / Thumb). The host objdump cannot. Taken from nixpkgs' cross
      # stdenv — just the binutils, no full gcc — and substituted from the binary
      # cache, so no vendor-tarball pin is needed (unlike the rv32 case).
      armBinutils = system: (pkgsFor system).pkgsCross.arm-embedded.buildPackages.binutils;
    in
    {
      packages = forAllSystems (system: {
        armBinutils = armBinutils system;
      });

      # Re-export the generic headless analyzer so `nix run .#analyzeHeadless`
      # (which the Bazel harness here invokes) resolves against this flake.
      apps = forAllSystems (system: {
        default = rules_re.apps.${system}.analyzeHeadless;
        analyzeHeadless = rules_re.apps.${system}.analyzeHeadless;
      });

      devShells = forAllSystems (system:
        let pkgs = pkgsFor system; in
        {
          # `nix develop` — Ghidra + `arm-none-eabi-objdump`/`nm`/`readelf` on
          # PATH. The objdump scanners in rules_reverse_engineering shell out to
          # `$OBJDUMP`; set `OBJDUMP=arm-none-eabi-objdump` for the Rd-03E blobs.
          default = pkgs.mkShell {
            packages = [ pkgs.ghidra (armBinutils system) ];
          };
        });
    };
}
