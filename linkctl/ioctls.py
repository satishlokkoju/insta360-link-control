"""Linux ioctl number construction and the V4L2/UVC structs we need.

Everything here is derived from <linux/videodev2.h> and <linux/uvcvideo.h>.
Sizes are for the LP64 ABI (x86_64 / aarch64), which is what Linux desktops use.
"""

import struct

# --- _IOC encoding (asm-generic/ioctl.h) ---
_IOC_NRBITS, _IOC_TYPEBITS, _IOC_SIZEBITS = 8, 8, 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(direction, typ, nr, size):
    return (
        (direction << _IOC_DIRSHIFT)
        | (ord(typ) << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IOR(typ, nr, size):
    return _IOC(_IOC_READ, typ, nr, size)


def _IOWR(typ, nr, size):
    return _IOC(_IOC_READ | _IOC_WRITE, typ, nr, size)


# --- struct layouts ---
# struct v4l2_capability { u8 driver[16], card[32], bus_info[32];
#                          u32 version, capabilities, device_caps; u32 reserved[3]; }
CAPABILITY_FMT = "16s32s32sIII12x"
CAPABILITY_SIZE = struct.calcsize(CAPABILITY_FMT)  # 104

# struct v4l2_control { u32 id; s32 value; }
CONTROL_FMT = "Ii"
CONTROL_SIZE = struct.calcsize(CONTROL_FMT)  # 8

# struct v4l2_queryctrl { u32 id, type; u8 name[32];
#                         s32 minimum, maximum, step, default_value; u32 flags, reserved[2]; }
QUERYCTRL_FMT = "II32siiiiI8x"
QUERYCTRL_SIZE = struct.calcsize(QUERYCTRL_FMT)  # 68

# struct v4l2_querymenu is __packed: { u32 id, index; union { u8 name[32]; s64 value; }; u32 reserved; }
QUERYMENU_FMT = "=II32sI"
QUERYMENU_SIZE = struct.calcsize(QUERYMENU_FMT)  # 44

# struct uvc_xu_control_query { u8 unit, selector, query; u16 size; u8 *data; }
# Natural alignment inserts one pad byte before `size` and four before `data`.
XU_QUERY_FMT = "BBBxH2xQ"
XU_QUERY_SIZE = struct.calcsize(XU_QUERY_FMT)  # 16

# --- ioctl numbers ---
VIDIOC_QUERYCAP = _IOR("V", 0, CAPABILITY_SIZE)
VIDIOC_G_CTRL = _IOWR("V", 27, CONTROL_SIZE)
VIDIOC_S_CTRL = _IOWR("V", 28, CONTROL_SIZE)
VIDIOC_QUERYCTRL = _IOWR("V", 36, QUERYCTRL_SIZE)
VIDIOC_QUERYMENU = _IOWR("V", 37, QUERYMENU_SIZE)
UVCIOC_CTRL_QUERY = _IOWR("u", 0x21, XU_QUERY_SIZE)

# --- capability bits ---
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_META_CAPTURE = 0x00800000

# --- control enumeration ---
V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000
V4L2_CTRL_FLAG_DISABLED = 0x0001
V4L2_CTRL_FLAG_READ_ONLY = 0x0004
V4L2_CTRL_FLAG_INACTIVE = 0x0010
V4L2_CTRL_FLAG_WRITE_ONLY = 0x0040

V4L2_CTRL_TYPE_INTEGER = 1
V4L2_CTRL_TYPE_BOOLEAN = 2
V4L2_CTRL_TYPE_MENU = 3
V4L2_CTRL_TYPE_BUTTON = 4
V4L2_CTRL_TYPE_CTRL_CLASS = 6

# --- control IDs we care about ---
V4L2_CID_BRIGHTNESS = 0x00980900
V4L2_CID_CONTRAST = 0x00980901
V4L2_CID_SATURATION = 0x00980902
V4L2_CID_HUE = 0x00980903
V4L2_CID_AUTO_WHITE_BALANCE = 0x0098090C
V4L2_CID_POWER_LINE_FREQUENCY = 0x00980918
V4L2_CID_WHITE_BALANCE_TEMPERATURE = 0x0098091A
V4L2_CID_SHARPNESS = 0x0098091B
V4L2_CID_PAN_ABSOLUTE = 0x009A0908
V4L2_CID_TILT_ABSOLUTE = 0x009A0909
V4L2_CID_FOCUS_ABSOLUTE = 0x009A090A
V4L2_CID_FOCUS_AUTO = 0x009A090C
V4L2_CID_ZOOM_ABSOLUTE = 0x009A090D

# --- UVC extension unit request codes ---
UVC_SET_CUR = 0x01
UVC_GET_CUR = 0x81
UVC_GET_MIN = 0x82
UVC_GET_MAX = 0x83
UVC_GET_RES = 0x84
UVC_GET_LEN = 0x85
UVC_GET_INFO = 0x86
UVC_GET_DEF = 0x87

# GET_INFO bits
UVC_INFO_SUPPORTS_GET = 0x01
UVC_INFO_SUPPORTS_SET = 0x02
