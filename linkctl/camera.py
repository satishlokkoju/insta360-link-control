"""High-level control of an Insta360 Link / Link 2 webcam.

Standard pan/tilt/zoom/focus/image settings go through ordinary V4L2 controls.
The proprietary features (AI tracking, DeskView, gimbal reset, ...) live in the
camera's UVC extension units and are reached with UVCIOC_CTRL_QUERY.
"""

import glob
import json
import os
import struct
import time

from . import ioctls as io
from .v4l2 import V4L2Device

INSTA360_VID = "2e1a"

# Product IDs seen in the wild. Unknown Insta360 PIDs still work if the
# extension-unit GUIDs match, so this is a label table, not a gate.
KNOWN_PRODUCTS = {
    "4c01": "Insta360 Link",
    "4c04": "Insta360 Link 2",
}

# Extension unit GUIDs, byte-for-byte as they appear in the USB descriptor.
# The unit *ID* varies between firmwares, so we locate units by GUID.
XU_MAIN_GUID = "faf1672d-b71b-4793-8c91-7b1c9b7f95f8"
XU_TRACK_GUID = "e307e649-4618-a3ff-82fc-2d8b5f216773"
XU_EXTRA_GUID = "a8bd5df2-1a98-474e-8dd0-d92672d194fa"

# Selectors on the main extension unit.
SEL_MODE = 2           # framing/AI mode. Byte 0 is the mode and byte 1 its flag, but the
                       # selector is a larger firmware-owned record, so writes are
                       # read-modify-write and the readback is state, not an echo.
SEL_GESTURE_MASK = 5   # 1 byte, bitmask of enabled gesture groups
SEL_GESTURE_BIND = 6   # 5 bytes, one action per gesture slot (firmware-owned; writes
                       # are accepted but do not stick)
SEL_NOISE_CANCEL = 7   # 1 byte, microphone noise cancellation
SEL_SERIAL = 12        # 32 bytes, unit serial (writable -- never touch)
SEL_GIMBAL_RESET = 14  # 1 byte, write 1 to recentre (no observable effect on the Link 2)
SEL_USB_MODE = 17      # 1 byte, 0=uvc 1=photo 2=mass-storage -- drops the webcam
SEL_TRACK_FRAME = 19   # 1 byte, 1=head 2=upper body 3=full body
SEL_TRACK_TARGET = 21  # normalised float x/y of the tracking target
SEL_FRAME_BIAS = 24    # 2x int16 framing offset
SEL_PTZ_SPEED = 25     # gimbal movement speed
SEL_PTZ_ABSOLUTE = 26  # 2x int32 (tilt, pan) in arcseconds -- mirrors V4L2
SEL_FEATURE = 27       # 2 bytes: {parameter id, value} multiplexer
SEL_EXPOSURE = 29      # shutter time
SEL_AE_MODE = 30       # auto-exposure mode

# Writing these can brick the webcam for the session, overwrite its identity, or
# push malformed blobs into firmware storage. `linkctl raw` refuses them unless
# --force is given; the high-level API never touches them at all.
UNSAFE_SELECTORS = {
    SEL_USB_MODE: "switches the USB personality; the camera stops being a webcam until replug",
    SEL_SERIAL: "overwrites the unit serial number",
    3: "bulk configuration blob",
    4: "bulk configuration blob",
    8: "bulk transfer buffer",
    10: "mode string blob",
    13: "device identifier",
    16: "tone curve blob",
    23: "unidentified blob",
}

# byte[0], byte[1] written to SEL_MODE.
MODES = {
    "normal": (0x00, 0x00),
    "track": (0x01, 0x00),
    "whiteboard": (0x04, 0x01),
    "overhead": (0x05, 0x03),
    "deskview": (0x06, 0x10),
}

TRACK_FRAMES = {"head": 1, "upper": 2, "full": 3}

# Parameter IDs for the SEL_FEATURE multiplexer.
FEATURES = {
    "flip": 0x0B,
    "hdr": 0x0E,
    "ai-zoom": 0x1C,
    "tracking-engine": 0x1D,
    "portrait": 0x2A,
    "touch-gesture": 0x2E,
    "drag": 0x2F,
}

ARCSEC_PER_DEGREE = 3600
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "insta360-link",
)


class CameraNotFound(Exception):
    pass


class UnsafeWrite(Exception):
    pass


def _sysfs_usb_attrs(video_node):
    """Walk up from /sys/class/video4linux/videoN to the owning USB device."""
    base = f"/sys/class/video4linux/{os.path.basename(video_node)}/device"
    for _ in range(6):
        vendor = os.path.join(base, "idVendor")
        if os.path.exists(vendor):
            read = lambda n: open(os.path.join(base, n)).read().strip()
            attrs = {
                "vid": read("idVendor"),
                "pid": read("idProduct"),
                "syspath": os.path.realpath(base),
            }
            for optional in ("product", "manufacturer", "serial"):
                try:
                    attrs[optional] = read(optional)
                except OSError:
                    attrs[optional] = ""
            return attrs
        base = os.path.join(base, "..")
    return None


def parse_extension_units(syspath):
    """Read the USB config descriptor and return {guid: unit_id} for every XU."""
    import uuid

    path = os.path.realpath(os.path.join(syspath, "descriptors"))
    try:
        data = open(path, "rb").read()
    except OSError:
        return {}

    units = {}
    offset, in_videocontrol = 0, False
    while offset < len(data):
        length = data[offset]
        if length == 0:
            break
        dtype = data[offset + 1]
        block = data[offset : offset + length]
        if dtype == 0x04:  # interface descriptor
            in_videocontrol = block[5] == 0x0E and block[6] == 0x01
        elif dtype == 0x24 and in_videocontrol and block[2] == 0x06:  # VC_EXTENSION_UNIT
            unit_id = block[3]
            guid = str(uuid.UUID(bytes_le=bytes(block[4:20])))
            units[guid] = unit_id
        offset += length
    return units


def find_cameras():
    """Every Insta360 video node on the system that can actually capture video."""
    found = []
    for node in sorted(glob.glob("/dev/video*")):
        attrs = _sysfs_usb_attrs(node)
        if not attrs or attrs["vid"].lower() != INSTA360_VID:
            continue
        try:
            with V4L2Device(node) as dev:
                caps = dev.query_capabilities()
                if not caps["is_capture"]:
                    continue  # skip the metadata node
                # Only the node that exposes the controls is useful to us.
                if not dev.has(io.V4L2_CID_ZOOM_ABSOLUTE):
                    continue
        except OSError:
            continue
        attrs["node"] = node
        attrs["card"] = caps["card"]
        attrs["model"] = KNOWN_PRODUCTS.get(attrs["pid"].lower(), attrs.get("product") or "Insta360 camera")
        found.append(attrs)
    return found


class LinkCamera:
    """Control surface for one Insta360 Link-family webcam."""

    def __init__(self, node=None):
        if node is None:
            cameras = find_cameras()
            if not cameras:
                raise CameraNotFound(
                    "No Insta360 Link camera found. Check `lsusb | grep 2e1a` and that "
                    "you can read /dev/video*."
                )
            info = cameras[0]
        else:
            attrs = _sysfs_usb_attrs(node)
            info = attrs or {"vid": "", "pid": "", "syspath": ""}
            info["node"] = node
            info.setdefault("model", "camera")

        self.info = info
        self.node = info["node"]
        self.dev = V4L2Device(self.node)
        capabilities = self.dev.query_capabilities()
        self.info.setdefault("card", capabilities["card"])
        if node is not None and not self.dev.has(io.V4L2_CID_ZOOM_ABSOLUTE):
            self.dev.close()
            hint = ""
            others = [c["node"] for c in find_cameras() if c["node"] != node]
            if others:
                hint = f" Try {' or '.join(others)}."
            raise CameraNotFound(
                f"{node} exposes no camera controls -- it is probably the metadata "
                f"node rather than the video node.{hint}"
            )

        units = parse_extension_units(info.get("syspath", "")) if info.get("syspath") else {}
        self.units = units
        self.xu_main = units.get(XU_MAIN_GUID)
        self.xu_track = units.get(XU_TRACK_GUID)
        self.xu_extra = units.get(XU_EXTRA_GUID)
        self._len_cache = {}
        self._last_mode = None
        self._gesture_backup = None

    def close(self):
        self.dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def has_extension_unit(self):
        return self.xu_main is not None

    # ---------- extension unit plumbing ----------

    def _require_xu(self):
        if self.xu_main is None:
            raise CameraNotFound(
                "This camera does not expose the Insta360 control extension unit; "
                "proprietary features are unavailable."
            )
        return self.xu_main

    def selector_length(self, selector, unit=None):
        """Ask the device how long this selector is, and remember the answer."""
        unit = unit if unit is not None else self._require_xu()
        key = (unit, selector)
        if key not in self._len_cache:
            self._len_cache[key] = self.dev.xu_len(unit, selector)
        return self._len_cache[key]

    def xu_write(self, selector, payload, unit=None, force=False):
        """Write to an extension-unit selector, zero-padded to its true length."""
        unit = unit if unit is not None else self._require_xu()
        if not force:
            if unit != self.xu_main:
                raise UnsafeWrite(
                    f"writes to extension unit {unit} are blocked: its selectors are "
                    "not mapped, and several are write-only with unknown effects"
                )
            if selector in UNSAFE_SELECTORS:
                raise UnsafeWrite(
                    f"selector {selector} is blocked: {UNSAFE_SELECTORS[selector]}"
                )
        length = self.selector_length(selector, unit)
        buf = bytearray(length)
        payload = bytes(payload)
        if len(payload) > length:
            raise ValueError(f"payload is {len(payload)} bytes, selector holds {length}")
        buf[: len(payload)] = payload
        self.dev.xu_set(unit, selector, bytes(buf))

    def xu_update(self, selector, changes, unit=None, force=False):
        """Change individual bytes of a selector, leaving the rest of the record intact.

        Several selectors are structured records the firmware also writes to. Blindly
        sending a zero-padded buffer blanks fields we do not understand, so read the
        current contents first and patch only the bytes we mean to change.
        """
        unit = unit if unit is not None else self._require_xu()
        current = bytearray(self.xu_read(selector, unit=unit))
        for offset, value in changes.items():
            current[offset] = value & 0xFF
        self.xu_write(selector, bytes(current), unit=unit, force=force)
        return bytes(current)

    def xu_read(self, selector, unit=None):
        unit = unit if unit is not None else self._require_xu()
        return self.dev.xu_get(unit, selector, self.selector_length(selector, unit))

    # ---------- pan / tilt / zoom ----------

    def _range_degrees(self, cid):
        ctrl = self.dev.control(cid)
        if ctrl is None:
            return None
        return (ctrl.min / ARCSEC_PER_DEGREE, ctrl.max / ARCSEC_PER_DEGREE)

    @property
    def pan_range(self):
        return self._range_degrees(io.V4L2_CID_PAN_ABSOLUTE)

    @property
    def tilt_range(self):
        return self._range_degrees(io.V4L2_CID_TILT_ABSOLUTE)

    @property
    def zoom_range(self):
        ctrl = self.dev.control(io.V4L2_CID_ZOOM_ABSOLUTE)
        return (ctrl.min / 100, ctrl.max / 100) if ctrl else None

    def get_pan(self):
        return self.dev.get(io.V4L2_CID_PAN_ABSOLUTE) / ARCSEC_PER_DEGREE

    def get_tilt(self):
        return self.dev.get(io.V4L2_CID_TILT_ABSOLUTE) / ARCSEC_PER_DEGREE

    def get_zoom(self):
        return self.dev.get(io.V4L2_CID_ZOOM_ABSOLUTE) / 100

    def set_pan(self, degrees):
        return self.dev.set(io.V4L2_CID_PAN_ABSOLUTE, round(degrees * ARCSEC_PER_DEGREE)) / ARCSEC_PER_DEGREE

    def set_tilt(self, degrees):
        return self.dev.set(io.V4L2_CID_TILT_ABSOLUTE, round(degrees * ARCSEC_PER_DEGREE)) / ARCSEC_PER_DEGREE

    def set_zoom(self, factor):
        return self.dev.set(io.V4L2_CID_ZOOM_ABSOLUTE, round(factor * 100)) / 100

    def move(self, d_pan=0.0, d_tilt=0.0):
        """Nudge the gimbal by a relative number of degrees."""
        if d_pan:
            self.set_pan(self.get_pan() + d_pan)
        if d_tilt:
            self.set_tilt(self.get_tilt() + d_tilt)
        return self.get_pan(), self.get_tilt()

    def center(self):
        """Point the gimbal straight ahead at 1x zoom."""
        self.set_pan(0)
        self.set_tilt(0)
        self.set_zoom(1.0)

    def gimbal_reset(self, settle=2.5):
        """Ask the firmware to re-home the gimbal.

        Returns (moved, before, after) as physical (tilt, pan) arcsecond pairs.
        On the Link 2 this firmware trigger appears to do nothing -- measured with the
        camera both parked and streaming -- so callers should treat a False result as
        normal and use center() to recentre.
        """
        before = self.ptz_raw()
        self.xu_write(SEL_GIMBAL_RESET, b"\x01")
        time.sleep(settle)
        after = self.ptz_raw()
        return before != after, before, after

    # The gimbal stows at roughly -85 deg tilt when no application is streaming.
    PARKED_TILT_DEGREES = -80

    def is_parked(self):
        """True when the gimbal is stowed because nothing is capturing video.

        While parked the camera accepts pan/tilt writes and reports them back as the
        setpoint, but does not move, and on wake it restores the position it held when
        it last streamed rather than the setpoint. Measured on the Link 2.
        """
        if not self.has_extension_unit:
            return False
        try:
            tilt, _ = self.ptz_raw()
        except OSError:
            return False
        return tilt / ARCSEC_PER_DEGREE <= self.PARKED_TILT_DEGREES

    def ptz_raw(self):
        """(tilt, pan) in arcseconds straight from the extension unit."""
        data = self.xu_read(SEL_PTZ_ABSOLUTE)
        tilt, pan = struct.unpack_from("<ii", data, 0)
        return tilt, pan

    # ---------- proprietary modes ----------

    def set_mode(self, name):
        try:
            mode, flag = MODES[name]
        except KeyError:
            raise ValueError(f"unknown mode {name!r}; choose from {', '.join(MODES)}")
        self.xu_update(SEL_MODE, {0: mode, 1: flag})
        self._remember_mode(name)
        return name

    def get_mode(self):
        """Best-effort mode readback.

        The Link 2 firmware overwrites byte 0 with 0xFF once any mode has been set,
        so it reports "a mode is active" without saying which. In that case fall back
        to whatever this process last selected.
        """
        data = self.xu_read(SEL_MODE)
        if data[0] == 0xFF:
            return self._last_mode or self._recall_mode() or "custom"
        for name, (mode, flag) in MODES.items():
            if data[0] == mode and data[1] == flag:
                return name
        for name, (mode, _) in MODES.items():
            if data[0] == mode:
                return name
        return f"unknown(0x{data[0]:02x},0x{data[1]:02x})"

    def set_tracking(self, enabled):
        return self.set_mode("track" if enabled else "normal")

    def get_tracking(self):
        return self.get_mode() == "track"

    def set_track_frame(self, name):
        if name not in TRACK_FRAMES:
            raise ValueError(f"unknown framing {name!r}; choose from {', '.join(TRACK_FRAMES)}")
        self.xu_update(SEL_TRACK_FRAME, {0: TRACK_FRAMES[name]})
        return name

    def get_track_frame(self):
        value = self.xu_read(SEL_TRACK_FRAME)[0]
        for name, code in TRACK_FRAMES.items():
            if code == value:
                return name
        return f"unknown({value})"

    def set_feature(self, name, value):
        """Toggle a firmware feature through the parameter multiplexer.

        The camera gives no readback for these, so state is not queryable.
        """
        if name not in FEATURES:
            raise ValueError(f"unknown feature {name!r}; choose from {', '.join(FEATURES)}")
        self.xu_write(SEL_FEATURE, bytes([FEATURES[name], 1 if value else 0]))
        return bool(value)

    def set_noise_cancellation(self, enabled):
        self.xu_write(SEL_NOISE_CANCEL, bytes([1 if enabled else 0]))
        return bool(enabled)

    def get_noise_cancellation(self):
        return bool(self.xu_read(SEL_NOISE_CANCEL)[0])

    def get_gesture_mask(self):
        return self.xu_read(SEL_GESTURE_MASK)[0]

    def set_gesture_mask(self, mask):
        self.xu_write(SEL_GESTURE_MASK, bytes([mask & 0xFF]))
        return mask & 0xFF

    def set_gestures(self, enabled):
        """Turn gesture groups off, or back on to whatever was enabled before.

        The firmware clamps the mask to the groups this model supports, so asking for
        all of them is safe. Note that disabling gestures also clears the firmware's
        gesture *binding* table (selector 6), which this tool cannot write back --
        replug the camera to restore its defaults.
        """
        if not enabled:
            current = self.get_gesture_mask()
            if current:
                self._gesture_backup = current
            return self.set_gesture_mask(0x00)
        return self.set_gesture_mask(getattr(self, "_gesture_backup", 0x1F) or 0x1F)

    def serial_number(self):
        if self.xu_main is None:
            return self.info.get("serial", "")
        raw = self.xu_read(SEL_SERIAL)
        return raw.split(b"\x00")[0].decode("ascii", "replace")

    # ---------- image controls ----------

    IMAGE_CONTROLS = {
        "brightness": io.V4L2_CID_BRIGHTNESS,
        "contrast": io.V4L2_CID_CONTRAST,
        "saturation": io.V4L2_CID_SATURATION,
        "hue": io.V4L2_CID_HUE,
        "sharpness": io.V4L2_CID_SHARPNESS,
        "white-balance": io.V4L2_CID_WHITE_BALANCE_TEMPERATURE,
        "auto-white-balance": io.V4L2_CID_AUTO_WHITE_BALANCE,
        "power-line-frequency": io.V4L2_CID_POWER_LINE_FREQUENCY,
        "focus": io.V4L2_CID_FOCUS_ABSOLUTE,
        "autofocus": io.V4L2_CID_FOCUS_AUTO,
        "pan": io.V4L2_CID_PAN_ABSOLUTE,
        "tilt": io.V4L2_CID_TILT_ABSOLUTE,
        "zoom": io.V4L2_CID_ZOOM_ABSOLUTE,
    }

    def control_id(self, name):
        key = name.lower().replace("_", "-")
        if key not in self.IMAGE_CONTROLS:
            raise ValueError(f"unknown control {name!r}; try `linkctl info`")
        cid = self.IMAGE_CONTROLS[key]
        if not self.dev.has(cid):
            raise ValueError(f"this camera does not expose {key!r}")
        return cid

    def get_control(self, name):
        return self.dev.get(self.control_id(name))

    def set_control(self, name, value):
        return self.dev.set(self.control_id(name), value)

    def controls(self, refresh=True):
        return self.dev.enumerate_controls(refresh=refresh)

    # ---------- presets ----------

    @property
    def _state_path(self):
        return os.path.join(CONFIG_DIR, "state.json")

    def _remember_mode(self, name):
        """Record the mode on disk, since the camera will not tell us which one is active."""
        self._last_mode = name
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            state = {}
            if os.path.exists(self._state_path):
                with open(self._state_path) as handle:
                    state = json.load(handle)
            state.setdefault("modes", {})[self.info.get("syspath", self.node)] = name
            with open(self._state_path, "w") as handle:
                json.dump(state, handle, indent=2)
        except (OSError, ValueError):
            pass  # a missing cache is not worth failing a mode change over

    def _recall_mode(self):
        try:
            with open(self._state_path) as handle:
                return json.load(handle).get("modes", {}).get(self.info.get("syspath", self.node))
        except (OSError, ValueError):
            return None

    @property
    def _preset_path(self):
        return os.path.join(CONFIG_DIR, "presets.json")

    def _load_presets(self):
        try:
            with open(self._preset_path) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def save_preset(self, slot):
        presets = self._load_presets()
        presets[str(slot)] = {
            "pan": self.get_pan(),
            "tilt": self.get_tilt(),
            "zoom": self.get_zoom(),
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(self._preset_path, "w") as handle:
            json.dump(presets, handle, indent=2, sort_keys=True)
        return presets[str(slot)]

    def recall_preset(self, slot):
        preset = self._load_presets().get(str(slot))
        if not preset:
            raise ValueError(f"preset {slot} is empty")
        self.set_zoom(preset["zoom"])
        self.set_pan(preset["pan"])
        self.set_tilt(preset["tilt"])
        return preset

    def list_presets(self):
        return self._load_presets()

    def delete_preset(self, slot):
        presets = self._load_presets()
        if presets.pop(str(slot), None) is None:
            return False
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(self._preset_path, "w") as handle:
            json.dump(presets, handle, indent=2, sort_keys=True)
        return True
