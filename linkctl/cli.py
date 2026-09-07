"""Command line front end for the Insta360 Link controller."""

import argparse
import errno
import sys
import time

from . import ioctls as io
from .camera import (
    FEATURES,
    MODES,
    TRACK_FRAMES,
    UNSAFE_SELECTORS,
    CameraNotFound,
    LinkCamera,
    UnsafeWrite,
    find_cameras,
)


PARKED_NOTE = (
    "note: the gimbal is parked because nothing is streaming, so it will not move yet. "
    "Open a video app and re-issue the command."
)


def _warn_if_parked(cam):
    if cam.is_parked():
        print(PARKED_NOTE, file=sys.stderr)


def _fail(message, code=1):
    print(f"linkctl: {message}", file=sys.stderr)
    return code


# ---------- commands ----------

def cmd_list(args, _):
    cameras = find_cameras()
    if not cameras:
        return _fail("no Insta360 camera found")
    for cam in cameras:
        print(f"{cam['node']}  {cam['model']}  (usb {cam['vid']}:{cam['pid']})")
    return 0


def cmd_info(args, cam):
    print(f"Device      {cam.node}")
    print(f"Model       {cam.info.get('model')}  [{cam.info.get('vid')}:{cam.info.get('pid')}]")
    print(f"Card        {cam.info.get('card','').strip()}")
    print(f"Serial      {cam.serial_number()}")
    if cam.units:
        units = ", ".join(f"{uid} ({guid[:8]}…)" for guid, uid in sorted(cam.units.items(), key=lambda kv: kv[1]))
        print(f"Ext units   {units}")
    else:
        print("Ext units   none found -- proprietary features unavailable")

    print("\nPosition")
    print(f"  pan       {cam.get_pan():+7.1f}°   range {cam.pan_range[0]:+.0f}..{cam.pan_range[1]:+.0f}")
    print(f"  tilt      {cam.get_tilt():+7.1f}°   range {cam.tilt_range[0]:+.0f}..{cam.tilt_range[1]:+.0f}")
    print(f"  zoom      {cam.get_zoom():7.2f}x  range {cam.zoom_range[0]:.0f}..{cam.zoom_range[1]:.0f}")
    if cam.has_extension_unit:
        tilt, pan = cam.ptz_raw()
        print(f"  actual    pan {pan/3600:+.1f}°  tilt {tilt/3600:+.1f}°  (physical gimbal angle)")

    if cam.has_extension_unit:
        print("\nFeatures")
        print(f"  mode      {cam.get_mode()}")
        print(f"  framing   {cam.get_track_frame()}")
        print(f"  gestures  mask 0x{cam.get_gesture_mask():02x}")
        print(f"  noise-cx  {'on' if cam.get_noise_cancellation() else 'off'}")

    print("\nControls")
    for ctrl in cam.controls().values():
        try:
            value = cam.dev.get(ctrl.id)
        except OSError:
            value = "?"
        notes = []
        if ctrl.inactive:
            notes.append("inactive: an auto mode owns it")
        if not ctrl.writable:
            notes.append("read-only")
        note = ("   <- " + "; ".join(notes)) if notes else ""
        if ctrl.menu:
            label = ctrl.menu.get(value, "")
            print(f"  {ctrl.name:<32} {value:<8} {label}{note}")
        else:
            print(f"  {ctrl.name:<32} {value:<8} [{ctrl.min}..{ctrl.max} step {ctrl.step}]{note}")
    return 0


def cmd_status(args, cam):
    tilt, pan = cam.ptz_raw() if cam.has_extension_unit else (None, None)
    actual = f"  actual pan {pan/3600:+6.1f}° tilt {tilt/3600:+6.1f}°" if pan is not None else ""
    if cam.is_parked():
        actual += "  [parked]"
    mode = cam.get_mode() if cam.has_extension_unit else "n/a"
    print(
        f"pan {cam.get_pan():+6.1f}°  tilt {cam.get_tilt():+6.1f}°  "
        f"zoom {cam.get_zoom():.2f}x  mode {mode}{actual}"
    )
    return 0


def cmd_watch(args, cam):
    print("Live gimbal position -- Ctrl-C to stop")
    try:
        while True:
            tilt, pan = cam.ptz_raw()
            print(
                f"\r  set: pan {cam.get_pan():+7.1f}° tilt {cam.get_tilt():+7.1f}° "
                f"zoom {cam.get_zoom():.2f}x  |  actual: pan {pan/3600:+7.1f}° tilt {tilt/3600:+7.1f}°   ",
                end="",
                flush=True,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print()
    return 0


def cmd_pan(args, cam):
    if args.degrees is None:
        print(f"{cam.get_pan():+.1f}")
    else:
        print(f"{cam.set_pan(args.degrees):+.1f}")
        _warn_if_parked(cam)
    return 0


def cmd_tilt(args, cam):
    if args.degrees is None:
        print(f"{cam.get_tilt():+.1f}")
    else:
        print(f"{cam.set_tilt(args.degrees):+.1f}")
        _warn_if_parked(cam)
    return 0


def cmd_zoom(args, cam):
    if args.factor is None:
        print(f"{cam.get_zoom():.2f}")
    else:
        print(f"{cam.set_zoom(args.factor):.2f}")
    return 0


def cmd_move(args, cam):
    pan, tilt = cam.move(args.d_pan, args.d_tilt)
    print(f"pan {pan:+.1f}°  tilt {tilt:+.1f}°")
    _warn_if_parked(cam)
    return 0


def cmd_center(args, cam):
    cam.center()
    print("centred at 1x")
    _warn_if_parked(cam)
    return 0


def cmd_reset(args, cam):
    moved, before, after = cam.gimbal_reset()
    print(f"physical position {before[1]/3600:+.1f}/{before[0]/3600:+.1f} -> "
          f"{after[1]/3600:+.1f}/{after[0]/3600:+.1f} (pan/tilt)")
    if not moved:
        print("the firmware re-home trigger did nothing on this model; "
              "use `linkctl center` to recentre")
    return 0


def cmd_mode(args, cam):
    if args.name is None:
        print(cam.get_mode())
    else:
        print(cam.set_mode(args.name))
    return 0


def cmd_track(args, cam):
    if args.state is None:
        print("on" if cam.get_tracking() else "off")
    else:
        cam.set_tracking(args.state == "on")
        print(args.state)
    return 0


def cmd_frame(args, cam):
    if args.name is None:
        print(cam.get_track_frame())
    else:
        print(cam.set_track_frame(args.name))
    return 0


def cmd_feature(args, cam):
    cam.set_feature(args.name, args.state == "on")
    print(f"{args.name} -> {args.state}  (write-only; the camera reports no readback)")
    return 0


def cmd_gestures(args, cam):
    if args.state is None:
        print(f"mask 0x{cam.get_gesture_mask():02x}")
    else:
        cam.set_gestures(args.state == "on")
        print(args.state)
    return 0


def cmd_noise(args, cam):
    if args.state is None:
        print("on" if cam.get_noise_cancellation() else "off")
    else:
        cam.set_noise_cancellation(args.state == "on")
        print(args.state)
    return 0


def cmd_get(args, cam):
    print(cam.get_control(args.control))
    return 0


def cmd_set(args, cam):
    ctrl = cam.dev.control(cam.control_id(args.control))
    if ctrl is not None and ctrl.inactive:
        print(
            f"note: '{ctrl.name}' is currently inactive because an auto mode owns it; "
            "the write may be ignored.",
            file=sys.stderr,
        )
    print(cam.set_control(args.control, args.value))
    return 0


def cmd_preset(args, cam):
    if args.action == "list":
        presets = cam.list_presets()
        if not presets:
            print("no presets saved")
        for slot in sorted(presets, key=lambda s: (len(s), s)):
            p = presets[slot]
            print(f"  {slot}: pan {p['pan']:+7.1f}°  tilt {p['tilt']:+7.1f}°  zoom {p['zoom']:.2f}x   ({p.get('saved','')})")
        return 0
    if args.slot is None:
        return _fail(f"'preset {args.action}' needs a slot number")
    if args.action == "save":
        p = cam.save_preset(args.slot)
        print(f"saved slot {args.slot}: pan {p['pan']:+.1f}° tilt {p['tilt']:+.1f}° zoom {p['zoom']:.2f}x")
    elif args.action == "go":
        p = cam.recall_preset(args.slot)
        print(f"recalled slot {args.slot}: pan {p['pan']:+.1f}° tilt {p['tilt']:+.1f}° zoom {p['zoom']:.2f}x")
        _warn_if_parked(cam)
    elif args.action == "rm":
        print(f"deleted slot {args.slot}" if cam.delete_preset(args.slot) else f"slot {args.slot} was empty")
    return 0


def cmd_raw(args, cam):
    unit = args.unit if args.unit is not None else cam.xu_main
    if args.action == "dump":
        for uid in sorted(set(cam.units.values())):
            print(f"-- extension unit {uid} --")
            for sel in range(1, 33):
                try:
                    length = cam.dev.xu_len(uid, sel)
                    info = cam.dev.xu_info(uid, sel)
                except OSError:
                    continue
                caps = ("get" if info & io.UVC_INFO_SUPPORTS_GET else "") + \
                       ("/set" if info & io.UVC_INFO_SUPPORTS_SET else "")
                value = ""
                if info & io.UVC_INFO_SUPPORTS_GET and 0 < length <= 64:
                    try:
                        value = cam.dev.xu_get(uid, sel, length).hex()
                    except OSError as exc:
                        value = f"<errno {exc.errno}>"
                flag = "  UNSAFE" if uid == cam.xu_main and sel in UNSAFE_SELECTORS else ""
                print(f"  sel {sel:2d}  len {length:<5} {caps:<8} {value}{flag}")
        return 0

    if args.selector is None:
        return _fail("raw get/set needs a selector")
    if args.action == "get":
        print(cam.dev.xu_get(unit, args.selector).hex())
    else:
        payload = bytes.fromhex(args.data.replace(" ", ""))
        cam.xu_write(args.selector, payload, unit=unit, force=args.force)
        print(f"wrote {payload.hex()} to unit {unit} selector {args.selector}")
    return 0


def cmd_gui(args, cam):
    from .gui import run
    run(cam)
    return 0


# ---------- argument parsing ----------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="linkctl",
        description="Control an Insta360 Link / Link 2 webcam on Linux.",
        epilog="Angles are degrees, zoom is a magnification factor (1.0 = wide).",
    )
    parser.add_argument("-d", "--device", help="video node to use (default: autodetect)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list attached Insta360 cameras").set_defaults(func=cmd_list, no_camera=True)
    sub.add_parser("info", help="show everything about the camera").set_defaults(func=cmd_info)
    sub.add_parser("status", help="one-line position summary").set_defaults(func=cmd_status)

    p = sub.add_parser("watch", help="live position readout")
    p.add_argument("--interval", type=float, default=0.3)
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("pan", help="get or set pan angle")
    p.add_argument("degrees", nargs="?", type=float)
    p.set_defaults(func=cmd_pan)

    p = sub.add_parser("tilt", help="get or set tilt angle")
    p.add_argument("degrees", nargs="?", type=float)
    p.set_defaults(func=cmd_tilt)

    p = sub.add_parser("zoom", help="get or set zoom factor")
    p.add_argument("factor", nargs="?", type=float)
    p.set_defaults(func=cmd_zoom)

    p = sub.add_parser("move", help="nudge the gimbal by a relative amount")
    p.add_argument("d_pan", type=float)
    p.add_argument("d_tilt", type=float, nargs="?", default=0.0)
    p.set_defaults(func=cmd_move)

    sub.add_parser("center", help="pan 0, tilt 0, zoom 1x").set_defaults(func=cmd_center)
    sub.add_parser("reset", help="ask the firmware to re-home the gimbal").set_defaults(func=cmd_reset)

    p = sub.add_parser("mode", help="get or set the framing mode")
    p.add_argument("name", nargs="?", choices=sorted(MODES))
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("track", help="AI subject tracking on/off")
    p.add_argument("state", nargs="?", choices=["on", "off"])
    p.set_defaults(func=cmd_track)

    p = sub.add_parser("frame", help="how tightly tracking frames the subject")
    p.add_argument("name", nargs="?", choices=sorted(TRACK_FRAMES))
    p.set_defaults(func=cmd_frame)

    p = sub.add_parser("feature", help="toggle a firmware feature (no readback)")
    p.add_argument("name", choices=sorted(FEATURES))
    p.add_argument("state", choices=["on", "off"])
    p.set_defaults(func=cmd_feature)

    p = sub.add_parser("gestures", help="hand gesture control on/off")
    p.add_argument("state", nargs="?", choices=["on", "off"])
    p.set_defaults(func=cmd_gestures)

    p = sub.add_parser("noise", help="microphone noise cancellation on/off")
    p.add_argument("state", nargs="?", choices=["on", "off"])
    p.set_defaults(func=cmd_noise)

    p = sub.add_parser("get", help="read an image control")
    p.add_argument("control")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set", help="write an image control")
    p.add_argument("control")
    p.add_argument("value", type=int)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("preset", help="save and recall pan/tilt/zoom positions")
    p.add_argument("action", choices=["save", "go", "list", "rm"])
    p.add_argument("slot", nargs="?", type=int)
    p.set_defaults(func=cmd_preset)

    p = sub.add_parser("raw", help="talk to the extension units directly")
    p.add_argument("action", choices=["dump", "get", "set"])
    p.add_argument("selector", nargs="?", type=int)
    p.add_argument("data", nargs="?", default="", help="hex bytes for 'set'")
    p.add_argument("--unit", type=int, help="extension unit id (default: the main one)")
    p.add_argument("--force", action="store_true", help="permit writes to blocked selectors")
    p.set_defaults(func=cmd_raw)

    sub.add_parser("gui", help="open the graphical control panel").set_defaults(func=cmd_gui)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "no_camera", False):
        return args.func(args, None)
    try:
        cam = LinkCamera(args.device)
    except CameraNotFound as exc:
        return _fail(str(exc))
    except PermissionError:
        return _fail(
            f"permission denied opening {args.device or 'the camera'}. Add yourself to "
            "the 'video' group or install the bundled udev rule."
        )
    except ValueError as exc:
        return _fail(str(exc))
    except FileNotFoundError:
        return _fail(f"{args.device} does not exist. Run `linkctl list` to see what is attached.")
    except OSError as exc:
        return _fail(f"could not open {args.device or 'the camera'}: {exc}")
    try:
        return args.func(args, cam)
    except UnsafeWrite as exc:
        return _fail(f"refused: {exc}\nPass --force if you are certain.")
    except (ValueError, CameraNotFound) as exc:
        return _fail(str(exc))
    except OSError as exc:
        if exc.errno == errno.EINVAL:
            return _fail("the camera rejected that request (wrong length or unsupported)")
        return _fail(f"i/o error: {exc}")
    except KeyboardInterrupt:
        return 130
    finally:
        cam.close()


if __name__ == "__main__":
    sys.exit(main())
