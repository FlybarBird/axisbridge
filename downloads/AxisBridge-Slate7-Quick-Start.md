# AxisBridge on Slate 7 — quick start

For **GL.iNet Slate 7 GL-BE3600**. This is an SSH installer, **not a firmware image**.

1. Upload `AxisBridge-Slate7-Installer-v0.1.0.tar.gz` to the router's `/tmp` directory.
2. SSH to the router as root. Run:

```sh
cd /tmp
tar -xzf AxisBridge-Slate7-Installer-v0.1.0.tar.gz
cd AxisBridge-Slate7-Installer
sh install.sh --check
sh install.sh
```

A preflight exit code of 2 means Python needs installing; normal installation attempts that from the router's configured package feeds. Internet is required if dependencies are missing. A model/runtime/feed error needs resolving before proceeding.

3. Open `http://YOUR-ROUTER-IP:8080`. Display the portal access key with:

```sh
axisbridge-ctl key
```

If 8080 is occupied, install with `sh install.sh --port=8081`.

The package includes the full bridge, automatic service startup, persistent show storage, saved auto-connect settings, and upgrade rollback. With auto-connect enabled, PSN and MA reconnect after a restart, but output always starts **held**. Arm through the portal only after checking both status boxes.

**Configure networking before connecting show equipment.** The installer preserves the current router network configuration. The included `SETUP.md` explains assigning separate Ethernet interfaces to PSN and MA, maintaining Wi-Fi management access, and configuring DHCP/firewall rules. Its `APP-GUIDE.md` covers control blocks and calibration.

Manage it with `axisbridge-ctl status`, `axisbridge-ctl logs`, or `axisbridge-ctl restart`. Remove it with `axisbridge-ctl uninstall`; saved shows and keys remain.

Verified with 10 installer tests and all 31 app tests in development. Physical Slate 7/firmware and Raynok/grandMA2 testing remains outstanding.
