Adds Link Control, a utility for Insta360 Link and Link 2 webcams.

Insta360 ships no Linux software for these cameras. This app moves the motorised
gimbal, sets zoom and focus, adjusts image settings, and switches the camera's
built-in framing modes. Standard controls go through V4L2; the proprietary
features go through the camera's UVC extension units.

- Upstream: https://github.com/satishlokkoju/insta360_link_control
- License: MIT
- Tested on Insta360 Link 2 (USB 2e1a:4c04), Ubuntu 24.04, kernel 6.8

### On `--device=all`

The app controls the camera with `ioctl` calls on `/dev/video*`, including
`UVCIOC_CTRL_QUERY` for the extension units that drive the gimbal and the AI
framing modes. Flatpak has no finer-grained permission for video devices, so
`--device=all` is the only way to reach them; the app is useless without it.
`org.thonny.Thonny` uses the same permission for serial and USB devices.

I verified the requirement directly: inside a sandbox with `--device=all` the
extension-unit ioctl succeeds and the camera reports its unit GUIDs; without it,
`/dev/video0` is not present and the app finds no camera.

### On the runtime version

The manifest pins `24.08` rather than the newest runtime, and says why in a
comment. The `tkinter-standalone` module vendors a copy of `_tkinter.c` that
calls `Py_GetProgramName`, removed in Python 3.14, so building against `26.08`
fails to compile. `24.08` ships Python 3.12. I will move this forward once
`tkinter-standalone` supports 3.14.

### Naming

The application is called "Link Control" and ships an original icon. The Insta360
name appears only in the summary and description to identify compatible hardware,
and the description states that the project is unaffiliated with and not endorsed
by Arashi Vision Inc.
