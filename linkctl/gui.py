"""Tkinter control panel for the Insta360 Link.

Uses only the standard library. The optional live preview shells out to ffmpeg
and reads raw PPM frames from its stdout, which Tk can display directly.
"""

import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import ioctls as io
from .camera import FEATURES, MODES, TRACK_FRAMES, LinkCamera

APP_NAME = "Link Control"

PREVIEW_WIDTH, PREVIEW_HEIGHT = 480, 270

MODE_LABELS = [
    ("normal", "Normal"),
    ("track", "AI Tracking"),
    ("whiteboard", "Whiteboard"),
    ("overhead", "Overhead"),
    ("deskview", "DeskView"),
]

FRAME_LABELS = [("head", "Head"), ("upper", "Upper body"), ("full", "Full body")]

IMAGE_SLIDERS = [
    ("Brightness", io.V4L2_CID_BRIGHTNESS),
    ("Contrast", io.V4L2_CID_CONTRAST),
    ("Saturation", io.V4L2_CID_SATURATION),
    ("Sharpness", io.V4L2_CID_SHARPNESS),
    ("Hue", io.V4L2_CID_HUE),
]


class PreviewStream:
    """Runs ffmpeg and hands the newest decoded frame to the UI thread."""

    def __init__(self, node, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT, fps=15):
        self.node = node
        self.width, self.height, self.fps = width, height, fps
        self.frames = queue.Queue(maxsize=1)
        self.error = None
        self._proc = None
        self._thread = None
        self._stop = threading.Event()

    @staticmethod
    def available():
        return shutil.which("ffmpeg") is not None

    def start(self):
        self._stop.clear()
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "v4l2", "-input_format", "mjpeg",
            "-video_size", "1280x720", "-framerate", "30",
            "-i", self.node,
            "-vf", f"scale={self.width}:{self.height},fps={self.fps}",
            "-f", "image2pipe", "-vcodec", "ppm", "-",
        ]
        try:
            self._proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )
        except OSError as exc:
            self.error = str(exc)
            return False
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def _read_exactly(self, count):
        chunks, remaining = [], count
        while remaining > 0:
            chunk = self._proc.stdout.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_token(self):
        """Read one whitespace-delimited PPM header token, skipping comments."""
        token = b""
        while True:
            char = self._proc.stdout.read(1)
            if not char:
                return None
            if char == b"#":
                while char and char not in b"\r\n":
                    char = self._proc.stdout.read(1)
                continue
            if char.isspace():
                if token:
                    return token
                continue
            token += char

    def _pump(self):
        try:
            while not self._stop.is_set():
                magic = self._read_token()
                if magic is None:
                    break
                if magic != b"P6":
                    continue  # resynchronise on the next header
                width = self._read_token()
                height = self._read_token()
                maxval = self._read_token()
                if None in (width, height, maxval):
                    break
                payload = self._read_exactly(int(width) * int(height) * 3)
                if payload is None:
                    break
                frame = b"P6\n%s %s\n%s\n%s" % (width, height, maxval, payload)
                try:
                    self.frames.get_nowait()  # drop the stale frame
                except queue.Empty:
                    pass
                try:
                    self.frames.put_nowait(frame)
                except queue.Full:
                    pass
        except (OSError, ValueError) as exc:
            self.error = str(exc)
        finally:
            if self._proc and self._proc.stderr and not self._stop.is_set():
                detail = self._proc.stderr.read().decode("utf-8", "replace").strip()
                if detail:
                    self.error = detail.splitlines()[-1]


class ControlPanel(ttk.Frame):
    def __init__(self, master, camera):
        super().__init__(master, padding=10)
        self.cam = camera
        self.preview = None
        self.photo = None
        self._repeat_job = None
        self._suppress = False  # set while we push camera state into the widgets
        self._said_at = 0.0

        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_left()
        self._build_right()
        self._build_statusbar()
        self.refresh_from_camera()
        self._poll_position()

    # ---------- layout ----------

    def _build_left(self):
        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nw", padx=(0, 12))

        preview_box = ttk.LabelFrame(left, text="Preview", padding=8)
        preview_box.grid(row=0, column=0, sticky="ew")
        self.canvas = tk.Canvas(
            preview_box, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT,
            background="#1c1c1c", highlightthickness=0,
        )
        self.canvas.grid(row=0, column=0)
        self.placeholder = self.canvas.create_text(
            PREVIEW_WIDTH // 2, PREVIEW_HEIGHT // 2,
            text="Preview off\n\nThe camera can only stream to one app at a time,\n"
                 "so turn this off before joining a call.",
            fill="#8a8a8a", justify="center", font=("TkDefaultFont", 9),
        )
        self.preview_var = tk.BooleanVar(value=False)
        self.preview_button = ttk.Checkbutton(
            preview_box, text="Show live preview", variable=self.preview_var,
            command=self.toggle_preview, style="Switch.TCheckbutton",
        )
        self.preview_button.grid(row=1, column=0, sticky="w", pady=(8, 0))
        if not PreviewStream.available():
            self.preview_button.state(["disabled"])
            self.canvas.itemconfigure(self.placeholder, text="Preview needs ffmpeg\n(sudo apt install ffmpeg)")

        pad = ttk.LabelFrame(left, text="Gimbal", padding=8)
        pad.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for column in range(3):
            pad.columnconfigure(column, weight=1)

        buttons = [
            ("↖", -1, 1, 0, 0), ("↑", 0, 1, 0, 1), ("↗", 1, 1, 0, 2),
            ("←", -1, 0, 1, 0), ("●", 0, 0, 1, 1), ("→", 1, 0, 1, 2),
            ("↙", -1, -1, 2, 0), ("↓", 0, -1, 2, 1), ("↘", 1, -1, 2, 2),
        ]
        for label, dx, dy, row, column in buttons:
            button = ttk.Button(pad, text=label, width=4)
            button.grid(row=row, column=column, padx=2, pady=2, sticky="ew")
            if dx == 0 and dy == 0:
                button.configure(command=self.do_center)
            else:
                button.bind("<ButtonPress-1>", lambda e, x=dx, y=dy: self._start_nudge(x, y))
                button.bind("<ButtonRelease-1>", self._stop_nudge)

        step_row = ttk.Frame(pad)
        step_row.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(step_row, text="Step").pack(side="left")
        self.step_var = tk.DoubleVar(value=5.0)
        for degrees in (1, 5, 15):
            ttk.Radiobutton(
                step_row, text=f"{degrees}°", value=float(degrees), variable=self.step_var
            ).pack(side="left", padx=(6, 0))
        ttk.Button(step_row, text="Refresh", command=self.do_refresh).pack(side="right")

        zoom_box = ttk.Frame(pad)
        zoom_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        zoom_box.columnconfigure(1, weight=1)
        ttk.Label(zoom_box, text="Zoom").grid(row=0, column=0, sticky="w")
        self.zoom_label = ttk.Label(zoom_box, text="1.00x", width=6, anchor="e")
        self.zoom_label.grid(row=0, column=2, sticky="e")
        lo, hi = self.cam.zoom_range or (1.0, 4.0)
        self.zoom_scale = ttk.Scale(
            zoom_box, from_=lo, to=hi, orient="horizontal", command=self.on_zoom
        )
        self.zoom_scale.grid(row=0, column=1, sticky="ew", padx=6)

    def _build_right(self):
        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=1, sticky="nsew")

        # --- Framing tab ---
        framing = ttk.Frame(notebook, padding=10)
        notebook.add(framing, text="Framing")
        framing.columnconfigure(0, weight=1)

        mode_box = ttk.LabelFrame(framing, text="Mode", padding=8)
        mode_box.grid(row=0, column=0, sticky="ew")
        self.mode_var = tk.StringVar(value="normal")
        for index, (value, label) in enumerate(MODE_LABELS):
            ttk.Radiobutton(
                mode_box, text=label, value=value, variable=self.mode_var,
                command=self.on_mode,
            ).grid(row=index, column=0, sticky="w")

        frame_box = ttk.LabelFrame(framing, text="Tracking framing", padding=8)
        frame_box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.frame_var = tk.StringVar(value="head")
        for index, (value, label) in enumerate(FRAME_LABELS):
            ttk.Radiobutton(
                frame_box, text=label, value=value, variable=self.frame_var,
                command=self.on_frame,
            ).grid(row=0, column=index, padx=(0, 10), sticky="w")

        toggles = ttk.LabelFrame(framing, text="Camera toggles", padding=8)
        toggles.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.gesture_var = tk.BooleanVar()
        ttk.Checkbutton(
            toggles, text="Hand gesture control", variable=self.gesture_var,
            command=self.on_gestures,
        ).grid(row=0, column=0, sticky="w")
        self.noise_var = tk.BooleanVar()
        ttk.Checkbutton(
            toggles, text="Microphone noise cancellation", variable=self.noise_var,
            command=self.on_noise,
        ).grid(row=1, column=0, sticky="w")

        # --- Image tab ---
        image = ttk.Frame(notebook, padding=10)
        notebook.add(image, text="Image")
        image.columnconfigure(1, weight=1)
        self.sliders = {}
        row = 0
        for label, cid in IMAGE_SLIDERS:
            ctrl = self.cam.dev.control(cid)
            if ctrl is None:
                continue
            self._add_slider(image, row, label, cid, ctrl)
            row += 1

        wb_ctrl = self.cam.dev.control(io.V4L2_CID_WHITE_BALANCE_TEMPERATURE)
        if wb_ctrl is not None:
            self._add_slider(image, row, "White balance", io.V4L2_CID_WHITE_BALANCE_TEMPERATURE, wb_ctrl)
            row += 1
        self.awb_var = tk.BooleanVar()
        ttk.Checkbutton(
            image, text="Auto white balance", variable=self.awb_var, command=self.on_awb,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 6))
        row += 1

        focus_ctrl = self.cam.dev.control(io.V4L2_CID_FOCUS_ABSOLUTE)
        if focus_ctrl is not None:
            self._add_slider(image, row, "Focus", io.V4L2_CID_FOCUS_ABSOLUTE, focus_ctrl)
            row += 1
        self.af_var = tk.BooleanVar()
        ttk.Checkbutton(
            image, text="Continuous autofocus", variable=self.af_var, command=self.on_af,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 6))
        row += 1

        ttk.Button(image, text="Restore defaults", command=self.restore_defaults).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        # --- Presets tab ---
        presets = ttk.Frame(notebook, padding=10)
        notebook.add(presets, text="Presets")
        presets.columnconfigure(0, weight=1)
        ttk.Label(
            presets,
            text="Click a slot to recall it. Use Save to store the current\n"
                 "pan, tilt and zoom into that slot.",
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.preset_buttons = {}
        for slot in range(1, 7):
            button = ttk.Button(
                presets, text=f"{slot}: empty", width=28,
                command=lambda s=slot: self.on_preset_recall(s),
            )
            button.grid(row=slot, column=0, sticky="ew", pady=2)
            ttk.Button(
                presets, text="Save", width=6, command=lambda s=slot: self.on_preset_save(s),
            ).grid(row=slot, column=1, padx=(6, 0))
            self.preset_buttons[slot] = button

        # --- Advanced tab ---
        advanced = ttk.Frame(notebook, padding=10)
        notebook.add(advanced, text="Advanced")
        advanced.columnconfigure(0, weight=1)
        ttk.Label(
            advanced,
            text="These write to the firmware's feature multiplexer.\n"
                 "The camera reports no state back, so these are fire-and-forget.",
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for index, name in enumerate(sorted(FEATURES), start=1):
            ttk.Label(advanced, text=name).grid(row=index, column=0, sticky="w", pady=2)
            buttons = ttk.Frame(advanced)
            buttons.grid(row=index, column=1, sticky="e")
            ttk.Button(
                buttons, text="On", width=5, command=lambda n=name: self.on_feature(n, True),
            ).pack(side="left", padx=2)
            ttk.Button(
                buttons, text="Off", width=5, command=lambda n=name: self.on_feature(n, False),
            ).pack(side="left", padx=2)

        info = ttk.LabelFrame(advanced, text="Device", padding=8)
        info.grid(row=len(FEATURES) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        details = [
            ("Model", self.cam.info.get("model", "")),
            ("Node", self.cam.node),
            ("USB", f"{self.cam.info.get('vid','')}:{self.cam.info.get('pid','')}"),
            ("Serial", self.cam.serial_number() if self.cam.has_extension_unit else "-"),
            ("Ext units", ", ".join(str(u) for u in sorted(set(self.cam.units.values()))) or "none"),
        ]
        for index, (key, value) in enumerate(details):
            ttk.Label(info, text=key, foreground="#666").grid(row=index, column=0, sticky="w", padx=(0, 10))
            ttk.Label(info, text=value).grid(row=index, column=1, sticky="w")

    def _add_slider(self, parent, row, label, cid, ctrl):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        value_label = ttk.Label(parent, text="-", width=6, anchor="e")
        value_label.grid(row=row, column=2, sticky="e")
        scale = ttk.Scale(
            parent, from_=ctrl.min, to=ctrl.max, orient="horizontal",
            command=lambda v, c=cid: self.on_slider(c, v),
        )
        scale.grid(row=row, column=1, sticky="ew", padx=8)
        self.sliders[cid] = (scale, value_label)

    def _build_statusbar(self):
        self.status = ttk.Label(self, text="", anchor="w", padding=(2, 6, 2, 0))
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew")

    def say(self, message):
        """Show a transient message; the position readout returns a few seconds later."""
        self._said_at = time.monotonic()
        self.status.configure(text=message)

    # ---------- camera state ----------

    def refresh_from_camera(self):
        self._suppress = True
        try:
            self.zoom_scale.set(self.cam.get_zoom())
            self.zoom_label.configure(text=f"{self.cam.get_zoom():.2f}x")
            for cid, (scale, label) in self.sliders.items():
                value = self.cam.dev.get(cid)
                scale.set(value)
                label.configure(text=str(value))
                ctrl = self.cam.dev.control(cid)
                state = "disabled" if (ctrl and ctrl.inactive) else "normal"
                scale.configure(state=state)
            if self.cam.dev.has(io.V4L2_CID_AUTO_WHITE_BALANCE):
                self.awb_var.set(bool(self.cam.dev.get(io.V4L2_CID_AUTO_WHITE_BALANCE)))
            if self.cam.dev.has(io.V4L2_CID_FOCUS_AUTO):
                self.af_var.set(bool(self.cam.dev.get(io.V4L2_CID_FOCUS_AUTO)))
            if self.cam.has_extension_unit:
                mode = self.cam.get_mode()
                if mode in MODES:
                    self.mode_var.set(mode)
                frame = self.cam.get_track_frame()
                if frame in TRACK_FRAMES:
                    self.frame_var.set(frame)
                self.gesture_var.set(bool(self.cam.get_gesture_mask()))
                self.noise_var.set(self.cam.get_noise_cancellation())
            self._refresh_presets()
        except OSError as exc:
            self.say(f"Could not read camera state: {exc}")
        finally:
            self._suppress = False

    def _refresh_presets(self):
        saved = self.cam.list_presets()
        for slot, button in self.preset_buttons.items():
            preset = saved.get(str(slot))
            if preset:
                button.configure(
                    text=f"{slot}:  pan {preset['pan']:+.0f}°  tilt {preset['tilt']:+.0f}°  {preset['zoom']:.1f}x"
                )
            else:
                button.configure(text=f"{slot}: empty")

    def _poll_position(self):
        try:
            pan, tilt, zoom = self.cam.get_pan(), self.cam.get_tilt(), self.cam.get_zoom()
            text = f"pan {pan:+.1f}°   tilt {tilt:+.1f}°   zoom {zoom:.2f}x"
            if self.cam.has_extension_unit:
                actual_tilt, actual_pan = self.cam.ptz_raw()
                text += f"      physical: pan {actual_pan/3600:+.1f}°  tilt {actual_tilt/3600:+.1f}°"
                if self.cam.is_parked():
                    text += "   — parked, turn on preview or open a video app to move it"
            # Let a transient message stand briefly, then go back to the readout.
            if time.monotonic() - self._said_at > 3.0:
                self._said_at = 0.0
                self.status.configure(text=text)
        except OSError:
            pass
        self.after(500, self._poll_position)

    # ---------- handlers ----------

    def _start_nudge(self, dx, dy):
        self._nudge(dx, dy)
        self._repeat_job = self.after(400, lambda: self._repeat_nudge(dx, dy))

    def _repeat_nudge(self, dx, dy):
        self._nudge(dx, dy)
        self._repeat_job = self.after(150, lambda: self._repeat_nudge(dx, dy))

    def _stop_nudge(self, _event=None):
        if self._repeat_job is not None:
            self.after_cancel(self._repeat_job)
            self._repeat_job = None

    def _nudge(self, dx, dy):
        step = self.step_var.get()
        try:
            self.cam.move(dx * step, dy * step)
        except OSError as exc:
            self.say(f"Move failed: {exc}")

    def do_refresh(self):
        self.cam.controls(refresh=True)  # re-read INACTIVE flags, not just values
        self.refresh_from_camera()
        self.say("Refreshed from camera")

    def do_center(self):
        try:
            self.cam.center()
            self.zoom_scale.set(1.0)
            self.say("Centred at 1x")
        except OSError as exc:
            self.say(f"Centre failed: {exc}")

    def on_zoom(self, value):
        if self._suppress:
            return
        factor = float(value)
        self.zoom_label.configure(text=f"{factor:.2f}x")
        try:
            self.cam.set_zoom(factor)
        except OSError as exc:
            self.say(f"Zoom failed: {exc}")

    def on_slider(self, cid, value):
        if self._suppress:
            return
        try:
            written = self.cam.dev.set(cid, float(value))
            self.sliders[cid][1].configure(text=str(written))
        except OSError as exc:
            self.say(f"Control write failed: {exc}")

    def on_awb(self):
        if self._suppress:
            return
        try:
            self.cam.dev.set(io.V4L2_CID_AUTO_WHITE_BALANCE, int(self.awb_var.get()))
            self.cam.controls(refresh=True)
            self.refresh_from_camera()
        except OSError as exc:
            self.say(f"White balance failed: {exc}")

    def on_af(self):
        if self._suppress:
            return
        try:
            self.cam.dev.set(io.V4L2_CID_FOCUS_AUTO, int(self.af_var.get()))
            self.cam.controls(refresh=True)
            self.refresh_from_camera()
        except OSError as exc:
            self.say(f"Autofocus failed: {exc}")

    def on_mode(self):
        if self._suppress:
            return
        try:
            self.cam.set_mode(self.mode_var.get())
            self.say(f"Mode: {self.mode_var.get()}")
        except Exception as exc:
            self.say(f"Mode change failed: {exc}")

    def on_frame(self):
        if self._suppress:
            return
        try:
            self.cam.set_track_frame(self.frame_var.get())
            self.say(f"Tracking framing: {self.frame_var.get()}")
        except Exception as exc:
            self.say(f"Framing change failed: {exc}")

    def on_gestures(self):
        if self._suppress:
            return
        try:
            self.cam.set_gestures(self.gesture_var.get())
            self.say(f"Gestures {'on' if self.gesture_var.get() else 'off'}")
        except Exception as exc:
            self.say(f"Gesture toggle failed: {exc}")

    def on_noise(self):
        if self._suppress:
            return
        try:
            self.cam.set_noise_cancellation(self.noise_var.get())
            self.say(f"Noise cancellation {'on' if self.noise_var.get() else 'off'}")
        except Exception as exc:
            self.say(f"Noise cancellation failed: {exc}")

    def on_feature(self, name, state):
        try:
            self.cam.set_feature(name, state)
            self.say(f"{name} -> {'on' if state else 'off'} (no readback available)")
        except Exception as exc:
            self.say(f"{name} failed: {exc}")

    def on_preset_save(self, slot):
        try:
            self.cam.save_preset(slot)
            self._refresh_presets()
            self.say(f"Saved preset {slot}")
        except OSError as exc:
            self.say(f"Save failed: {exc}")

    def on_preset_recall(self, slot):
        try:
            self.cam.recall_preset(slot)
            self._suppress = True
            self.zoom_scale.set(self.cam.get_zoom())
            self._suppress = False
            self.say(f"Recalled preset {slot}")
        except (OSError, ValueError) as exc:
            self.say(str(exc))

    def restore_defaults(self):
        for cid in list(self.sliders):
            ctrl = self.cam.dev.control(cid)
            if ctrl is not None and not ctrl.inactive:
                try:
                    self.cam.dev.set(cid, ctrl.default)
                except OSError:
                    pass
        self.refresh_from_camera()
        self.say("Image settings restored to defaults")

    # ---------- preview ----------

    def toggle_preview(self):
        if self.preview_var.get():
            self.preview = PreviewStream(self.cam.node)
            if not self.preview.start():
                self.say(f"Preview failed: {self.preview.error}")
                self.preview_var.set(False)
                self.preview = None
                return
            self.canvas.itemconfigure(self.placeholder, text="")
            self.say("Preview on -- the camera is now busy; turn it off before a call")
            self._pump_preview()
        else:
            if self.preview:
                self.preview.stop()
                self.preview = None
            self.photo = None
            self.canvas.delete("frame")
            self.canvas.itemconfigure(
                self.placeholder,
                text="Preview off\n\nThe camera can only stream to one app at a time,\n"
                     "so turn this off before joining a call.",
            )
            self.say("Preview off")

    def _pump_preview(self):
        if not self.preview_var.get() or self.preview is None:
            return
        try:
            frame = self.preview.frames.get_nowait()
        except queue.Empty:
            frame = None
        if frame is not None:
            try:
                self.photo = tk.PhotoImage(data=frame)
                self.canvas.delete("frame")
                self.canvas.create_image(0, 0, anchor="nw", image=self.photo, tags="frame")
            except tk.TclError as exc:
                self.say(f"Cannot display frame: {exc}")
                self.preview_var.set(False)
                self.toggle_preview()
                return
        elif self.preview.error:
            self.say(f"Preview stopped: {self.preview.error}")
            self.preview_var.set(False)
            self.toggle_preview()
            return
        self.after(33, self._pump_preview)

    def shutdown(self):
        if self.preview:
            self.preview.stop()


def main(argv=None):
    """Desktop entry point: report a missing camera in a dialog, not a traceback."""
    from .camera import CameraNotFound

    try:
        camera = LinkCamera()
    except (CameraNotFound, PermissionError, OSError) as exc:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox

        if isinstance(exc, PermissionError):
            detail = (
                "Permission denied opening the camera.\n\n"
                "Add your user to the 'video' group, or install the udev rule "
                "shipped with this project, then reconnect the camera."
            )
        else:
            detail = (
                "No Insta360 Link camera was found.\n\n"
                "Connect the camera and try again. If it is already connected, "
                "check that it appears in the output of: lsusb | grep 2e1a"
            )
        messagebox.showerror(APP_NAME, detail)
        root.destroy()
        return 1
    run(camera)
    return 0


def run(camera=None):
    owns_camera = camera is None
    if camera is None:
        camera = LinkCamera()
    root = tk.Tk()
    root.title(APP_NAME)
    root.minsize(880, 560)
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except tk.TclError:
        pass
    panel = ControlPanel(root, camera)

    def on_close():
        panel.shutdown()
        root.destroy()
        if owns_camera:
            camera.close()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
