# AxisBridge — Slate 7 installer

**Target:** GL.iNet Slate 7 **GL-BE3600**, running GL.iNet/OpenWrt firmware with `procd` and a firmware-compatible **Python 3.10+** package.

Package version: **0.1.0-slate7.7**. Includes the complete AxisBridge 0.1.0 bridge and web portal. No separate app download is required.

This is a shell installer archive for SSH installation. It is **not router firmware** and must **not** be uploaded through the router's Firmware Upgrade page. An `.ipk` is not supplied because the installed firmware/package format and dependency versions have not been provided. The installer detects `opkg` or `apk` and uses only the router's existing package feeds.

## Installation

### Optional hardware home screen

The built-in 284 × 76 touchscreen can show PSN/MA status and large **Update Low** / **Update High** buttons. Enable it after installation:

```sh
uci set axisbridge.main.screen_enabled='1'
uci commit axisbridge
axisbridge-ctl restart
```

In the portal, open **Slate screen**, choose the blocks to include, and save. No blocks are selected automatically. The chosen names and positions cycle along the bottom of the hardware screen. Both buttons apply to the entire selected group. A continuous **four-second hold** shows a countdown and progress bar; release or slide off to cancel. A completed hold applies once, with a new release required before another capture. Low maps to 0%, High to 100%.

Output must be held. Every selected block must be enabled with a fresh axis sample, and low/high must differ. If any selected block fails these checks, nothing is changed. Selection changes during a hold cancel it. Status boxes distinguish PSN LIVE (fresh traffic), WAIT (listening without fresh data), DEMO, and OFF; MA LIVE means authenticated.

The screen uses the firmware's existing framebuffer/touch drivers. The original GL.iNet screen is restored when AxisBridge stops, including a normal uninstall. To keep the stock screen, set `screen_enabled='0'`, commit, and restart AxisBridge. Display geometry follows [GL.iNet's gl-lvgl implementation](https://github.com/gl-inet/gl-lvgl/blob/main/patches/03-fix-gl-lcd-init.patch), with the home screen and touch coordinates rotated together for the router's upright viewing orientation.

### Install the bridge

Keep the router connected to the internet for the first install if it needs Python. Keep it off the show networks until its network configuration is complete.

1. Download `AxisBridge-Slate7-Installer-v0.1.0.tar.gz` to your computer.
2. Upload it to `/tmp/` on the router using an SFTP/SCP client such as WinSCP, or use this command with your router's IP:

```sh
scp AxisBridge-Slate7-Installer-v0.1.0.tar.gz root@192.168.8.1:/tmp/
```

If your router does not provide an SFTP subsystem and your OpenSSH client supports it, use `scp -O` for legacy SCP. `192.168.8.1` is an example; use your current router address.

3. SSH to the router and extract:

```sh
ssh root@192.168.8.1
cd /tmp
tar -xzf AxisBridge-Slate7-Installer-v0.1.0.tar.gz
cd AxisBridge-Slate7-Installer
sh install.sh --check
```

`--check` verifies the model, firmware environment, archive, available storage, and runtime. It makes no configuration changes. Exit code 2 means Python must be installed before the remaining checks can complete; the normal install attempts that automatically.

4. Install:

```sh
sh install.sh
```

This checks the supplied SHA-256 manifest, installs `python3` if required, deploys the app, enables its OpenWrt service, starts the portal, and checks the portal responds. It does not update firmware or replace package feeds. Package-feed or version errors stop installation with an explanation. Dependency downloads require internet, but normal app operation does not.

If port 8080 is already occupied, use:

```sh
sh install.sh --port=8081
```

To restrict the portal to a specific **already assigned local address**:

```sh
sh install.sh --bind=192.168.8.1
```

If Python is already installed and this router is offline:

```sh
sh install.sh --offline
```

5. Open **http://192.168.8.1:8080** in a browser on the router's LAN/Wi-Fi, substituting your router IP and chosen port. Display its login key with:

```sh
axisbridge-ctl key
```

The key is stored at `/etc/axisbridge/portal-key.txt`, readable by root. The service does not print it to syslog.

## What the installer changes

| Location | Purpose |
|---|---|
| `/usr/share/axisbridge/` | Application and web portal |
| `/etc/init.d/axisbridge` | OpenWrt `procd` service |
| `/usr/bin/axisbridge-ctl` | Status/start/stop/key/uninstall helper |
| `/etc/config/axisbridge` | Portal bind address, port, enabled setting |
| `/etc/axisbridge/` | Saved show, owner-only MA password, access key, installation receipt |

It does **not** change Ethernet, Wi-Fi, DHCP, NAT, firewall rules, or GL.iNet's administration interface. Normal LAN access usually permits the portal; a custom/guest network may require an explicit input rule. The default portal bind is `0.0.0.0`, so access is controlled by the router's existing firewall. Do not add a public-WAN port-forward for it.

On upgrades, the current show, portal key, and service configuration remain. Existing service running/enabled state is retained. App/service files are restored automatically if deployment or portal health checking fails. Newly installed Python packages remain installed. Stop or hold fader output before upgrading because an upgrade stops/restarts the bridge.

## Configure the two show ports

The physical labels WAN/LAN do not set the AxisBridge roles. Configure two independent IP interfaces in GL.iNet's advanced OpenWrt/LuCI settings, then select their **local IPv4 addresses** in AxisBridge.

Proposed topology (addresses are examples):

| Physical connection | Role | Router local address | Peer |
|---|---|---|---|
| WAN Ethernet | PSN / Raynok | `10.10.10.20/24` | Raynok `10.10.10.10` |
| LAN Ethernet | MA control | `192.168.0.20/24` | grandMA2 `192.168.0.1` |
| Wi-Fi management SSID | Portal / administration | `192.168.8.1/24` | Phone, tablet, computer |

**The LAN Ethernet port and main Wi-Fi are commonly in the same bridge by default.** To use the topology above, separate the Ethernet port from that bridge while preserving Wi-Fi management access. Check the actual device/interface names on your firmware; do not assume `eth0` or `eth1`. Back up the router configuration before making network changes.

Configure:

- Static addresses on both show-facing interfaces, on distinct subnets.
- No DHCP server on either show-facing Ethernet network. DHCP can remain enabled on the Wi-Fi management network.
- No forwarding/NAT between the PSN, MA, and management zones unless separately required by your network design. AxisBridge itself terminates PSN locally and originates the MA TCP connection.
- An **input-to-router** firewall allowance on the PSN zone for the configured UDP port, normally **56565**, preferably restricted to Raynok's source IP. Multicast must reach the selected interface; some switch/network configurations may also need IGMP allowances.
- Router-originated TCP access to grandMA2 port **30000** on the MA side.
- Management clients permitted to reach the router's portal TCP port **8080** (or your chosen port).
- Exclude these local show connections from VPN policy routing, kill switches, WAN failover policies, and automatic network switching that would move or block the traffic.

These are configuration requirements, not instructions to blindly apply a generic firewall script. The installer leaves current router connectivity intact because your firmware version and existing network assignments are unknown. Both Ethernet ports are used in this layout; neither remains a dedicated wired internet uplink.

## Configure AxisBridge

1. In **Network**, enter the PSN-side local IPv4, receive mode, port, group, and optional Raynok source filter.
2. Enter the MA-side local IPv4, console IP, TCP port 30000, and MA username. Adapter IPs can be entered manually; `psutil` is optional and not required by this installer.
3. On grandMA2 enable **Setup → Console → Global Settings → Telnet → Login Enabled**.
4. Save network settings, start PSN, and connect MA using a user with playback rights. Leave the two auto-connect checkboxes enabled to restore both connections after a service restart.
5. Add control blocks, choose an entity/axis, assign `page.executor` targets, and capture bottom/top.
6. Arm output when ready.

The program controls grandMA2 through Telnet over the selected adapter. It does not join an MA-Net2 session. Monitor values labeled **Last sent** are commands sent, not console fader readback.

The service starts automatically after boot and restarts after a crash, with a limited retry policy. With auto-connect enabled, it starts PSN and reconnects MA from the saved settings; unavailable adapters are retried automatically. The MA password is kept separately at `/etc/axisbridge/ma-password.txt` with owner-only permissions and is not included in shows or exports. **Every new process still starts with output held.** An operator must arm output after every restart, so booting the router cannot move a fader unattended.

## Manage the service

```sh
axisbridge-ctl status
axisbridge-ctl logs
axisbridge-ctl restart
axisbridge-ctl stop
axisbridge-ctl start
axisbridge-ctl key
```

To disable startup:

```sh
axisbridge-ctl stop
axisbridge-ctl disable
```

To change the portal port after installation:

```sh
uci set axisbridge.main.port='8081'
uci commit axisbridge
axisbridge-ctl restart
```

Likewise `axisbridge.main.bind` selects the portal listening IPv4. Existing service settings are preserved when reinstalling; install command-line flags only supply initial defaults. If binding to a specific IP, that address must be present when the service starts.

To uninstall:

```sh
axisbridge-ctl uninstall
```

This stops/disables the service and removes owned application/service files. Saved shows, the key, service settings, Python, and network configuration remain. Export your show from the portal before router firmware upgrades; preservation of custom files/packages depends on the firmware upgrade procedure. Reinstall this package afterwards if needed.

## Checks and limitations

The installer is designed for BusyBox/POSIX shell and `procd`. It checks available storage: at least 12 MiB for app staging/rollback, or 64 MiB before attempting a Python install. These are conservative preflight thresholds; the package manager still determines actual dependency sizes and availability.

Development verification includes shell syntax, payload integrity, rollback tests, the procd command definition, staged app imports, and all 31 AxisBridge protocol/backend tests. It has **not been installed on a physical Slate 7**; exact firmware package availability, interface names, and hardware performance must be confirmed on the device. Start with `--check` and retain its output if it reports an issue.

Sources:

- [GL.iNet package management](https://docs.gl-inet.com/router/en/4/interface_guide/plugins/)
- [GL.iNet advanced settings](https://docs.gl-inet.com/router/en/4/interface_guide/advanced_settings/)
- [OpenWrt procd implementation](https://github.com/openwrt/openwrt/blob/master/package/system/procd/files/procd.sh)
- [OpenWrt Python package definitions](https://github.com/openwrt/packages/blob/openwrt-23.05/lang/python/python3/Makefile)
