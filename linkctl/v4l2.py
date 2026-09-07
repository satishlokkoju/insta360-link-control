"""Minimal V4L2 control access over raw ioctls -- no external dependencies."""

import ctypes
import errno
import fcntl
import os
import stat
import struct

from . import ioctls as io


class V4L2Error(OSError):
    pass


class Control:
    """One enumerated V4L2 control."""

    def __init__(self, cid, ctype, name, minimum, maximum, step, default, flags):
        self.id = cid
        self.type = ctype
        self.name = name
        self.min = minimum
        self.max = maximum
        self.step = step
        self.default = default
        self.flags = flags
        self.menu = {}  # index -> label, for menu controls

    @property
    def writable(self):
        return not (self.flags & (io.V4L2_CTRL_FLAG_READ_ONLY | io.V4L2_CTRL_FLAG_DISABLED))

    @property
    def inactive(self):
        """True when another control (usually an 'auto' toggle) currently owns this one."""
        return bool(self.flags & io.V4L2_CTRL_FLAG_INACTIVE)

    def clamp(self, value):
        value = max(self.min, min(self.max, int(value)))
        if self.step > 1:
            # Snap to the nearest legal step relative to the minimum.
            value = self.min + round((value - self.min) / self.step) * self.step
            value = max(self.min, min(self.max, int(value)))
        return value

    def __repr__(self):
        return f"<Control {self.name} 0x{self.id:08x} {self.min}..{self.max}>"


class V4L2Device:
    """A V4L2 character device, opened for control access only (no streaming)."""

    def __init__(self, path):
        self.path = path
        if not stat.S_ISCHR(os.stat(path).st_mode):
            raise ValueError(f"{path} is not a character device")
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self._controls = None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- capabilities ----------

    def query_capabilities(self):
        buf = bytearray(io.CAPABILITY_SIZE)
        fcntl.ioctl(self.fd, io.VIDIOC_QUERYCAP, buf, True)
        driver, card, bus, version, caps, device_caps = struct.unpack(io.CAPABILITY_FMT, buf)
        cstr = lambda b: b.split(b"\x00")[0].decode("utf-8", "replace")
        # device_caps is only meaningful when the driver sets V4L2_CAP_DEVICE_CAPS.
        effective = device_caps or caps
        return {
            "driver": cstr(driver),
            "card": cstr(card),
            "bus_info": cstr(bus),
            "version": f"{version >> 16}.{(version >> 8) & 0xFF}.{version & 0xFF}",
            "capabilities": caps,
            "device_caps": effective,
            "is_capture": bool(effective & io.V4L2_CAP_VIDEO_CAPTURE),
        }

    # ---------- controls ----------

    def enumerate_controls(self, refresh=False):
        """Walk every control the driver exposes, using the NEXT_CTRL flag."""
        if self._controls is not None and not refresh:
            return self._controls

        controls = {}
        cid = 0
        while True:
            buf = bytearray(io.QUERYCTRL_SIZE)
            struct.pack_into("I", buf, 0, cid | io.V4L2_CTRL_FLAG_NEXT_CTRL)
            try:
                fcntl.ioctl(self.fd, io.VIDIOC_QUERYCTRL, buf, True)
            except OSError as exc:
                # EINVAL is the documented end-of-list marker; uvcvideo ends the
                # NEXT_CTRL walk with EIO instead, so both terminate the loop.
                if exc.errno in (errno.EINVAL, errno.ENOTTY, errno.EIO):
                    break
                raise
            rid, ctype, raw_name, mn, mx, step, dflt, flags = struct.unpack(io.QUERYCTRL_FMT, buf)
            if rid <= cid:
                break  # driver is not advancing; guard against an infinite loop
            cid = rid
            if ctype == io.V4L2_CTRL_TYPE_CTRL_CLASS:
                continue  # class headers are labels, not controls
            if flags & io.V4L2_CTRL_FLAG_DISABLED:
                continue
            name = raw_name.split(b"\x00")[0].decode("utf-8", "replace")
            ctrl = Control(rid, ctype, name, mn, mx, step, dflt, flags)
            if ctype == io.V4L2_CTRL_TYPE_MENU:
                ctrl.menu = self._enumerate_menu(rid, mn, mx)
            controls[rid] = ctrl

        self._controls = controls
        return controls

    def _enumerate_menu(self, cid, minimum, maximum):
        items = {}
        for index in range(minimum, maximum + 1):
            buf = bytearray(io.QUERYMENU_SIZE)
            struct.pack_into("=II", buf, 0, cid, index)
            try:
                fcntl.ioctl(self.fd, io.VIDIOC_QUERYMENU, buf, True)
            except OSError:
                continue  # sparse menus are legal; skip holes
            _, _, raw, _ = struct.unpack(io.QUERYMENU_FMT, buf)
            items[index] = raw.split(b"\x00")[0].decode("utf-8", "replace")
        return items

    def control(self, cid, refresh=False):
        return self.enumerate_controls(refresh=refresh).get(cid)

    def has(self, cid):
        return cid in self.enumerate_controls()

    def get(self, cid):
        buf = bytearray(struct.pack(io.CONTROL_FMT, cid, 0))
        fcntl.ioctl(self.fd, io.VIDIOC_G_CTRL, buf, True)
        return struct.unpack(io.CONTROL_FMT, buf)[1]

    def set(self, cid, value):
        """Set a control, clamping to its advertised range. Returns the value written."""
        ctrl = self.control(cid)
        if ctrl is not None:
            value = ctrl.clamp(value)
        buf = bytearray(struct.pack(io.CONTROL_FMT, cid, int(value)))
        fcntl.ioctl(self.fd, io.VIDIOC_S_CTRL, buf, True)
        return int(value)

    # ---------- UVC extension units ----------

    def xu(self, unit, selector, query, size):
        """Issue a raw UVCIOC_CTRL_QUERY. Returns the data buffer for reads."""
        size = max(int(size), 1)
        data = (ctypes.c_uint8 * size)()
        req = struct.pack(
            io.XU_QUERY_FMT, unit, selector, query, size, ctypes.addressof(data)
        )
        fcntl.ioctl(self.fd, io.UVCIOC_CTRL_QUERY, req)
        return bytes(data)

    def xu_write(self, unit, selector, query, payload):
        buf = (ctypes.c_uint8 * len(payload)).from_buffer_copy(bytes(payload))
        req = struct.pack(
            io.XU_QUERY_FMT, unit, selector, query, len(payload), ctypes.addressof(buf)
        )
        fcntl.ioctl(self.fd, io.UVCIOC_CTRL_QUERY, req)

    def xu_len(self, unit, selector):
        """Device-reported payload length. Always size transfers from this, never from docs."""
        return struct.unpack("<H", self.xu(unit, selector, io.UVC_GET_LEN, 2))[0]

    def xu_info(self, unit, selector):
        return self.xu(unit, selector, io.UVC_GET_INFO, 1)[0]

    def xu_get(self, unit, selector, size=None):
        if size is None:
            size = self.xu_len(unit, selector)
        return self.xu(unit, selector, io.UVC_GET_CUR, size)

    def xu_set(self, unit, selector, payload):
        self.xu_write(unit, selector, io.UVC_SET_CUR, payload)
