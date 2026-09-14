"""Real-robot ACT rollout runner: cameras + bridge state -> TE policy -> joint targets.

Runs in the industreal-mujoco env (py3.11) and talks to the franka bridge (see
franka_bridge.py) over ZMQ. The policy executes under the standard eval contract
baked into current checkpoints: temporal ensembling over all 8 dims, one action per
30 Hz step.

Verbs:
  python -m wmrl.real.real_rollout --reset                       # arm to sim start, gripper open
  python -m wmrl.real.real_rollout --run --seconds 90 --tag t1 \
      --ckpt wmrl/runs/mujoco_gear/act_3cam_8dstate_gripfix_1m_v001/checkpoints/last/pretrained_model
  (Ctrl-C during --run stops streaming gracefully; the arm holds pose)

Recording (on by default, disable with --no-record) per rollout under --out/<tag>_<timestamp>/:
  wrist.mp4 right_static.mp4 left_static.mp4 panels.mp4   (3-panel = wrist|right|left, the
      same layout as the sim eval videos, for side-by-side sim/real comparison)
  rollout.npz: per-step q/dq/width/tcp_z (measured), action (raw 8D policy output),
      q_cmd (post-safety-filter command actually sent), flags, wall-clock t — enough to
      replay the real action stream in sim against the sim rollout.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
LEROBOT_SRC = REPO_ROOT.parent / "lerobot" / "src"
if LEROBOT_SRC.exists() and str(LEROBOT_SRC) not in sys.path:
    sys.path.insert(0, str(LEROBOT_SRC))

from wmrl.real import constants
from wmrl.real.bridge_client import BridgeClient
from wmrl.real.cameras import RealSenseRig


def load_policy(ckpt: str, device: str, temporal_ensemble: bool = True):
    import torch  # noqa: F401

    from lerobot.configs.policies import PreTrainedConfig
    from lerobot.policies import make_policy, make_pre_post_processors

    cfg = PreTrainedConfig.from_pretrained(ckpt)
    cfg.pretrained_path = ckpt
    if temporal_ensemble:
        # Standard eval contract: TE over all dims, one action per 30 Hz step.
        cfg.temporal_ensemble_coeff = 0.01
        cfg.n_action_steps = 1
    else:
        # Full-chunk open-loop execution (re-predict every chunk_size steps).
        cfg.temporal_ensemble_coeff = None
        cfg.n_action_steps = cfg.chunk_size
    cfg.device = device
    policy = _make_policy_env(cfg, ckpt)
    pre, post = make_pre_post_processors(
        policy_cfg=cfg, pretrained_path=ckpt,
        preprocessor_overrides={"device_processor": {"device": device}},
    )
    return policy, pre, post


def _make_policy_env(cfg, ckpt):
    """Instantiate via the env-config route so features come from the checkpoint itself."""
    from wmrl.lerobot.envs import WMRLMujocoGearEnvConfig
    from lerobot.policies import make_policy

    state_dim = cfg.input_features["observation.state"].shape[0]
    # Feature names come FROM THE CHECKPOINT: different training runs name the
    # cameras differently (sim-era ckpts observation.images.<cam>_rgb, the 0803
    # real-demo ckpts observation.images.<cam>), and a hardcoded env feature
    # set fails make_policy's consistency check for one or the other.
    features = dict(cfg.input_features)
    features.update(cfg.output_features)
    env_cfg = WMRLMujocoGearEnvConfig(legacy_state_9d=(state_dim == 9),
                                      features=features,
                                      features_map={k: k for k in features})
    policy = make_policy(cfg=cfg, env_cfg=env_cfg)
    policy.eval()
    return policy


def policy_camera_map(policy_cfg) -> dict[str, str]:
    """Map rig camera names -> the pixel keys this checkpoint was trained with
    (e.g. 'wrist' -> 'wrist_rgb' for sim-era ckpts, 'wrist' -> 'wrist' for the
    0803 real-demo ckpts). preprocess_observation prefixes them with
    'observation.images.'."""
    suffixes = [k.split("observation.images.", 1)[1]
                for k in policy_cfg.input_features if k.startswith("observation.images.")]
    out = {}
    for rig_name in ("wrist", "right_static", "left_static"):
        for cand in (rig_name, f"{rig_name}_rgb"):
            if cand in suffixes:
                out[rig_name] = cand
                break
        else:
            raise ValueError(f"checkpoint has no image feature for camera '{rig_name}' "
                             f"(features: {suffixes})")
    return out


def build_observation(frames: dict, state: np.ndarray, task: str):
    import torch

    from lerobot.envs.utils import preprocess_observation

    obs = {
        "pixels": {k: v[None] for k, v in frames.items()},
        "agent_pos": np.asarray(state, dtype=np.float32)[None],
    }
    o = preprocess_observation(obs)
    o["task"] = [task]
    return o


def run_rollout(args) -> None:
    import torch

    from lerobot.utils.constants import ACTION
    from wmrl.lerobot.features import TASK_DESCRIPTION
    from wmrl.utils.video_utils import numpy_video_to_mp4

    bridge = BridgeClient(args.bridge)
    print("[rollout] bridge:", bridge.ping())
    policy, pre, post = load_policy(args.ckpt, args.device, temporal_ensemble=args.te)
    state_dim = policy.config.input_features["observation.state"].shape[0]
    mode = ("TE all dims, n_action_steps=1" if args.te
            else f"TE DISABLED, full-chunk n_action_steps={policy.config.n_action_steps}")
    print(f"[rollout] policy loaded (state dim {state_dim}); {mode}")

    rig = RealSenseRig(size=constants.POLICY_IMAGE_SIZE)
    out_dir = None
    if args.record:
        out_dir = Path(args.out) / f"{args.tag}_{time.strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
    else:
        print("[rollout] recording DISABLED (--no-record)")

    n_steps = int(args.seconds * constants.CONTROL_HZ)
    dt = 1.0 / constants.CONTROL_HZ
    log = {"q": [], "dq": [], "width": [], "width_fb": [], "tcp_z": [], "action": [],
           "q_cmd": [], "flags": [], "t": []}
    videos = {name: [] for name in ("wrist", "right_static", "left_static")}
    # Virtual gripper width fed back to the policy. The real hand cannot track widths,
    # so the measured width gives the policy no confirmation of its own close ramp and
    # the reactive close loop deadlocks (policy waits for the fingers to follow). We
    # emulate the sim finger servo instead: integrate toward the policy's commanded
    # width at the demo slew rate. Once the real grasp event has physically settled,
    # switch to the measured width (real contact width == sim squeeze proprio).
    virtual_w = None
    W_SLEW = 0.005  # m per 30 Hz step, max close rate measured from the demo dataset

    bridge.start_stream()
    policy.reset()
    print(f"[rollout] running {n_steps} steps ({args.seconds:.0f}s). Ctrl-C to stop.")
    cam_map = policy_camera_map(policy.config)
    try:
        for step in range(n_steps):
            t0 = time.time()
            frames_rig = rig.read()
            frames = {cam_map[n]: f for n, f in frames_rig.items()}
            st = bridge.get_state()
            if virtual_w is None:
                virtual_w = float(st["width"])
            holding = (st.get("gripper_state") == "closed"
                       and st["width"] < constants.GRIPPER_OPEN_CMD_THRESH)
            fb_w = float(st["width"]) if holding else virtual_w
            state = np.asarray(st["q"] + [fb_w], dtype=np.float32)
            if state_dim == 9:  # legacy checkpoints only
                grip_norm = 1.0 - fb_w / constants.GRIPPER_MAX_WIDTH
                state = np.concatenate([state, [grip_norm]]).astype(np.float32)

            obs = build_observation(frames, state, TASK_DESCRIPTION)
            obs = pre(obs)
            with torch.no_grad():
                action = policy.select_action(obs)
            action = post(action)
            a = action.to("cpu").numpy().reshape(-1)

            reply = bridge.step(a[:7], width=float(a[7]))
            if reply.get("safety_flags"):
                print(f"[rollout] step {step}: safety {reply['safety_flags']}")
            # advance the virtual finger servo toward the (TE-ensembled) commanded width
            virtual_w += float(np.clip(a[7] - virtual_w, -W_SLEW, W_SLEW))
            virtual_w = float(np.clip(virtual_w, 0.0, constants.GRIPPER_MAX_WIDTH))

            if args.record:
                for name in videos:
                    videos[name].append(frames_rig[name])
                log["q"].append(st["q"])
                log["dq"].append(st["dq"])
                log["width"].append(st["width"])
                log["width_fb"].append(fb_w)
                log["tcp_z"].append(st["tcp_z"])
                log["action"].append(a.tolist())
                log["q_cmd"].append(reply.get("q_commanded", [np.nan] * 7))
                log["flags"].append(",".join(reply.get("safety_flags", [])))
                log["t"].append(t0)

            slack = dt - (time.time() - t0)
            if slack > 0:
                time.sleep(slack)
            elif step % 30 == 0 and slack < -0.005:
                print(f"[rollout] loop overrun {-slack*1000:.1f} ms at step {step}")
    except KeyboardInterrupt:
        print("\n[rollout] interrupted - stopping stream (arm holds pose)")
    finally:
        bridge.stop_stream()
        rig.close()

    if not args.record:
        print("[rollout] done (nothing recorded)")
        return
    print(f"[rollout] saving to {out_dir}")
    for name, frames_list in videos.items():
        if frames_list:
            numpy_video_to_mp4(np.stack(frames_list), out_dir / f"{name}.mp4",
                               fps=int(constants.CONTROL_HZ))
    if videos["wrist"]:
        panels = np.concatenate(
            [np.stack(videos["wrist"]), np.stack(videos["right_static"]),
             np.stack(videos["left_static"])], axis=2)
        numpy_video_to_mp4(panels, out_dir / "panels.mp4", fps=int(constants.CONTROL_HZ))
    np.savez_compressed(
        out_dir / "rollout.npz",
        **{k: np.asarray(v) for k, v in log.items()},
        start_arm_qpos=np.asarray(constants.START_ARM_QPOS),
        ckpt=str(args.ckpt),
    )
    print("[rollout] done")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", default=constants.BRIDGE_CONNECT)
    verbs = parser.add_mutually_exclusive_group(required=True)
    verbs.add_argument("--reset", action="store_true", help="Arm to sim start pose, gripper open")
    verbs.add_argument("--run", action="store_true", help="Run a policy rollout")
    verbs.add_argument("--stop", action="store_true", help="Stop any active stream (arm holds)")
    verbs.add_argument("--state", action="store_true", help="Print bridge state")
    parser.add_argument("--ckpt", default=None)
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--tag", default="rollout")
    parser.add_argument("--out", default="wmrl/runs/mujoco_gear/real_rollouts")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--te", action=argparse.BooleanOptionalAction, default=True,
                        help="Temporal ensembling (default on; --no-te = full-chunk open loop)")
    parser.add_argument("--record", action=argparse.BooleanOptionalAction, default=True,
                        help="Save the 3-panel/per-camera videos and the action-log npz "
                             "(--no-record to disable)")
    args = parser.parse_args()

    if args.reset:
        client = BridgeClient(args.bridge)
        print(client.ping())
        client.reset_to_start()
        print("reset done:", client.get_state())
    elif args.stop:
        client = BridgeClient(args.bridge)
        print(client.stop_stream())
    elif args.state:
        client = BridgeClient(args.bridge)
        print(client.get_state())
    elif args.run:
        if not args.ckpt:
            parser.error("--run requires --ckpt")
        run_rollout(args)


if __name__ == "__main__":
    main()
