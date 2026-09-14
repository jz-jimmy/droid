"""RealSense camera access for real rollouts and data collection.

Two consumption modes:
  - read():     square crops centered on the principal point (+ optional resize),
                geometrically matching the sim's centered square pinhole render.
  - read_raw(): full-FOV frames at the configured resolution (e.g. 1280x720 for
                high-res collection); per-camera device intrinsics are exposed via
                .intrinsics / .K() for metadata and projection.

The principal-point crop uses the DEVICE-reported intrinsics at the configured
resolution (the constants store the 848x480 values for reference/config only).
"""

from __future__ import annotations

import time as time_module

import numpy as np

from wmrl.real import constants


def crop_square(img: np.ndarray, principal: tuple[float, float] | None) -> np.ndarray:
    """Square crop with the principal point moved EXACTLY to the image center
    (subpixel x + y translation, replicated border). Sim renders use a centered
    principal point; the device cy sits up to ~7 px off center (left D455), so a
    plain crop leaves a constant sim-vs-real shift no extrinsics can fix."""
    h, w = img.shape[:2]
    side = h
    cx = w / 2.0 if principal is None else float(principal[0])
    cy = h / 2.0 if principal is None else float(principal[1])
    x0 = int(round(np.clip(cx - side / 2.0, 0, w - side)))
    out = img[:, x0:x0 + side]
    dx = (x0 + side / 2.0) - cx   # leftover subpixel/clipped x shift
    dy = side / 2.0 - cy
    if abs(dx) >= 0.05 or abs(dy) >= 0.05:
        import cv2
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        out = cv2.warpAffine(out, M, (side, side), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    return out


class RealSenseRig:
    """Owns the policy cameras. RGB uint8 frames, serial-addressed."""

    def __init__(self, size: int | None = None, require_all: bool = True,
                 names: tuple[str, ...] = ("wrist", "right_static", "left_static"),
                 width: int | None = None, height: int | None = None,
                 fps: int | None = None,
                 resolutions: dict[str, tuple[int, int]] | None = None):
        import pyrealsense2 as rs

        self.rs = rs
        self.size = size  # None: keep native square; else resize (e.g. 224)
        self.width = int(width or constants.CAM_WIDTH)
        self.height = int(height or constants.CAM_HEIGHT)
        self.fps = int(fps or constants.CAM_FPS)
        # Per-camera resolution overrides. NOTE: the wrist D405 advertises but cannot
        # actually stream 1280x720 color (firmware limitation, verified 2026-07-12);
        # it must stay at 848x480. High-res collection uses statics at 1280x720.
        self.resolutions = dict(resolutions or {})
        ctx = rs.context()
        devices = {
            d.get_info(rs.camera_info.serial_number): d.get_info(rs.camera_info.name)
            for d in ctx.query_devices()
        }
        print(f"[cameras] detected: {devices} @ {self.width}x{self.height}")
        wrist_serial = next(
            (s for s, name in devices.items() if constants.WRIST_MODEL in name), None
        )
        full_serials = {
            "wrist": wrist_serial,
            "right_static": constants.RIGHT_STATIC_SERIAL,
            "left_static": constants.LEFT_STATIC_SERIAL,
        }
        self.serials = {k: v for k, v in full_serials.items() if k in names}
        self.pipelines: dict[str, object] = {}
        self.intrinsics: dict[str, dict] = {}
        for name, serial in self.serials.items():
            if serial is None or serial not in devices:
                msg = f"[cameras] '{name}' (serial {serial}) not connected"
                if require_all:
                    raise RuntimeError(msg)
                print(msg + " - skipping")
                continue
            self._start_pipeline(name, serial)
        # Warmup for auto-exposure. First frames after start can take several seconds
        # (USB negotiation); retry once with a device restart on persistent timeout.
        for attempt in range(2):
            try:
                for _ in range(15):
                    self._grab_raw(timeout_ms=7000)
                self._restore_auto_white_balance()
                break
            except RuntimeError as exc:
                print(f"[cameras] warmup attempt {attempt} failed ({exc}); hardware-resetting + restarting")
                if attempt == 1:
                    raise
                # A camera can come up wedged from a previous session (esp. the D405 at
                # 1280x720) and only a hardware reset recovers it (verified 2026-07-12).
                for name in list(self.pipelines):
                    try:
                        self.pipelines[name].stop()
                    except RuntimeError:
                        pass
                for d in rs.context().query_devices():
                    if d.get_info(rs.camera_info.serial_number) in self.serials.values():
                        try:
                            d.hardware_reset()
                        except Exception:
                            pass
                time_module.sleep(8.0)
                for name in list(self.pipelines):
                    self._start_pipeline(name, self.serials[name])

    def _restore_auto_white_balance(self) -> None:
        """Photometric lockdown (2026-07-27, for real ACT demo collection):
        let the autos converge on the shared scene, then FREEZE everything so
        the three cameras stop drifting apart mid-episode:
          1. auto WB + auto exposure ON, 3 s converge;
          2. disable auto WB (freeze - never WRITE the white_balance register
             on this D455 firmware, it corrupts the ISP);
          3. disable auto exposure, force GAIN to the sensor minimum, and set
             a fixed EXPOSURE from camera_manual.json (tuned interactively via
             wmrl.real.tune_exposure; falls back to the converged value).
        camera_manual.json: {"<cam>": {"exposure": float, "gain": float}}."""
        import json as _json
        import time as _t
        from pathlib import Path as _Path

        cfg_path = _Path(__file__).with_name("camera_manual.json")
        manual = {}
        if cfg_path.exists():
            manual = _json.loads(cfg_path.read_text())

        for name, pipe in self.pipelines.items():
            try:
                dev = pipe.get_active_profile().get_device()
                for sensor in dev.query_sensors():
                    if sensor.supports(self.rs.option.enable_auto_white_balance):
                        sensor.set_option(self.rs.option.enable_auto_white_balance, 1)
                    if sensor.supports(self.rs.option.enable_auto_exposure):
                        sensor.set_option(self.rs.option.enable_auto_exposure, 1)
            except RuntimeError as exc:
                print(f"[cameras] {name}: auto enable failed ({exc})")
        deadline = _t.time() + 3.0   # convergence beyond the 15 warmup frames
        while _t.time() < deadline:
            self._grab_raw(timeout_ms=7000)
        ref_path = _Path(__file__).with_name("camera_wb_reference.json")
        wb_ref = _json.loads(ref_path.read_text()) if ref_path.exists() else {}

        def _freeze_all():
            for name, pipe in self.pipelines.items():
                dev = pipe.get_active_profile().get_device()
                for sensor in dev.query_sensors():
                    if not sensor.supports(self.rs.option.enable_auto_white_balance):
                        continue
                    sensor.set_option(self.rs.option.enable_auto_white_balance, 0)
                    if sensor.supports(self.rs.option.enable_auto_exposure):
                        sensor.set_option(self.rs.option.enable_auto_exposure, 0)
                    m = manual.get(name, {})
                    if sensor.supports(self.rs.option.gain):
                        g = m.get("gain", sensor.get_option_range(self.rs.option.gain).min)
                        sensor.set_option(self.rs.option.gain, float(g))
                    exp = m.get("exposure")
                    if exp is not None and sensor.supports(self.rs.option.exposure):
                        sensor.set_option(self.rs.option.exposure, float(exp))
                    cur_exp = (sensor.get_option(self.rs.option.exposure)
                               if sensor.supports(self.rs.option.exposure) else -1)
                    cur_gain = (sensor.get_option(self.rs.option.gain)
                                if sensor.supports(self.rs.option.gain) else -1)
                    self.intrinsics[name]["white_balance"] = "frozen_manual"
                    self.intrinsics[name]["exposure"] = float(cur_exp)
                    self.intrinsics[name]["gain"] = float(cur_gain)
                    print(f"[cameras] {name}: autos frozen; exposure {cur_exp:.0f}, gain {cur_gain:.0f}")

        def _reconverge(names):
            for name in names:
                dev = self.pipelines[name].get_active_profile().get_device()
                for sensor in dev.query_sensors():
                    if sensor.supports(self.rs.option.enable_auto_white_balance):
                        sensor.set_option(self.rs.option.enable_auto_white_balance, 1)
            deadline = _t.time() + 2.5
            while _t.time() < deadline:
                self._grab_raw(timeout_ms=7000)

        # Compare settled frames, including the final retry. A missing/invalid
        # reference is never a success and cannot be repaired by more AWB retries.
        from wmrl.real.white_balance import validate_reference
        self.wb_status = {}
        try:
            _freeze_all()
            for attempt in range(5):
                for _ in range(8):
                    frames = self._grab_raw(timeout_ms=7000)
                bad = []
                for name in self.pipelines:
                    if name == "wrist":
                        continue
                    ref = wb_ref.get(name)
                    if ref is None:
                        self.wb_status[name] = "reference missing; refresh at task reset"
                        continue
                    try:
                        ok, rg, bg = validate_reference(frames[name], ref, self.serials[name])
                    except (ValueError, KeyError, TypeError) as exc:
                        self.wb_status[name] = str(exc)
                        continue
                    self.wb_status[name] = "valid" if ok else f"off-reference R/G={rg:.4f} B/G={bg:.4f}"
                    if not ok:
                        bad.append(name)
                if not bad or attempt == 4:
                    break
                print(f"[cameras] WB off-reference {bad}; re-converging ({attempt+1}/4)")
                _reconverge(bad)
                _freeze_all()
            if self.wb_status and all(v == "valid" for v in self.wb_status.values()):
                print("[cameras] WB validated against reference")
            else:
                print(f"[cameras] WB needs review: {self.wb_status}")
        except RuntimeError as exc:
            self.wb_status = {name: f"manual freeze failed: {exc}" for name in self.pipelines}
            print(f"[cameras] manual freeze failed ({exc})")

    def _start_pipeline(self, name: str, serial: str) -> None:
        rs = self.rs
        w, h = self.resolutions.get(name, (self.width, self.height))
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_device(serial)
        cfg.enable_stream(rs.stream.color, int(w), int(h), rs.format.rgb8, self.fps)
        profile = pipe.start(cfg)
        intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
        self.intrinsics[name] = {
            "fx": float(intr.fx), "fy": float(intr.fy),
            "cx": float(intr.ppx), "cy": float(intr.ppy),
            "width": int(intr.width), "height": int(intr.height),
            "model": str(intr.model), "coeffs": [float(c) for c in intr.coeffs],
            "serial": serial,
        }
        self.pipelines[name] = pipe

    def K(self, name: str) -> np.ndarray:
        i = self.intrinsics[name]
        return np.array([[i["fx"], 0.0, i["cx"]],
                         [0.0, i["fy"], i["cy"]],
                         [0.0, 0.0, 1.0]])

    def _principal(self, name: str) -> tuple[float, float]:
        i = self.intrinsics[name]
        return (i["cx"], i["cy"])

    def _grab_raw(self, timeout_ms: int = 2000) -> dict[str, np.ndarray]:
        frames = {}
        for name, pipe in self.pipelines.items():
            fs = pipe.wait_for_frames(timeout_ms=timeout_ms)
            frames[name] = np.asanyarray(fs.get_color_frame().get_data())
        return frames

    def read_raw(self) -> dict[str, np.ndarray]:
        """Latest full-FOV RGB frames at the configured resolution."""
        return self._grab_raw()

    def read(self) -> dict[str, np.ndarray]:
        """Latest RGB frames, square-cropped on the device principal point (and
        resized to self.size if set)."""
        import cv2

        out = {}
        for name, raw in self._grab_raw().items():
            img = crop_square(raw, self._principal(name))
            if self.size is not None and img.shape[0] != self.size:
                img = cv2.resize(img, (self.size, self.size), interpolation=cv2.INTER_AREA)
            out[name] = img
        return out

    def close(self) -> None:
        for pipe in self.pipelines.values():
            pipe.stop()


class FrameGrabber:
    """Threaded latest-frame buffer so full-res captures never block a control loop."""

    def __init__(self, rig: RealSenseRig, raw: bool = True):
        import threading

        self.rig = rig
        self.raw = raw
        self._lock = threading.Lock()
        self._latest: dict[str, np.ndarray] | None = None
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            try:
                frames = self.rig.read_raw() if self.raw else self.rig.read()
            except RuntimeError:
                continue
            with self._lock:
                self._latest = frames

    def latest(self) -> dict[str, np.ndarray] | None:
        with self._lock:
            return None if self._latest is None else dict(self._latest)

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=2.0)
