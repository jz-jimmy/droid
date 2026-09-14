"""Real-robot scripted data-collection session (state/action/video/timestep).

Per episode: load the seed's NominalScene JSON (exported from sim), confirm
placement interactively, reset to the task start pose, then run the 30 Hz loop
bridge + DiffIK + ScriptedPickPlace while recording. Every termination path
(success, failure, Ctrl-C, watchdog) still saves the recording.

Output wmrl/post-train-dataset_v3/auto/seed_<N>_<timestamp>/:
  {wrist,right_static,left_static}.mp4   raw full-FOV @ capture resolution
  frames/<cam>_<tick>.png                (only with --save-every-n-frames K)
  episode.npz   per-tick: t_wall, stage_idx, q_meas, q_cmd, width_meas,
                width_cmd, tcp_pos_meas (FK of q_meas), tcp_pos_cmd, target_pos
  meta.json     seed, nominal scene, task, camera intrinsics(+distortion)
                @capture res, sim-side extrinsics (features.py), resolution,
                fps, stages, result, annotation

Usage (industreal-mujoco-sdf env / mujoco 3.9, from the Sim2Real root, bridge
live on the NUC):
  PYTHONPATH=$PWD/wmrl:$PWD/industreal-mujoco/src python -m \
      wmrl.real.scripted_session --seed 200 --out wmrl/post-train-dataset_v3/auto
Safety ladder: --dry-run-check (no robot), --no-objects (free-air), --until
{approach,grasp,lift,place,insert}, then full episodes. Hand on the e-stop.
"""

from __future__ import annotations

import os

# Must be set BEFORE anything imports mujoco (ShadowArm pulls it in at startup):
# the overlay gate renders sim views offscreen, and GLX is not reliable in every
# shell this session runs from - EGL is.
os.environ.setdefault("MUJOCO_GL", "egl")

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from wmrl.real import constants
from wmrl.real.scripted_pick_place import HZ, STAGES, ScriptedPickPlace
from wmrl.real.scripted_pick_place_v2 import ScriptedPickPlaceV2

# --script {v1,v2}: v1 = original settle/sweep expert (post-train-dataset_v1
# era); v2 = fast bold straight-push expert (no sweep, stuck -> discard).
SCRIPTS = {"v1": ScriptedPickPlace, "v2": ScriptedPickPlaceV2}

# Runaway guard, NOT a tracking-quality gate: the impedance controller trails
# the streamed command by up to ~0.11 rad on healthy transports, and extended-
# reach seeds (grasp x ~0.65) legitimately reach ~0.16 (seed 1000001 was
# aborted at 0.15 while carrying the gear normally). Real runaways (wrong IK
# branch, encoder fault) blow far past 0.3.
WATCHDOG_QERR_RAD = 0.30   # |q_meas - q_cmd| beyond this -> freeze + abort
CAPTURE_WH = (1280, 720)   # sensor capture resolution (RealSense has no square mode)
# Saved videos are the SQUARE policy view (user decision 2026-08-01): each
# frame passes through cameras.crop_square (principal recentred exactly to
# the centre), so the recorded intrinsics have cx = cy = SAVE_SIDE/2 and all
# downstream crop_square calls become identity. Full-FOV context is discarded
# at record time.
SAVE_SIDE = 720            # saved video resolution (square, = capture height)


class EpisodeRecorder:
    def __init__(self, out_dir: Path, rig, fps: float, every_n: int | None):
        import imageio.v2 as imageio

        self.out_dir = out_dir
        self.rig = rig
        self.every_n = every_n
        self.writers = {}
        self.frame_ticks: list[int] = []
        if every_n:
            (out_dir / "frames").mkdir(parents=True, exist_ok=True)
        else:
            # streaming H.264 (libx264 + yuv420p): plays in VS Code / browsers,
            # unlike cv2's default 'mp4v' (MPEG-4 Part 2)
            for name in rig.intrinsics:
                self.writers[name] = imageio.get_writer(
                    str(out_dir / f"{name}.mp4"), fps=fps, format="FFMPEG",
                    codec="libx264", pixelformat="yuv420p", quality=8,
                    output_params=["-movflags", "+faststart", "-profile:v", "main"])

    def _crop(self, name: str, img: np.ndarray) -> np.ndarray:
        from wmrl.real.cameras import crop_square

        intr = self.rig.intrinsics[name]
        return crop_square(img, (intr["cx"], intr["cy"]))

    def record(self, tick: int, frames: dict[str, np.ndarray] | None) -> None:
        if frames is None:
            return
        if self.every_n:
            if tick % self.every_n == 0:
                import cv2
                for name, img in frames.items():
                    cv2.imwrite(str(self.out_dir / "frames" / f"{name}_{tick:05d}.png"),
                                cv2.cvtColor(self._crop(name, img), cv2.COLOR_RGB2BGR))
                self.frame_ticks.append(tick)
        else:
            for name, img in frames.items():
                self.writers[name].append_data(self._crop(name, img))  # rig frames are RGB
            self.frame_ticks.append(tick)

    def close(self) -> None:
        for w in self.writers.values():
            w.close()


def camera_meta(rig) -> dict:
    from wmrl.lerobot.features import camera_cfgs

    # capture_intrinsics describe the SAVED (square, principal-recentred)
    # videos: cx = cy = SAVE_SIDE/2 exactly, fx/fy unchanged by the crop.
    # The sensor-native intrinsics are kept alongside for provenance.
    saved = {}
    for cam, intr in rig.intrinsics.items():
        s = dict(intr)
        s.update(width=SAVE_SIDE, height=SAVE_SIDE,
                 cx=SAVE_SIDE / 2.0, cy=SAVE_SIDE / 2.0)
        saved[cam] = s
    meta = {"capture_intrinsics": saved,
            "sensor_intrinsics": rig.intrinsics,
            "save_crop": "cameras.crop_square (square, principal recentred)"}
    for cam in rig.intrinsics:
        meta[f"{cam}_sim_cfg"] = {k: v for k, v in camera_cfgs(cam)[cam].items()
                                  if not isinstance(v, np.ndarray)}
    return meta


def run_episode(args, nominal: dict, bridge, shadow, diffik, rig, grabber) -> dict:
    out_dir = Path(args.out) / f"seed_{nominal['seed']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder = None
    if args.record and rig is not None:
        recorder = EpisodeRecorder(out_dir, rig, HZ, args.save_every_n_frames)

    machine = SCRIPTS[args.script](nominal, until=args.until, no_objects=args.no_objects)
    log: dict[str, list] = {k: [] for k in
                            ["t_wall", "stage_idx", "q_meas", "q_cmd", "width_meas",
                             "width_cmd", "tcp_pos_meas", "tcp_pos_cmd", "target_pos"]}
    result = {"result": "aborted", "failure_reason": None, "ticks": 0}

    print(f"[episode] reset_to_start ...")
    bridge.reset_to_start()
    time.sleep(0.5)
    state = bridge.get_state()
    q_cmd = np.asarray(state["q"], dtype=np.float64)
    bridge.start_stream()
    last_stage = None
    try:
        t0 = time.time()
        tick = 0
        while True:
            t_tick = t0 + tick / HZ
            delay = t_tick - time.time()
            if delay > 0:
                time.sleep(delay)

            state = bridge.get_state()
            q_meas = np.asarray(state["q"], dtype=np.float64)
            width_meas = float(state["width"])
            if np.abs(q_meas - q_cmd).max() > WATCHDOG_QERR_RAD:
                machine.failure_reason = "tracking watchdog (|q_meas-q_cmd|)"
                result["result"] = "watchdog_abort"
                break
            tcp_meas, R_meas = shadow.fk(q_meas)

            cmd = machine.step({"tcp_pos": tcp_meas, "tcp_R": R_meas, "width": width_meas})
            if cmd["stage"] != last_stage:
                print(f"[stage] tick {tick}: {last_stage} -> {cmd['stage']}"
                      + (f" ({cmd['failure_reason']})" if cmd["failure_reason"] else "")
                      + (f"  [{cmd['note']}]" if cmd.get("note") else ""))
                last_stage = cmd["stage"]
            q_cmd = diffik.step(q_cmd, cmd["pos"], cmd["R"])
            bridge.step(q_cmd, width=cmd["width"])
            tcp_cmd, _ = shadow.fk(q_cmd)

            log["t_wall"].append(time.time())
            log["stage_idx"].append(cmd["stage_idx"])
            log["q_meas"].append(q_meas)
            log["q_cmd"].append(q_cmd.copy())
            log["width_meas"].append(width_meas)
            log["width_cmd"].append(cmd["width"])
            log["tcp_pos_meas"].append(tcp_meas)
            log["tcp_pos_cmd"].append(tcp_cmd)
            log["target_pos"].append(cmd["pos"])
            if recorder is not None:
                recorder.record(tick, grabber.latest())

            tick += 1
            if cmd["done"]:
                result["result"] = "success" if cmd["success"] else "failure"
                result["failure_reason"] = cmd["failure_reason"]
                break
    except KeyboardInterrupt:
        print("\n[episode] Ctrl-C: aborting gracefully (recording is kept)")
        result["result"] = "user_abort"
    finally:
        try:
            bridge.stop_stream()
        except Exception as exc:
            print(f"[episode] stop_stream failed: {exc}")
        # ALWAYS open the gripper, then leave the rig at the precise joint-space
        # task reset (every termination path: success, failure, watchdog, Ctrl-C).
        try:
            bridge.open_gripper()
        except Exception as exc:
            print(f"[episode] open_gripper failed: {exc}")
        # ALWAYS leave the rig at the precise joint-space task reset: the
        # retreat stage only tracks the start pose in task space (~5 mm with
        # lag/sag), and overlay checks + the next episode assume the exact pose.
        try:
            bridge.reset_to_start()
            s = bridge.get_state()
            err = np.abs(np.asarray(s["q"]) - np.asarray(constants.START_ARM_QPOS)).max()
            print(f"[episode] post-episode task reset: max joint err {err:.4f} rad")
        except Exception as exc:
            print(f"[episode] post-episode reset failed: {exc}")
        result["ticks"] = len(log["t_wall"])
        result["insert_outcome"] = machine.insert_outcome
        result["insert_note"] = getattr(machine, "stuck_note", None)
        if recorder is not None:
            recorder.close()
        np.savez_compressed(out_dir / "episode.npz",
                            **{k: np.asarray(v) for k, v in log.items()},
                            frame_ticks=np.asarray(recorder.frame_ticks if recorder else []))
        annotation = ""
        if result["result"] in ("success", "failure") and not args.no_annotate:
            annotation = input(f"[episode] result={result['result']} — annotation "
                               f"(enter to accept, or note): ").strip()
        meta = {
            "seed": nominal["seed"], "nominal_scene": nominal, "task": args.task,
            "script_version": args.script,
            "until": args.until, "no_objects": args.no_objects,
            "resolution": [SAVE_SIDE, SAVE_SIDE], "capture_resolution": list(CAPTURE_WH),
            "fps": HZ,
            "stage_names": STAGES, "save_every_n_frames": args.save_every_n_frames,
            "gripper": {"close_thresh": constants.GRIPPER_CLOSE_CMD_THRESH,
                        "open_thresh": constants.GRIPPER_OPEN_CMD_THRESH,
                        "measured_latency_s": 1.08},
            **result, "annotation": annotation,
            **(camera_meta(rig) if rig is not None else {}),
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        print(f"[episode] saved {out_dir} ({result['result']}, {result['ticks']} ticks)")
    result["out_dir"] = str(out_dir)
    return result


def _overlay_gate(seed: int, sim_env, rig, grabber) -> str:
    """Live sim/real overlay for placement checking, integrated into the
    collection loop. Returns 'collect' | 'next' | 'prev' | 'quit'."""
    import cv2

    from wmrl.real import project_ranges as pr
    from wmrl.real.align_overlay import compose_panel, draw_ranges, render_sim
    from wmrl.real.cameras import crop_square
    from wmrl.lerobot.features import camera_cfgs

    SIZE = 480
    cams = ("wrist", "right_static", "left_static")
    u = sim_env.unwrapped
    sims = {}
    for cam in cams:
        sims[cam] = render_sim(sim_env, seed, SIZE, cam)
        K_sim = pr.K_from_fovy(float(camera_cfgs(cam)[cam]["fovy"]), SIZE, SIZE)
        draw_ranges(sims[cam], u, cam, K_sim)

    win = f"seed {seed} overlay   [c/ENTER]=collect  [n]ext  [p]rev  [j]=bless WB  [q]uit"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    action = "quit"
    try:
        while True:
            frames = grabber.latest()
            if not frames:
                cv2.waitKey(50)
                continue
            reals = {}
            for cam in cams:
                intr = rig.intrinsics[cam]
                real = crop_square(frames[cam], (intr["cx"], intr["cy"]))
                if real.shape[0] != SIZE:
                    real = cv2.resize(real, (SIZE, SIZE))
                reals[cam] = real
            panel = compose_panel(u, cams, sims, reals, rig, SIZE)
            cv2.imshow(win, cv2.cvtColor(panel, cv2.COLOR_RGB2BGR))
            k = cv2.waitKey(50) & 0xFF
            if k == ord("j"):
                import json as _json

                from wmrl.real.bless_wb import OUT, PATCHES

                ref = {}
                for nm, (y0, y1, x0, x1) in PATCHES.items():
                    from wmrl.real.white_balance import make_reference
                    ref[nm] = make_reference(frames[nm], (y0, y1, x0, x1), rig.serials[nm])
                OUT.write_text(_json.dumps(ref, indent=2))
                rig.wb_status = {nm: "valid" for nm in ref}
                print(f"[overlay] WB re-blessed from current session state -> {OUT}")
                continue
            if k in (ord("c"), 13):
                action = "collect"; break
            if k == ord("n"):
                action = "next"; break
            if k == ord("p"):
                action = "prev"; break
            if k in (ord("q"), 27):
                action = "quit"; break
    finally:
        cv2.destroyWindow(win)
    return action


def _hover_for_camera_warmup(bridge, shadow, diffik, hover=(0.486, -0.027, -0.10)) -> None:
    """Move the TCP to the canonical camera-warmup hover (same scene the WB
    reference was blessed at), streaming via DiffIK; arm holds there while the
    camera warmup runs."""
    hover = np.asarray(hover, dtype=np.float64)
    R_down = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]])
    q_cmd = np.asarray(bridge.get_state()["q"], dtype=np.float64)
    pos, _ = shadow.fk(q_cmd)
    target = pos.copy()
    bridge.start_stream()
    try:
        t0 = time.time()
        tick = 0
        settle = 0
        while True:
            time.sleep(max(0.0, t0 + tick / HZ - time.time()))
            tick += 1
            q_meas = np.asarray(bridge.get_state()["q"], dtype=np.float64)
            if np.abs(q_meas - q_cmd).max() > WATCHDOG_QERR_RAD:
                print("[session] warmup-hover watchdog; holding")
                break
            pm, _ = shadow.fk(q_meas)
            d = hover - target
            n = np.linalg.norm(d)
            target = hover if n < 0.003 else target + d / n * 0.003
            if n < 1e-9 and np.linalg.norm(pm[:2] - hover[:2]) < 0.01:
                settle += 1
                if settle > 30:
                    break
            if tick > 30 * HZ:
                break
            q_cmd = diffik.step(q_cmd, target, R_down)
            bridge.step(q_cmd, width=0.078)
    finally:
        bridge.stop_stream()
    print("[session] holding warmup hover for camera warmup")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1000000)
    # v3 nominals are SDF-derived (mujoco 3.9); v1/v2 seeds 0-199 and the eval
    # seeds 1000000+ are convex-decomp-derived and live in
    # wmrl/post-train-dataset_v1/seed_nominals - pass --nominals for those.
    parser.add_argument("--nominals", default="wmrl/post-train-dataset_v3/seed_nominals")
    # auto = the automated scripted workflow source (teleop episodes will live
    # beside it under post-train-dataset/<source>/)
    parser.add_argument("--out", default="wmrl/post-train-dataset_v3/auto")
    # The overlay the operator matches against MUST come from the same env the
    # nominals were exported from, otherwise the placement gate and the scripted
    # grasp reference disagree (convex vs SDF settle differs by ~1 mm).
    parser.add_argument("--task", default="wmrl/wmrl/tasks/gear_assembly/medium_pick_place_insert_sdf.yaml")
    parser.add_argument("--until", default="done",
                        choices=["approach", "grasp", "lift", "place", "preinsert", "insert", "done"])
    parser.add_argument("--script", choices=list(SCRIPTS), default="v2",
                        help="expert version: v1 = settle+sweep, v2 = fast bold no-sweep")
    parser.add_argument("--no-objects", action="store_true", help="free-air rehearsal")
    parser.add_argument("--record", dest="record", action="store_true", default=True)
    parser.add_argument("--no-record", dest="record", action="store_false")
    parser.add_argument("--no-annotate", action="store_true")
    parser.add_argument("--no-overlay", action="store_true",
                        help="Disable the integrated overlay gate (fall back to the text prompt)")
    parser.add_argument("--save-every-n-frames", type=int, default=None)
    parser.add_argument("--dry-run-check", action="store_true",
                        help="load nominal + build DiffIK + print the plan; no robot/cameras")
    args = parser.parse_args()

    from wmrl.real.diffik import DiffIK, ShadowArm

    shadow = ShadowArm()
    diffik = DiffIK(shadow, q_nominal=np.asarray(constants.START_ARM_QPOS))

    seed = args.seed
    rig = grabber = bridge = None
    sim_env = None
    if not args.dry_run_check:
        from wmrl.real.bridge_client import BridgeClient
        from wmrl.real.cameras import FrameGrabber, RealSenseRig

        bridge = BridgeClient(timeout_s=60.0)
        bridge.ping()
        if args.record:
            # CANONICAL CAMERA WARMUP SCENE: the frozen-WB convergence point is
            # scene-dependent (left tint R/G 1.45 hovering vs 1.00 at reset,
            # 2026-07-27), and the blessed reference tint was captured with the
            # gripper HOVERING over the base plate. Reproduce that scene for
            # warmup, then task-reset before collecting.
            _hover_for_camera_warmup(bridge, shadow, diffik)
            w, h = CAPTURE_WH
            rig = RealSenseRig(size=None, require_all=True, width=w, height=h,
                               resolutions={})
            grabber = FrameGrabber(rig, raw=True)
            bridge.open_gripper()
            bridge.reset_to_start()

    try:
        while True:
            path = Path(args.nominals) / f"seed_{seed}.json"
            if not path.exists():
                print(f"[session] {path} missing — run wmrl.scripts.export_seed_nominals")
                break
            nominal = json.loads(path.read_text())
            print(f"\n=== seed {seed} ({nominal['reset_variant']}) ===")
            print(f"  place the MEDIUM gear at {np.round(nominal['grip_site_world'][:2], 4).tolist()}"
                  f" (base at {np.round(nominal['base_pos'][:2], 4).tolist()},"
                  f" yaw {nominal['base_yaw']:.3f})")
            print("  verify with: python -m wmrl.real.align_overlay --seed", seed)

            if args.dry_run_check:
                machine = SCRIPTS[args.script](nominal, until=args.until, no_objects=True)
                tcp = np.asarray(nominal["tcp_start_pos"])
                width = 0.078
                for _ in range(int(120 * HZ)):
                    cmd = machine.step({"tcp_pos": tcp, "width": width})
                    tcp = cmd["pos"]  # perfect tracking
                    # emulate the hand: slew toward the commanded open/close at
                    # the real finger speed (the width-stability grasp gate
                    # never settles on a constant width)
                    w_tgt = 0.0 if cmd["width"] < 0.045 else 0.078
                    width += float(np.clip(w_tgt - width, -0.096 / HZ, 0.096 / HZ))
                    if machine.stage == "insert":
                        tcp = tcp.copy()
                        tcp[2] = max(tcp[2], nominal["tcp_z_seated"])  # gear meets shaft
                    if cmd["done"]:
                        break
                print(f"  dry-run: reached {cmd['stage']} in {machine.tick} ticks"
                      f" ({machine.tick / HZ:.1f}s) failure={cmd['failure_reason']}")
                break

            if rig is not None and not args.no_overlay:
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
                result = run_episode(args, nominal, bridge, shadow, diffik, rig, grabber)
                while True:
                    outcome = result.get("insert_outcome")
                    ans = input(f"[episode] result={result['result']}"
                                + (f" insert={outcome}" if outcome else "")
                                + " — [k]eep & next seed / [d]iscard & retry this seed > "
                                ).strip().lower()
                    if ans in ("k", "d", ""):
                        break
                if ans == "d":
                    import shutil

                    shutil.rmtree(result["out_dir"], ignore_errors=True)
                    print(f"[episode] discarded {result['out_dir']}; back to seed {seed} overlay")
                    continue
                seed += 1
                continue

            ans = input("[enter]=run  n/p=seed+-1  g <seed>=jump  s=skip  q=quit > ").strip()
            if ans == "q":
                break
            if ans == "n":
                seed += 1
                continue
            if ans == "p":
                seed -= 1
                continue
            if ans.startswith("g"):
                seed = int(ans.split()[1])
                continue
            if ans == "s":
                seed += 1
                continue
            run_episode(args, nominal, bridge, shadow, diffik, rig, grabber)
            seed += 1
    finally:
        if grabber is not None:
            grabber.stop()
        if rig is not None:
            rig.close()


if __name__ == "__main__":
    main()
