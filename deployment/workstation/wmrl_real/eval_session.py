"""Gated real-robot POLICY EVALUATION session: overlay -> rollout -> log.

The eval twin of scripted_session's collection gate. Per seed:
  1. the standard 9-tile sim/real overlay opens (sim | real | blend x 3 cams)
     so you can place the gear for the seed; [c]/ENTER starts the rollout,
     [n]/[p] move a seed forward/back, [j] re-blesses WB, [q] quits;
  2. the checkpoint policy runs for --seconds (recording videos + rollout.npz
     under --out/<ckpt-tag>_s<seed>_<time>), then the arm task-resets;
  3. you grade the episode (furthest stage reached, 0-4 + note) and choose
     [k] log & next seed / [d] discard & redo this seed / [q] log & quit.

Grades append to the markdown eval log (wmrl.real.eval_log format: hand-
editable table + auto summary). The policy is loaded once for the session.

Usage (industreal-mujoco env, from the Sim2Real root, bridge live):
  PYTHONPATH=$PWD/lerobot/src:$PWD/wmrl:$PWD/industreal-mujoco/src \
    conda run --no-capture-output -n industreal-mujoco \
    python -m wmrl.real.eval_session \
    --ckpt wmrl/external/pretrained_model_0803_12ep --ckpt-tag 12ep \
    --file wmrl/eval-logs/real_eval_0804.md --seed 1000000
"""

from __future__ import annotations

import os

# BEFORE any mujoco import (the overlay gate renders sim offscreen; GLX is
# unreliable in every shell this runs from - EGL is).
os.environ.setdefault("MUJOCO_GL", "egl")

import argparse
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from wmrl.real import constants
from wmrl.real.eval_log import REACHED_CHOICES, STAGES, parse_rows, write_file

CAPTURE_WH = (1280, 720)


def _policy_views(frames_raw: dict, rig) -> dict:
    """Reproduce RealSenseRig.read(size=POLICY_IMAGE_SIZE) from raw frames."""
    import cv2

    from wmrl.real.cameras import crop_square

    out = {}
    for name, raw in frames_raw.items():
        intr = rig.intrinsics[name]
        img = crop_square(raw, (intr["cx"], intr["cy"]))
        if img.shape[0] != constants.POLICY_IMAGE_SIZE:
            img = cv2.resize(img, (constants.POLICY_IMAGE_SIZE, constants.POLICY_IMAGE_SIZE),
                             interpolation=cv2.INTER_AREA)
        out[name] = img
    return out


def run_policy_episode(args, seed, bridge, rig, grabber, policy_bundle) -> Path:
    import torch

    from wmrl.lerobot.features import TASK_DESCRIPTION
    from wmrl.real.real_rollout import build_observation, policy_camera_map
    from wmrl.utils.video_utils import numpy_video_to_mp4

    policy, pre, post = policy_bundle
    cam_map = policy_camera_map(policy.config)
    state_dim = policy.config.input_features["observation.state"].shape[0]
    out_dir = Path(args.out) / f"{args.ckpt_tag}_s{seed}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    n_steps = int(args.seconds * constants.CONTROL_HZ)
    dt = 1.0 / constants.CONTROL_HZ
    log = {"q": [], "width": [], "width_fb": [], "action": [], "flags": [], "t": [], "q_commanded": [], "gripper_state": []}
    videos = {name: [] for name in ("wrist", "right_static", "left_static")}
    virtual_w = None
    W_SLEW = 0.005

    bridge.start_stream()
    policy.reset()
    print(f"[eval] rolling out {args.ckpt_tag} on seed {seed} "
          f"({args.seconds:.0f}s, Ctrl-C stops early)")
    try:
        for step in range(n_steps):
            t0 = time.time()
            frames_rig = _policy_views(grabber.latest(), rig)
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

            obs = pre(build_observation(frames, state, TASK_DESCRIPTION))
            with torch.no_grad():
                action = policy.select_action(obs)
            a = post(action).to("cpu").numpy().reshape(-1)

            reply = bridge.step(a[:7], width=float(a[7]))
            if reply.get("safety_flags"):
                print(f"[eval] step {step}: safety {reply['safety_flags']}")
            virtual_w += float(np.clip(a[7] - virtual_w, -W_SLEW, W_SLEW))
            virtual_w = float(np.clip(virtual_w, 0.0, constants.GRIPPER_MAX_WIDTH))

            if args.video:
                for name in videos:
                    videos[name].append(frames_rig[name])
            log["q"].append(st["q"])
            log["q_commanded"].append(reply["q_commanded"])
            log["gripper_state"].append(reply.get("gripper_state", "unknown"))
            log["width"].append(st["width"])
            log["width_fb"].append(fb_w)
            log["action"].append(a.tolist())
            log["flags"].append(",".join(reply.get("safety_flags", [])))
            log["t"].append(t0)

            slack = dt - (time.time() - t0)
            if slack > 0:
                time.sleep(slack)
    except KeyboardInterrupt:
        print("\n[eval] rollout interrupted early")
    finally:
        try:
            bridge.stop_stream()
        except Exception as exc:  # noqa: BLE001
            print(f"[eval] stop_stream failed: {exc}")

        # Post-episode gripper release + return-home. reset_to_start() is blocking
        # (~12 s of joint moves + gripper open), so by default we run it in a
        # background thread and keep appending camera frames to the videos while it
        # executes. That way the mp4 contains the full aftermath -- the release and
        # the return home -- even when the policy timed out still holding the gear,
        # which is exactly the behaviour we want to review. --no-record-return falls
        # back to the plain blocking reset (video ends at the last policy step).
        def _post_reset():
            try:
                bridge.open_gripper()
                bridge.reset_to_start()
            except Exception as exc:  # noqa: BLE001
                print(f"[eval] post-episode reset failed: {exc}")

        if args.video and getattr(args, "record_return", True):
            th = threading.Thread(target=_post_reset, daemon=True)
            th.start()
            # Cap the tail so a wedged reset can't record forever (reset budget is
            # ~14 s; 30 s leaves ample margin).
            tail_deadline = time.time() + 30.0
            while th.is_alive() and time.time() < tail_deadline:
                t0 = time.time()
                raw = grabber.latest()
                if raw is not None:
                    fr = _policy_views(raw, rig)
                    for name in videos:
                        videos[name].append(fr[name])
                slack = dt - (time.time() - t0)
                if slack > 0:
                    time.sleep(slack)
            th.join(timeout=5.0)
        else:
            _post_reset()

    if args.video:
        for name, fl in videos.items():
            if fl:
                numpy_video_to_mp4(np.stack(fl), out_dir / f"{name}.mp4",
                                   fps=int(constants.CONTROL_HZ))
    np.savez_compressed(out_dir / "rollout.npz",
                        **{k: np.asarray(v) for k, v in log.items()},
                        ckpt=str(args.ckpt), seed=seed, temporal_ensemble=bool(args.te),
                        n_action_steps=policy.config.n_action_steps,
                        camera_wb_status=str(getattr(rig, "wb_status", {})))
    print(f"[eval] saved {out_dir}")
    return out_dir


def _grade() -> tuple[str, str]:
    opts = "  ".join(f"[{i}] {n}" for i, n in enumerate(REACHED_CHOICES))
    while True:
        ans = input(f"[eval] furthest stage reached — {opts} > ").strip().lower()
        if ans in [str(i) for i in range(len(REACHED_CHOICES))]:
            reached = REACHED_CHOICES[int(ans)]
            break
        if ans in REACHED_CHOICES:
            reached = ans
            break
        print("  enter 0-4 or a stage name")
    note = input("[eval] note (enter for none) > ").strip()
    return reached, note


def _log_row(args, seed, reached, note) -> None:
    path = Path(args.file)
    rows = parse_rows(path)
    k = REACHED_CHOICES.index(reached)
    row = {"ckpt": args.ckpt_tag, "seed": seed, "note": note}
    for i, s in enumerate(STAGES):
        row[s] = (i + 1) <= k
    rows = [r for r in rows if not (r["ckpt"] == row["ckpt"] and r["seed"] == row["seed"])]
    rows.append(row)
    write_file(path, rows, title=path.stem.replace("_", " "))
    ck = [r for r in rows if r["ckpt"] == row["ckpt"]]
    print(f"[eval] logged; {row['ckpt']} so far ({len(ck)} seeds): "
          + "  ".join(f"{s} {sum(r[s] for r in ck)}/{len(ck)}" for s in STAGES))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckpt", required=True)
    parser.add_argument("--ckpt-tag", default=None, help="short tag for the log (default: ckpt dirname)")
    parser.add_argument("--file", default=None,
                        help="markdown eval log (default: eval-logs/real_eval_<date>.md)")
    parser.add_argument("--seed", type=int, default=1000000)
    parser.add_argument("--seed-end", type=int, default=None,
                        help="stop after logging this seed (inclusive); default: run until [q]")
    parser.add_argument("--nominals", default="wmrl/post-train-dataset_v4/seed_nominals")
    parser.add_argument("--task", default="wmrl/wmrl/tasks/gear_assembly/medium_pick_place_insert_sdf.yaml")
    parser.add_argument("--seconds", type=float, default=90.0)
    parser.add_argument("--out", default="wmrl/runs/mujoco_gear/real_rollouts")
    parser.add_argument("--device", default="cuda:0")
    # 2026-08-28: default OFF. TE re-runs the full policy every 30 Hz step
    # (3 ResNet18 + transformer) and fell behind real-time; the policy is a
    # 100-action chunk model, so chunked open-loop (--no-te) is the intended,
    # real-time eval mode. Pass --te to re-enable temporal ensembling.
    parser.add_argument("--te", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--video", action=argparse.BooleanOptionalAction, default=True,
                        help="keep per-episode camera videos (--no-video to skip and save disk)")
    parser.add_argument("--record-return", action=argparse.BooleanOptionalAction, default=True,
                        help="extend the video through the post-episode gripper release + "
                             "return-home (default on; --no-record-return ends the video at "
                             "the last policy step)")
    args = parser.parse_args()
    if args.ckpt_tag is None:
        args.ckpt_tag = Path(args.ckpt).name
    if args.file is None:
        args.file = f"wmrl/eval-logs/real_eval_{datetime.now().strftime('%Y%m%d')}.md"
        print(f"[eval] logging to {args.file}")

    import json

    from wmrl.real.bridge_client import BridgeClient
    from wmrl.real.cameras import FrameGrabber, RealSenseRig
    from wmrl.real.real_rollout import load_policy
    from wmrl.real.scripted_session import _overlay_gate

    bridge = BridgeClient(timeout_s=60.0)
    bridge.ping()

    # Match the pose used when the operator saves WB references in the overlay.
    bridge.reset_to_start()
    w, h = CAPTURE_WH
    rig = RealSenseRig(size=None, require_all=True, width=w, height=h, resolutions={})
    grabber = FrameGrabber(rig, raw=True)
    bridge.open_gripper()
    bridge.reset_to_start()

    policy_bundle = load_policy(args.ckpt, args.device, temporal_ensemble=args.te)
    print(f"[eval] policy {args.ckpt_tag} loaded (TE={'on' if args.te else 'off'})")

    sim_env = None
    seed = args.seed
    try:
        while True:
            npath = Path(args.nominals) / f"seed_{seed}.json"
            if not npath.exists():
                print(f"[eval] {npath} missing — run wmrl.scripts.export_seed_nominals")
                break
            nominal = json.loads(npath.read_text())
            print(f"\n=== {args.ckpt_tag} | seed {seed} ({nominal['reset_variant']}) ===")
            print(f"  place the MEDIUM gear at {np.round(nominal['grip_site_world'][:2], 4).tolist()}"
                  f" (base at {np.round(nominal['base_pos'][:2], 4).tolist()},"
                  f" yaw {nominal['base_yaw']:.3f})")

            if sim_env is None:
                import gymnasium as gym

                from wmrl.envs.mujoco import (MUJOCO_GEAR_ASSEMBLY_MEDIUM_ENV_ID,
                                              register_mujoco_envs)

                register_mujoco_envs()
                sim_env = gym.make(MUJOCO_GEAR_ASSEMBLY_MEDIUM_ENV_ID,
                                   task_config=args.task, render_mode="rgb_array")
            act = _overlay_gate(seed, sim_env, rig, grabber)
            if act == "quit":
                break
            if act == "next":
                seed += 1
                continue
            if act == "prev":
                seed -= 1
                continue

            out_dir = run_policy_episode(args, seed, bridge, rig, grabber, policy_bundle)

            while True:
                ans = input("[eval] [k] log & next seed / [d] discard & redo seed / "
                            "[q] log & quit > ").strip().lower()
                if ans in ("k", "d", "q", ""):
                    break
            if ans == "d":
                shutil.rmtree(out_dir, ignore_errors=True)
                print(f"[eval] discarded {out_dir}; back to seed {seed} overlay")
                continue
            reached, note = _grade()
            _log_row(args, seed, reached, note)
            if ans == "q" or (args.seed_end is not None and seed >= args.seed_end):
                break
            seed += 1
    finally:
        grabber.stop()
        rig.close()


if __name__ == "__main__":
    main()
