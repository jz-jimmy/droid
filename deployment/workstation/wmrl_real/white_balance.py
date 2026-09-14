"""Validate reference patches without accepting empty, stale, or dark samples."""
from __future__ import annotations

import numpy as np


def patch_ratios(frame, patch):
    y0, y1, x0, x1 = map(int, patch)
    h, w = frame.shape[:2]
    if not (0 <= y0 < y1 <= h and 0 <= x0 < x1 <= w):
        raise ValueError(f"patch {patch} outside {w}x{h} image")
    rgb = np.asarray(frame[y0:y1, x0:x1], dtype=np.float64).reshape(-1, 3).mean(0)
    if not np.isfinite(rgb).all() or rgb[1] < 5:
        raise ValueError("reference patch is nonfinite or too dark")
    return float(rgb[0] / rgb[1]), float(rgb[2] / rgb[1])


def validate_reference(frame, ref, serial):
    if ref.get("serial") != serial or ref.get("pose") != "task_reset":
        raise ValueError("legacy/stale reference: refresh at task reset with the current camera view")
    h, w = frame.shape[:2]
    if ref.get("resolution") != [w, h]:
        raise ValueError(f"reference resolution {ref.get('resolution')} does not match {w}x{h}")
    rg, bg = patch_ratios(frame, ref["patch"])
    targets = np.asarray([ref["r_g"], ref["b_g"], ref.get("tol", 0.06)])
    if not np.isfinite(targets).all() or targets[2] <= 0:
        raise ValueError("invalid reference ratios/tolerance")
    return max(abs(rg - targets[0]), abs(bg - targets[1])) <= targets[2], rg, bg


def make_reference(frame, patch, serial):
    rg, bg = patch_ratios(frame, patch)
    h, w = frame.shape[:2]
    return {"patch": list(patch), "resolution": [w, h], "serial": serial,
            "pose": "task_reset", "r_g": round(rg, 4), "b_g": round(bg, 4), "tol": 0.06}
