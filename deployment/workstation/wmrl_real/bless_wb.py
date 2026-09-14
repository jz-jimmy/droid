"""Bless the CURRENT frozen white balance as the session-to-session reference.

Run this ONCE after a warmup whose colors you have visually approved (arm at
TASK RESET - the canonical warmup scene). It measures each camera's tint on a
fixed wood-table patch and writes camera_wb_reference.json; every future
RealSenseRig warmup then re-converges the AWB until the frozen tint matches
this reference (tolerance +-0.06 in R/G and B/G).

Usage (from the Sim2Real root, arm at task reset):
  PYTHONPATH=$PWD/wmrl conda run -n industreal-mujoco python -m wmrl.real.bless_wb
"""

from __future__ import annotations

import json
from pathlib import Path

from wmrl.real.cameras import RealSenseRig

# fixed wood-table patches (y0, y1, x0, x1) at capture resolution
# Statics only: their table patches are stable scenery. The wrist patch sees
# plate/gear/wood depending on the scene, so validating it against a stored
# tint produced false mismatches (2026-07-27); the D405's AWB has also been
# stable in practice.
PATCHES = {
    "left_static": (500, 700, 100, 500),     # 1280x720
    "right_static": (500, 650, 700, 1000),    # 1280x720
}
OUT = Path(__file__).with_name("camera_wb_reference.json")


def main() -> None:
    rig = RealSenseRig(size=None, require_all=True,
                       names=("wrist", "right_static", "left_static"),
                       resolutions={"left_static": (1280, 720),
                                    "right_static": (1280, 720)})
    for _ in range(8):
        frames = rig.read_raw()
    ref = {}
    for name, (y0, y1, x0, x1) in PATCHES.items():
        from wmrl.real.white_balance import make_reference
        ref[name] = make_reference(frames[name], (y0, y1, x0, x1), rig.serials[name])
        print(f"{name}: R/G {ref[name]['r_g']}  B/G {ref[name]['b_g']}")
    OUT.write_text(json.dumps(ref, indent=2))
    print(f"BLESSED -> {OUT}")
    for pipe in rig.pipelines.values():
        pipe.stop()


if __name__ == "__main__":
    main()


