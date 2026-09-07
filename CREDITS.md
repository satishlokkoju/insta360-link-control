# Credits and attribution

This project is an independent, clean-room Python implementation. No source code from
any other project was copied into it. What it *does* build on is the published research
of several people who worked out the Insta360 Link control protocol first — the
extension-unit selector numbers, the mode byte values, and the shape of the requests.

Protocol details such as USB GUIDs, control selector numbers and interface constants are
factual descriptions of a hardware interface rather than creative expression, and they
are used here as facts. Several of the projects below carry **no license file at all**,
which means their code is all-rights-reserved and cannot be copied or relicensed; that
is precisely why this implementation was written from scratch rather than ported.

If you are one of the authors below and want your attribution changed or removed, please
open an issue and it will be handled promptly.

## Prior work this project relies on

### [schlarpc/insta360-link-firmware-re](https://github.com/schlarpc/insta360-link-firmware-re)
No license declared. A teardown of the Link firmware that documented the three extension
unit GUIDs, the selector map for units 9/10/11, the internal parameter IDs behind the
feature multiplexer, and the request structure. This is the most complete public account
of the protocol and was the primary reference for what the selectors mean.

### [vrwallace/Insta360-Link-1-and-2-Controller-for-Linux](https://github.com/vrwallace/Insta360-Link-1-and-2-Controller-for-Linux)
No license declared. A Free Pascal controller, and the first of these projects tested
against Link 2 hardware. Source of the mode/flag byte pairs used for AI tracking,
Whiteboard, Overhead and DeskView, and of the tracking-framing values.

### [EdenCoder/insta360-linux](https://github.com/EdenCoder/insta360-linux)
A TypeScript CLI and TUI. Its named constants for the mode, framing and target selectors
confirmed the values above, and its "pad the payload to the selector's real length"
approach matches what the hardware requires.

### [jfwoods/insta360link-controller](https://github.com/jfwoods/insta360link-controller)
No license declared. A daemon, CLI and companion hardware project for the Link.

### [Daniel15/WebCamControl](https://github.com/Daniel15/WebCamControl) (MIT)
A general Linux webcam PTZ tool; its issue #47 tracks Insta360 extension-unit support.

## Corrections this project contributes back

Testing on Link 2 hardware showed three places where the published Link 1 information
does not carry over. These are documented in the README and offered back to the projects
above:

- **Selector 2 is not a simple mode register on the Link 2.** It is a 61-byte
  firmware-owned record. A zero-padded write blanks neighbouring fields, so writes must
  be read-modify-write. After any mode is set the firmware puts `0xFF` in byte 0, so the
  readback reports "a mode is active" without saying which.
- **Selector 14 (gimbal re-home) has no effect on the Link 2**, measured both parked and
  while streaming.
- **Selector 6 (gesture bindings) is firmware-owned.** Disabling gestures clears it, and
  writes to restore it are accepted but ignored.

Every selector length in this implementation is read from the device with `GET_LEN`
rather than taken from documentation, because the Link 2 differs from the Link 1 on
several of them.

## Trademark

Insta360 and Insta360 Link are trademarks of Arashi Vision Inc. This project is not
affiliated with, authorised by, or endorsed by Arashi Vision Inc. The trademarks are
used only to describe the hardware this software is compatible with.
