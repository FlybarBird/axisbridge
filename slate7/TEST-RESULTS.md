# Slate 7 installer verification

Package: 0.1.1. Development checks completed 2026-09-23. This release promotes the slate7.10 implementation with updated version metadata and documentation.

- 11 installer tests passed: production Slate 7 model/board identifiers, private atomic writes, checksums/tamper detection, unlisted payload rejection, port collision, wrong-platform rejection, fresh installation, rollback, and failed-upgrade restoration of the previous app/service/configuration.
- Full deployment tests used temporary filesystem paths and mocked procd/service calls; they did not modify a router.
- POSIX shell syntax checks passed for the installer, service, and management helper.
- Running the actual installer preflight on the development host correctly rejected the non-OpenWrt host before installation.
- Bundled application/router-launcher imports passed with Python 3.12.
- All 59 application tests passed, including 22 screen/hold/capture tests, whitespace-preserving credentials, UDP unicast/multicast, independent local-IP binding, simulated MA login/output, automatic PSN/MA startup, demo output, loss of input, and reconnection. An initial slate7.8 run hit an ephemeral multicast test-port collision; the full rerun passed.
- Final archive contents and SHA-256 manifest were verified after extraction.

Previous revisions were installed on physical GL-BE3600 firmware 4.9.0, using the bundled Python 3.11.7, and verified across a reboot. The hardware screen revision adds tests for four-second holds, early release, sliding off, repeat suppression, selection changes, selected-only atomic capture, stale/disabled input rejection, and framebuffer rotation. Physical touch acceptance and real Raynok/grandMA2 calibration still require operator verification.

Revision slate7.6 was installed on that router. procd runs the bridge with `--screen`, the process owns `/dev/fb0` and `/dev/input/event0`, and the stock display process stopped. Framebuffer readback confirmed the expected PSN/MA status boxes and two calibration buttons. The live portal displayed all ten existing blocks on the new Slate screen selection page.

Revision slate7.7 corrects the upside-down physical orientation reported by the operator, rotating both the framebuffer and input coordinates 180 degrees. The 11 screen tests pass, including corner-pixel placement and touch hit-testing for both Low and High buttons.

Revision slate7.8 handles the stock CST816X driver's ABS_MT_TRACKING_ID contact/release events rather than its latched BTN_TOUCH key. This behavior was confirmed by inspecting the installed kernel module: contact sends ID 0 and BTN_TOUCH=1; release sends ID -1 followed by a delayed sync, without clearing BTN_TOUCH. Input tests cover early release, successive holds with unchanged coordinates, one-shot activation, both buttons, dropped-event cancellation, and the single-touch fallback. No kernel or controller firmware changes are required.

New tests cover automatic output after authenticated MA login and reconnect, rejected-login/stale-signal suppression, manual Hold staying held until the next login, live atomic Low/High updates with unrelated blocks unaffected, and protected screen-selection updates while live. Normal show/network editing remains held-only.

Revision slate7.8 was installed on the physical GL-BE3600. The three automatic connection/output settings were saved and the service restarted. It then restored PSN listening, authenticated to the configured real MA endpoint, and enabled output without portal interaction. The driver selected the tracking-ID decoder. All five selected POD blocks and the ten existing blocks' calibration were preserved. At verification no PSN packets had arrived after restart, and no fader commands were sent. This confirms automatic MA login/arming, not end-to-end tracking output or physical fader readback.

Subsequent live acceptance: PSN traffic arrived and 58 fader commands were sent to the authenticated MA connection. The physical High hold was detected at 13:14:04 and reached capture validation at 13:14:08, correctly rejecting equal low/high positions. An early-release Low hold was cancelled. A new Low hold at 13:14:18 successfully captured all five selected blocks at 13:14:22 while output remained enabled. Thus the hardware contact/release decoder, four-second timer, repeat holds, live selected-group capture, and real PSN-to-MA transport were observed working. Console fader readback is still not provided.

The operator subsequently reported that physical Low was detected as High and vice versa. Revision slate7.9 reverses only the horizontal touch coordinate; framebuffer orientation, button semantics, and saved calibration are unchanged. The input tests use the corrected left/right physical coordinate mapping. The previous acceptance confirms timing/capture behavior, but did not establish that labels matched the touch regions.

Revision slate7.10 changes both Update High and Update Low to skip selected blocks still at the opposite saved endpoint or already at the endpoint being captured. Six new tests cover partial capture and all-skipped no-op behavior for each button (including preservation of skipped and unselected blocks' output state), retained stale/disabled/revision guards, and a failed disk write leaving configuration/output unchanged. The hardware message reports actual saved/skipped counts instead of claiming every selected block was saved. The four-second contact rules are unchanged.

Revision slate7.10 was installed on the physical router after all 59 application and 11 installer tests passed. The saved show was verified unchanged by the installation, and the operator's active demo mode/levels were restored with MA authenticated and output enabled. Isolated checks against the installed Python 3.11 code produced `HIGH: 1 SAVED / 1 SKIPPED`, `LOW: 1 SAVED / 1 SKIPPED`, and an all-skipped no-op result. These checks used temporary test shows without altering the live calibration or sending test fader commands; physical button acceptance of this revision remains for the operator.
