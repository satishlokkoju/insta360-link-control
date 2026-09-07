# linkctl — Insta360 Link / Link 2 control for Linux

Insta360 ships no Linux version of its Link Controller app. This is a CLI and a GUI
that drive the camera directly through V4L2 and the camera's own UVC extension units.

Pure Python standard library. No pip install, no compile step, no root.

```
./linkctl-run gui        # graphical control panel
./linkctl-run info       # everything the camera reports
./linkctl-run pan 30     # point 30° to the right
```

Verified against an **Insta360 Link 2** (`2e1a:4c04`) on Ubuntu, kernel 6.8, using
the stock `uvcvideo` driver.

---

## Just want to move the camera right now?

Pan, tilt, zoom and the image controls are plain UVC — no special software needed:

```bash
sudo apt install v4l-utils
v4l2-ctl -d /dev/video0 --list-ctrls                    # see everything
v4l2-ctl -d /dev/video0 --set-ctrl=pan_absolute=108000  # arcseconds: 3600 = 1°
v4l2-ctl -d /dev/video0 --set-ctrl=tilt_absolute=-36000
v4l2-ctl -d /dev/video0 --set-ctrl=zoom_absolute=200    # 200 = 2x
```

`linkctl` exists for the rest: degrees instead of arcseconds, presets, a GUI, and the
proprietary AI/framing modes that `v4l2-ctl` cannot reach.

---

## Install

### From Flathub

Once the app is published (submission in progress):

```bash
flatpak install flathub io.github.satishlokkoju.insta360_link_control
```

It then appears in your application menu as **Link Control**.

### From this repository

Nothing needs building. To get `linkctl` on your PATH:

```bash
git clone https://github.com/satishlokkoju/insta360_link_control.git
cd insta360_link_control
./install.sh          # symlinks ~/.local/bin/linkctl
```

Or install it as a Python package, which also provides the `link-control-gui`
desktop command:

```bash
pip install --user .
```

### Building the Flatpak yourself

```bash
flatpak install --user flathub org.flatpak.Builder
flatpak run --env=FLATPAK_USER_DIR=$HOME/.local/share/flatpak \
  --command=flatpak-builder org.flatpak.Builder \
  --force-clean --user --install --install-deps-from=flathub --repo=repo \
  build-dir packaging/io.github.satishlokkoju.insta360_link_control.yaml
```

The `FLATPAK_USER_DIR` override is needed because `org.flatpak.Builder` otherwise
resolves runtimes against the system installation, which may not have them.

### Permissions

Desktop sessions already grant your user an ACL on `/dev/video*`. On a headless box or
over SSH, install the bundled udev rule (see the top of `99-insta360-link.rules`).

The live preview in the GUI needs `ffmpeg`; the Flatpak already includes it.

## Command line

```
linkctl list                     attached Insta360 cameras
linkctl info                     device, position, features, every control
linkctl status                   one-line summary
linkctl watch                    live position readout

linkctl pan [DEGREES]            get or set pan       (-145..+145)
linkctl tilt [DEGREES]           get or set tilt      (-90..+100)
linkctl zoom [FACTOR]            get or set zoom      (1.0..4.0)
linkctl move DPAN [DTILT]        nudge by a relative amount
linkctl center                   pan 0, tilt 0, zoom 1x

linkctl mode [normal|track|whiteboard|overhead|deskview]
linkctl track [on|off]           AI subject tracking
linkctl frame [head|upper|full]  how tightly tracking frames you
linkctl gestures [on|off]        hand gesture control
linkctl noise [on|off]           microphone noise cancellation
linkctl feature NAME on|off      firmware feature multiplexer

linkctl get CONTROL              e.g. brightness, contrast, focus
linkctl set CONTROL VALUE

linkctl preset save|go|rm SLOT   pan/tilt/zoom presets, stored on disk
linkctl preset list

linkctl raw dump                 every extension-unit selector, with lengths
linkctl raw get SEL
linkctl raw set SEL HEX [--force]
```

Angles are degrees everywhere in this tool. The camera itself works in arcseconds;
`linkctl` converts.

### Examples

```bash
linkctl move -10 5              # 10° left, 5° up
linkctl preset save 1           # remember this framing
linkctl preset go 1             # snap back to it
linkctl track on                # follow me
linkctl frame upper             # ...framing head and shoulders
linkctl set brightness 65
```

---

## GUI

```bash
linkctl gui
```

Live preview, a gimbal pad with press-and-hold repeat, zoom, six presets, the framing
modes, and every image control. Sliders whose value is currently owned by an auto mode
(focus under autofocus, white balance under AWB) are greyed out rather than silently
ignoring you.

The preview is off by default and opt-in: a UVC camera streams to one application at a
time, so leave it off before joining a call.

---

## What actually works on the Link 2

Everything below was tested on real hardware by moving the camera and comparing captured
frames, not by assuming the write succeeded.

| Feature | Status |
| --- | --- |
| Pan / tilt / zoom / focus | Works. Standard V4L2 controls. |
| Brightness, contrast, saturation, hue, sharpness, white balance, power-line frequency | Works. |
| Presets | Works. Stored in `~/.config/insta360-link/presets.json`. |
| Framing modes (normal / track / whiteboard / overhead / deskview) | Works — visibly changes framing and gimbal position. |
| Tracking framing (head / upper / full) | Works, and reads back correctly. |
| Gesture enable, noise cancellation | Works, and reads back correctly. See the gesture caveat below. |
| `feature flip` | Works — mirrors the image horizontally. |
| Mode **readback** | Unreliable. See below. |
| Firmware gimbal re-home (selector 14) | **No effect on the Link 2.** Use `linkctl center`. |
| Other `feature` toggles (hdr, portrait, ai-zoom, …) | Write-only; the camera reports no state, and only `flip` was confirmed visually. |

### Two behaviours worth knowing

**The gimbal only moves while something is streaming.** With no application capturing
video the camera stows the lens at about −85° tilt. Pan/tilt writes made while it is parked
are accepted by the driver and change the reported setpoint, but the camera ignores them —
and when it next wakes it returns to the position it held the last time it was streaming,
*not* to the setpoint you asked for. Re-issue the command once video is flowing and the
gimbal follows exactly.

`linkctl` tells you when this is happening: `status` and `info` print the physical angle
alongside the setpoint and mark the camera `[parked]`, and any movement command prints a
reminder. In the GUI, turning the preview on is enough to wake the gimbal.

**Turning gestures off clears their bindings.** Writing a zero gesture mask also blanks
the firmware's gesture binding table (extension-unit selector 6), and that table cannot be
written back — the firmware accepts the write and ignores it. If you use hand gestures and
they stop responding after toggling them off, unplug and replug the camera to restore its
defaults. The firmware also clamps the mask to the groups the model supports, so this
camera reports `0x0e` even when asked for all five groups.

**Mode readback is not an echo.** Selector 2 is a 61-byte firmware-owned record, not a
mode register. Once any mode has been set the firmware writes `0xFF` into byte 0, which
says "a mode is active" without saying which. `linkctl` therefore reports the mode this
process last set. For the same reason mode changes are written read-modify-write, so a
write patches two bytes instead of blanking the other 59.

---

## Safety

`linkctl` refuses to write to extension-unit selectors that can take the camera away from
you or overwrite its identity — most importantly selector 17, which switches the USB
personality and stops the device being a webcam until you unplug it, and selector 12,
which holds the serial number. Bulk configuration blobs are blocked too.

Writes to extension units 10 and 11 are blocked outright, because their selectors are not
mapped and several are write-only with unknown effects.

`linkctl raw set --force` overrides both guards. Read `UNSAFE_SELECTORS` in
`linkctl/camera.py` before you do. Reads are never blocked — `linkctl raw dump` is safe.

---

## How it works

| File | Role |
| --- | --- |
| `linkctl/ioctls.py` | ioctl numbers and struct layouts from `videodev2.h` / `uvcvideo.h` |
| `linkctl/v4l2.py` | control enumeration and get/set; raw `UVCIOC_CTRL_QUERY` |
| `linkctl/camera.py` | device discovery, degrees, modes, presets, write safety |
| `linkctl/cli.py` | the command line |
| `linkctl/gui.py` | the Tk panel; preview decodes ffmpeg's PPM output straight into Tk |

The camera exposes three UVC extension units. `linkctl` finds them by GUID from the USB
descriptor rather than trusting a hard-coded unit ID, and asks the device for each
selector's length with `GET_LEN` rather than trusting a documented size — the Link 2
differs from the original Link on several.

```
9  faf1672d-b71b-4793-8c91-7b1c9b7f95f8   modes, gimbal, gestures, features
10 e307e649-4618-a3ff-82fc-2d8b5f216773   tracking metadata
11 a8bd5df2-1a98-474e-8dd0-d92672d194fa   autoframe, zoom presets
```

`linkctl raw dump` prints every selector with its length, access bits and current value —
the starting point for anything not yet mapped.

## Credits

This is an independent implementation — no code from another project was copied into it.
It does build on published protocol research by several people who worked the Insta360
Link control interface out first, and it contributes three Link 2 corrections back.

- [schlarpc/insta360-link-firmware-re](https://github.com/schlarpc/insta360-link-firmware-re) — firmware teardown and the extension-unit selector map
- [vrwallace/Insta360-Link-1-and-2-Controller-for-Linux](https://github.com/vrwallace/Insta360-Link-1-and-2-Controller-for-Linux) — mode byte values, tested on Link 2
- [EdenCoder/insta360-linux](https://github.com/EdenCoder/insta360-linux) — TypeScript implementation
- [jfwoods/insta360link-controller](https://github.com/jfwoods/insta360link-controller) — daemon and companion hardware
- [Daniel15/WebCamControl](https://github.com/Daniel15/WebCamControl) — general webcam PTZ tool (MIT)

Full attribution, including why this was written from scratch rather than ported, is in
[CREDITS.md](CREDITS.md).

## License

MIT — see [LICENSE](LICENSE).

Insta360 and Insta360 Link are trademarks of Arashi Vision Inc. This project is not
affiliated with, authorised by, or endorsed by Arashi Vision Inc., and uses those names
only to describe the hardware it is compatible with.
