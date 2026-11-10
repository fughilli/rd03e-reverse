# System config baked into BOTH the base and full images. The Pi 3B+ uplinks via
# Ethernet by default (plug it in and it gets DHCP). To use WiFi instead, either
# uncomment below (passphrase lands in the image) or export $SBC_WIFI_SSID /
# $SBC_WIFI_PSK before building (kept out of git). See sbc-deploy nix/modules/wifi.nix.
{ ... }:
{
  # sbcDeploy.wifi.networks = [
  #   { ssid = "Your Wi-Fi"; psk = "hunter2"; priority = 100; }
  # ];
}
