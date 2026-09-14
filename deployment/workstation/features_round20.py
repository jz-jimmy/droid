"""Shared LeRobot feature schema and camera metadata for WMRL gear demos."""

from __future__ import annotations

from typing import Any

# 8D proprio: 7 arm joint positions + measured finger width. The old 9th element
# `gripper_ctrl_norm` (normalized gripper command state) was removed: it is a
# controller convention rather than measured proprioception, and in collected
# datasets it was almost perfectly anti-correlated with `finger_width`.
# Datasets/checkpoints collected with the old 9D state are incompatible.
PROPRIO_NAMES = (
    "arm_qpos_0",
    "arm_qpos_1",
    "arm_qpos_2",
    "arm_qpos_3",
    "arm_qpos_4",
    "arm_qpos_5",
    "arm_qpos_6",
    "finger_width",
)

ACTION_KIND = "joint_pos_plus_gripper_width"
JOINT_POSITION_CONTROLLER = "computed_torque"
JOINT_POSITION_KP = (300.0, 300.0, 300.0, 300.0, 300.0, 300.0, 300.0)
JOINT_POSITION_KD = (35.0, 35.0, 35.0, 35.0, 35.0, 35.0, 35.0)
JOINT_POSITION_TORQUE_CLAMP = 100.0

ACTION_NAMES = (
    "target_arm_qpos_0",
    "target_arm_qpos_1",
    "target_arm_qpos_2",
    "target_arm_qpos_3",
    "target_arm_qpos_4",
    "target_arm_qpos_5",
    "target_arm_qpos_6",
    "target_gripper_width",
)

TASK_DESCRIPTION = "pick up the medium gear, place it over the target shaft, and insert it until fully seated"

WRIST_CAMERA_CFG: dict[str, Any] = {
    "type": "fixed",
    "name": "wrist_rgb",
    "model": "Intel RealSense D405",
    "mount_body": "hand",
    "source_frame": "tcp_flange_link8",
    "note": "Calibrated wrist pose from sim_handover_new.yaml, converted from TCP/flange/link8 into the MuJoCo Panda hand body frame (hand origin == flange, so z passes through; xy/quat carry the 45 deg hand-mount rotation). Fixed 2026-07-10: earlier version wrongly subtracted the 0.107 m link7->flange offset from z. Round 16c (2026-08-11) TASK-LOCAL cam2hand: solved against the statics-consistent anchor at the task reset geometry (residual 5.6 -> 0.5 mm there); the wrist/statics disagreement is pose-dependent FK error, so no single rigid transform fits all geometries - this one is tuned for the task workspace (top-down, ~0.3-0.45 m). See docs/calibration/2026-08-11_round16.",
    "resolution": {"width": 848, "height": 480},
    "intrinsics": {"fx": 434.0816345214844, "fy": 433.4703063964844, "cx": 421.857421875, "cy": 242.11648559570312},
    # Round 19c (2026-08-22): task-local cam2hand re-solved after the wrist mount
    # was disturbed by USB reseating (measured 6.5 mm offset at task geometry;
    # shift [2.8, -3.3, -3.0] mm / 0.77 deg). Previous (round 16c, 2026-08-11):
    # pos_hand [0.060154897387885334, -0.007103878722188401, 0.06046294266838584],
    # quat [0.011917685534148062, -0.7216926472742473, -0.6921050288156666, -0.0028845675349047483].
    "pos_hand": [0.06445880438838325, -0.00744230818425292, 0.05751136972087756],
    "quat_wxyz_hand": [0.010844436099566923, -0.7262043637317354, -0.6873832442452931, -0.0037277115612381635],
    "fovy": 57.944068,
    "shadows": False,
    "frame_convention": "MuJoCo/OpenGL camera frame: +x image-right, +y image-up, -z optical axis",
}

RIGHT_STATIC_CAMERA_CFG: dict[str, Any] = {
    "type": "fixed",
    "name": "right_static_rgb",
    "model": "Intel RealSense D455",
    # Round 17 (2026-08-21, new room): sides swapped in the remount - right is now
    # 338122303713. Previous (round 16, old room): serial 235422302222,
    # pos [0.6798154455, -0.2593796060, 0.0850624010],
    # quat [0.8566248505, 0.4306610583, 0.1628289207, 0.2328339772], fovy 59.21323370082117.
    "serial": "338122303713",
    "mount": "world_static",
    "coordinate_frame": "robot_base: +x forward, +y robot-left, +z up; right side is negative y",
    "resolution": {"width": 848, "height": 480},
    "intrinsics": {"fx": 428.6227111816406, "fy": 427.96759033203125, "cx": 421.3064880371094, "cy": 246.91749572753906},
    "fov_deg": {"horizontal": 89.37871876517899, "vertical": 58.56655611727773},
    # Round 19 (2026-08-22): re-measured, moved 1.1 mm / 0.105 deg vs round 18
    # (not physically touched). Previous (round 18, 2026-08-21):
    # pos [0.3936252558, -0.3225158146, 0.1105560215],
    # quat [0.9035396925, 0.3285187039, 0.0059456323, -0.2750566027].
    # Round 20 (2026-08-29): re-solved after physical re-aim. Previous (round 19):
    # pos [0.3928608346, -0.3217022986, 0.1107950948],
    # quat [0.9037788881, 0.3279355142, 0.0052807531, -0.2749802421].
    "pos_world": [0.3506592847, -0.29340958, 0.2007184616],
    "quat_wxyz_world": [0.9207292288, 0.2634713513, -0.040805008, -0.2849131194],
    "fovy": 58.56655611727773,
    "shadows": False,
    "principal_point_note": "MuJoCo pinhole fixed cameras assume a centered principal point; cx/cy offsets are stored as metadata only.",
    "frame_convention": "MuJoCo/OpenGL camera frame: +x image-right, +y image-up, -z optical axis",
}

LEFT_STATIC_CAMERA_CFG: dict[str, Any] = {
    "type": "fixed",
    "name": "left_static_rgb",
    "model": "Intel RealSense D455",
    # Round 17 (2026-08-21, new room): sides swapped in the remount - left is now
    # 235422302222. Previous (round 16, old room): serial 338122303713,
    # pos [0.7609268762, 0.2211128683, 0.1041024659],
    # quat [0.4381493080, 0.1326077259, 0.3235106256, 0.8281191038], fovy 58.56655611727771.
    "serial": "235422302222",
    "mount": "world_static",
    "coordinate_frame": "robot_base: +x forward, +y robot-left, +z up; left side is positive y",
    "resolution": {"width": 848, "height": 480},
    "intrinsics": {"fx": 422.9227294921875, "fy": 422.3628234863281, "cx": 429.3864440917969, "cy": 242.79046630859375},
    "fov_deg": {"horizontal": 90.14575832730475, "vertical": 59.21323370082117},
    # Round 19 (2026-08-22): left camera re-aimed again (+44.8 mm / 10.8 deg vs
    # round 18). Previous (round 18, 2026-08-21):
    # pos [0.2882859635, 0.2955032130, 0.1640242370],
    # quat [0.3353810802, 0.0856138488, -0.2932981464, -0.8911599168].
    # (Round 17: pos [0.2875894975, 0.2805682025, 0.1414027814],
    # quat [0.3218174549, 0.0700580686, -0.2500825456, -0.9104856469].)
    # Round 20 (2026-08-29): re-solved after physical re-aim. Previous (round 19):
    # pos [0.3055045439, 0.3250391808, 0.1929339000],
    # quat [0.3891530856, 0.1191415304, -0.3453632233, -0.8456295972].
    "pos_world": [0.2863702681, 0.2715920743, 0.1620988534],
    "quat_wxyz_world": [0.405590218, 0.164336453, -0.3267467758, -0.8376912616],
    "fovy": 59.21323370082117,
    "shadows": False,
    "principal_point_note": "MuJoCo pinhole fixed cameras assume a centered principal point; cx/cy offsets are stored as metadata only.",
    "frame_convention": "MuJoCo/OpenGL camera frame: +x image-right, +y image-up, -z optical axis",
}


def parse_camera_names(camera: list[str] | tuple[str, ...] | str | None) -> list[str]:
    items: list[str]
    if camera is None:
        items = ["wrist"]
    elif isinstance(camera, str):
        items = [camera]
    else:
        items = [str(x) for x in camera]
    names = [name.strip() for item in items for name in item.split(",") if name.strip()]
    if not names:
        names = ["wrist"]
    out: list[str] = []
    for name in names:
        canonical = {
            "static_right": "right_static",
            "static_left": "left_static",
        }.get(name, name)
        if canonical not in out:
            out.append(canonical)
    return out


def camera_cfgs(camera: list[str] | tuple[str, ...] | str | None, default_cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in parse_camera_names(camera):
        if name == "wrist":
            out[name] = dict(WRIST_CAMERA_CFG)
        elif name == "right_static":
            out[name] = dict(RIGHT_STATIC_CAMERA_CFG)
        elif name == "left_static":
            out[name] = dict(LEFT_STATIC_CAMERA_CFG)
        elif name == "front" and default_cfg is not None:
            out[name] = dict(default_cfg)
        elif name == "overhead" and default_cfg is not None:
            cfg = dict(default_cfg)
            cfg.update({"target": "shaft", "azimuth": 90.0, "elevation": -70.0, "distance": 0.55})
            out[name] = cfg
        elif name == "side" and default_cfg is not None:
            cfg = dict(default_cfg)
            cfg.update({"target": "gear_tcp_mid", "azimuth": 0.0, "elevation": -25.0, "distance": 0.42})
            out[name] = cfg
        elif name in ("third_person", "third") and default_cfg is not None:
            cfg = dict(default_cfg)
            cfg.update(
                {
                    "target": "fixed",
                    "lookat": [0.45, 0.0, 0.12],
                    "azimuth": -135.0,
                    "elevation": -22.0,
                    "distance": 1.05,
                    "shadows": False,
                }
            )
            out[name] = cfg
        else:
            raise ValueError(
                "Unknown camera preset "
                f"'{name}'. Use wrist, right_static, left_static, front, overhead, side, or third_person."
            )
    return out


def lerobot_dataset_features(camera_names: list[str], image_height: int, image_width: int) -> dict[str, dict[str, Any]]:
    features: dict[str, dict[str, Any]] = {}
    for name in camera_names:
        features[f"observation.images.{name}"] = {
            "dtype": "video",
            "shape": (int(image_height), int(image_width), 3),
            "names": ["height", "width", "channel"],
        }
    features["observation.state"] = {
        "dtype": "float32",
        "shape": (len(PROPRIO_NAMES),),
        "names": list(PROPRIO_NAMES),
    }
    features["action"] = {
        "dtype": "float32",
        "shape": (len(ACTION_NAMES),),
        "names": list(ACTION_NAMES),
    }
    return features



def joint_controller_metadata(unwrapped: Any) -> dict[str, Any]:
    """Return the low-level servo contract used for LeRobot joint-position actions."""
    return {
        "action_kind": ACTION_KIND,
        "joint_position_controller": str(getattr(unwrapped, "joint_position_controller", "")),
        "joint_position_kp": jsonable(getattr(unwrapped, "joint_position_kp", ())),
        "joint_position_kd": jsonable(getattr(unwrapped, "joint_position_kd", ())),
        "joint_position_torque_clamp": float(getattr(unwrapped, "joint_position_torque_clamp", JOINT_POSITION_TORQUE_CLAMP)),
        "joint_position_gravity_compensation": bool(getattr(unwrapped, "joint_position_gravity_comp", True)),
    }


def validate_joint_position_controller(unwrapped: Any, *, context: str = "WMRL LeRobot env") -> None:
    """Fail fast if LeRobot joint-position actions would be replayed with the old unstable servo."""
    if str(getattr(unwrapped, "action_representation", "")).lower() != ACTION_KIND:
        return
    if not bool(getattr(unwrapped, "joint_position_action", False)):
        raise RuntimeError(f"{context}: action representation is {ACTION_KIND}, but joint_position_action is false")
    controller = str(getattr(unwrapped, "joint_position_controller", "")).lower()
    if controller != JOINT_POSITION_CONTROLLER:
        raise RuntimeError(
            f"{context}: {ACTION_KIND} must use joint_position_controller={JOINT_POSITION_CONTROLLER!r}; "
            f"got {controller!r}. The raw PD torque servo was unstable during action replay."
        )
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"{context}: numpy is required to validate joint-position controller gains") from exc
    kp = np.asarray(getattr(unwrapped, "joint_position_kp", ()), dtype=np.float64).reshape(-1)
    kd = np.asarray(getattr(unwrapped, "joint_position_kd", ()), dtype=np.float64).reshape(-1)
    if kp.shape != (7,) or kd.shape != (7,):
        raise RuntimeError(f"{context}: joint-position gains must both be 7D; got kp={kp.shape}, kd={kd.shape}")
    if not np.allclose(kp, np.asarray(JOINT_POSITION_KP, dtype=np.float64)):
        raise RuntimeError(f"{context}: unexpected joint_position_kp={kp.tolist()}, expected {list(JOINT_POSITION_KP)}")
    if not np.allclose(kd, np.asarray(JOINT_POSITION_KD, dtype=np.float64)):
        raise RuntimeError(f"{context}: unexpected joint_position_kd={kd.tolist()}, expected {list(JOINT_POSITION_KD)}")


def jsonable(value: Any) -> Any:
    try:
        import numpy as np
    except Exception:  # pragma: no cover
        np = None
    if np is not None:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
