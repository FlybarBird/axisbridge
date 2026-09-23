# Slate 7 installer verification

Package: 0.1.0-slate7.7. Development checks completed 2026-09-23.

- 11 installer tests passed: production Slate 7 model/board identifiers, private atomic writes, checksums/tamper detection, unlisted payload rejection, port collision, wrong-platform rejection, fresh installation, rollback, and failed-upgrade restoration of the previous app/service/configuration.
- Full deployment tests used temporary filesystem paths and mocked procd/service calls; they did not modify a router.
- POSIX shell syntax checks passed for the installer, service, and management helper.
- Running the actual installer preflight on the development host correctly rejected the non-OpenWrt host before installation.
- Bundled application/router-launcher imports passed with Python 3.12.
- All 43 application tests passed, including 10 screen/hold/capture tests, whitespace-preserving credentials, UDP unicast/multicast, independent local-IP binding, simulated MA login/output, automatic PSN/MA startup, demo output, loss of input, and reconnection.
- Final archive contents and SHA-256 manifest were verified after extraction.

Previous revisions were installed on physical GL-BE3600 firmware 4.9.0, using the bundled Python 3.11.7, and verified across a reboot. The hardware screen revision adds tests for four-second holds, early release, sliding off, repeat suppression, selection changes, selected-only atomic capture, stale/disabled input rejection, and framebuffer rotation. Physical touch acceptance and real Raynok/grandMA2 calibration still require operator verification.

Revision slate7.6 was installed on that router. procd runs the bridge with `--screen`, the process owns `/dev/fb0` and `/dev/input/event0`, and the stock display process stopped. Framebuffer readback confirmed the expected PSN/MA status boxes and two calibration buttons. The live portal displayed all ten existing blocks on the new Slate screen selection page.

Revision slate7.7 corrects the upside-down physical orientation reported by the operator, rotating both the framebuffer and input coordinates 180 degrees. The 11 screen tests pass, including corner-pixel placement and touch hit-testing for both Low and High buttons.
