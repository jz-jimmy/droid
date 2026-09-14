"""Franka bridge server (polymetis backend): owns the arm via polymetis, ZMQ REP API.

Runs ON THE NUC in the `polymetis-local` conda env (the DROID stack; this rig has no
frankapy/ROS). The polymetis robot+gripper servers must be running first:

  sudo bash ~/droid/droid/franka/launch_robot.sh     # NUC terminal 1
  sudo bash ~/droid/droid/franka/launch_gripper.sh   # NUC terminal 2
  conda activate polymetis-local                     # NUC terminal 3
  python ~/wmrl_bridge/franka_bridge_polymetis.py

The workstation client connects to tcp://<NUC_IP>:5588 (constants.BRIDGE_CONNECT).
Protocol and safety are IDENTICAL to franka_bridge.py (the frankapy variant):

  - fingertip TCP z floor (Z_MIN_TCP) via analytic FK of the commanded target
  - per-joint step clamp toward the target (MAX_JOINT_STEP_RAD per tick)
  - joint limit clamp
  - event-quantized gripper (real hand cannot stream width setpoints)
  - freeze (terminate policy) on stop/estop

ZMQ API (JSON request -> JSON reply):
  {"cmd": "ping"}
  {"cmd": "get_state"}                 -> {"q": [7], "dq": [7], "width": w, "streaming": bool}
  {"cmd": "reset_to_start"}            -> blocking goto to the sim start pose + open gripper
  {"cmd": "start_stream"}              -> begin joint-impedance streaming at the current pose
  {"cmd": "step", "q": [7], "width": w} -> apply one 30 Hz target; replies with state + safety flags
  {"cmd": "stop_stream"}               -> terminate the policy (arm holds)
  {"cmd": "open_gripper"}
  {"cmd": "estop"}
"""

import argparse
import importlib.util
import json
import os
import threading
import time

import numpy as np


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_HERE = os.path.dirname(os.path.abspath(__file__))
C = _load_module("bridge_constants", os.path.join(_HERE, "constants.py"))
FK = _load_module("bridge_fk", os.path.join(_HERE, "franka_fk.py"))


class GripperWorker(threading.Thread):
    """Binary gripper execution with hysteresis, off the 30 Hz arm loop.

    The Franka Hand cannot track streamed widths, and its `move` primitive stops at
    finger-object contact WITHOUT holding force (a width staircase both stalls silently
    and could never carry the gear). So the policy's width channel is quantized to two
    events: target < GRIPPER_CLOSE_CMD_THRESH -> force-holding `grasp`;
    target > GRIPPER_OPEN_CMD_THRESH -> open move. The hysteresis band suppresses
    chatter from temporally-ensembled targets."""

    # Minimum spacing between fired primitives: latency 1.08 s + motion up to
    # ~0.8 s + margin. A command sent while a grasp is still executing raises
    # franka::CommandException INSIDE franka_hand_client and SIGABRTs it (hit
    # twice on the rig); policies in their retry regime can toggle every
    # 0.4-0.7 s (measured 2026-07-25). The latch below still applies the
    # LATEST requested state once the refractory expires, so no final open
    # or close is ever lost - only intermediate flapping is absorbed.
    # DISABLED 2026-07-25 per user (set back to 2.5 to re-enable): with 0.0
    # the latch/hysteresis behave exactly as before the guard was added.
    REFRACTORY_S = 0.0

    def __init__(self, gripper):
        super(GripperWorker, self).__init__(daemon=True)
        self.gripper = gripper
        self.lock = threading.Lock()
        self.target = None
        self.state = "open"  # reset_to_start/open_gripper keep this in sync
        self.running = True
        self.last_fire = 0.0

    def set_target(self, width):
        with self.lock:
            self.target = float(np.clip(width, 0.0, C.GRIPPER_MAX_WIDTH))

    def run(self):
        while self.running:
            action = None
            with self.lock:
                if self.target is not None and time.time() - self.last_fire >= self.REFRACTORY_S:
                    if self.state != "closed" and self.target < C.GRIPPER_CLOSE_CMD_THRESH:
                        action, self.state = "close", "closed"
                        self.last_fire = time.time()
                    elif self.state != "open" and self.target > C.GRIPPER_OPEN_CMD_THRESH:
                        action, self.state = "open", "open"
                        self.last_fire = time.time()
            if action == "close":
                try:
                    # Same grasp invocation DROID uses on this hand/fork: closes onto
                    # whatever is between the fingers and HOLDS with force.
                    self.gripper.grasp(speed=0.2, force=C.GRIPPER_GRASP_FORCE,
                                       grasp_width=-0.2, epsilon_inner=0.1,
                                       epsilon_outer=0.9, blocking=False)
                    print("[bridge] gripper event: GRASP")
                except Exception as exc:  # noqa: BLE001 - never kill the safety loop
                    print("[bridge] gripper grasp failed: %r" % (exc,))
            elif action == "open":
                try:
                    self.gripper.goto(width=C.START_GRIPPER_WIDTH, speed=C.GRIPPER_SPEED,
                                      force=1.0, blocking=False)
                    print("[bridge] gripper event: OPEN")
                except Exception as exc:  # noqa: BLE001
                    print("[bridge] gripper open failed: %r" % (exc,))
            time.sleep(0.05)


class FrankaBridgePolymetis(object):
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.streaming = False
        self.gripper = None
        self._dry_q = np.asarray(C.START_ARM_QPOS, dtype=np.float64)
        self._dry_w = C.START_GRIPPER_WIDTH
        if not dry_run:
            import torch  # noqa: F401 - polymetis interfaces take torch tensors
            from polymetis import GripperInterface, RobotInterface

            self.robot = RobotInterface(ip_address="localhost")
            self.hand = GripperInterface(ip_address="localhost")
            self.gripper = GripperWorker(self.hand)
            self.gripper.start()
        print("[bridge] ready (dry_run=%s), z floor = %.4f m" % (dry_run, C.Z_MIN_TCP))

    # ----- state -----
    def get_state(self):
        if self.dry_run:
            q, dq, w = self._dry_q, np.zeros(7), self._dry_w
        else:
            q = np.asarray(self.robot.get_joint_positions(), dtype=np.float64)
            dq = np.asarray(self.robot.get_joint_velocities(), dtype=np.float64)
            w = float(self.hand.get_state().width)
        if self.dry_run:
            g_state = "closed" if self._dry_w < C.GRIPPER_CLOSE_CMD_THRESH else "open"
        else:
            g_state = self.gripper.state
        return {
            "q": q.tolist(),
            "dq": dq.tolist(),
            "width": w,
            "tcp_z": FK.tcp_z(q),
            "streaming": self.streaming,
            "gripper_state": g_state,
        }

    # ----- motions -----
    def reset_to_start(self):
        if self.streaming:
            self.stop_stream()
        print("[bridge] reset_to_start")
        if not self.dry_run:
            import torch

            # Closed-loop settle: polymetis joint impedance sags ~0.02 rad under gravity
            # (about 10 mm TCP). Feed the measured residual forward into the goal until
            # the TRUE joints land on the start pose.
            target = np.asarray(C.START_ARM_QPOS, dtype=np.float64)
            goal = target.copy()
            for i in range(4):
                self.robot.move_to_joint_positions(
                    torch.Tensor(goal.tolist()), time_to_go=6.0 if i == 0 else 2.0
                )
                time.sleep(0.3)
                q = np.asarray(self.robot.get_joint_positions(), dtype=np.float64)
                err = target - q
                print("[bridge] reset iter %d: max joint err %.4f rad" % (i, np.abs(err).max()))
                if np.abs(err).max() < 0.004:
                    break
                goal = np.clip(goal + err,
                               np.asarray(C.JOINT_LIMITS_LOW) + 1e-3,
                               np.asarray(C.JOINT_LIMITS_HIGH) - 1e-3)
            self._open_and_settle()
        else:
            self._dry_q = np.asarray(C.START_ARM_QPOS, dtype=np.float64)
            self._dry_w = C.START_GRIPPER_WIDTH
        return {"ok": True}

    def _open_and_settle(self):
        """Open the hand and wait for the width to physically settle (this polymetis
        fork's `goto` returns before the motion completes even with blocking=True)."""
        if self.gripper is not None:
            with self.gripper.lock:
                self.gripper.target = None
                self.gripper.state = "open"
        self.hand.goto(width=C.START_GRIPPER_WIDTH, speed=C.GRIPPER_SPEED,
                       force=1.0, blocking=True)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if abs(float(self.hand.get_state().width) - C.START_GRIPPER_WIDTH) < 0.003:
                break
            time.sleep(0.05)

    def start_stream(self):
        if self.streaming:
            return {"ok": True, "note": "already streaming"}
        if not self.dry_run:
            if not self.robot.is_running_policy():
                # Joint impedance tracks streamed joint targets directly. (DROID's fork
                # streams update_desired_joint_positions into cartesian impedance; plain
                # polymetis pairs it with joint impedance - try that first.)
                try:
                    self.robot.start_joint_impedance()
                except Exception as exc:  # noqa: BLE001
                    print("[bridge] start_joint_impedance failed (%r); "
                          "falling back to start_cartesian_impedance" % (exc,))
                    self.robot.start_cartesian_impedance()
                deadline = time.time() + 5.0
                while not self.robot.is_running_policy():
                    time.sleep(0.01)
                    if time.time() > deadline:
                        return {"ok": False, "error": "controller policy did not start"}
        self.streaming = True
        return {"ok": True}

    def _safety_filter(self, q_cur, q_target):
        flags = []
        q_t = np.clip(
            np.asarray(q_target, dtype=np.float64).reshape(7),
            np.asarray(C.JOINT_LIMITS_LOW) + 1e-3,
            np.asarray(C.JOINT_LIMITS_HIGH) - 1e-3,
        )
        if not np.all(np.isfinite(q_t)):
            return q_cur, ["nonfinite_target"]
        # Per-tick step clamp toward the target.
        delta = q_t - q_cur
        max_step = C.MAX_JOINT_STEP_RAD
        if np.any(np.abs(delta) > max_step):
            q_t = q_cur + np.clip(delta, -max_step, max_step)
            flags.append("step_clamped")
        # z floor: reject (hold pose) if the commanded pose would dip the TCP below Z_MIN.
        if FK.tcp_z(q_t) < C.Z_MIN_TCP:
            # Try to keep the lateral part of the motion by bisecting toward the current pose.
            lo, hi = 0.0, 1.0
            for _ in range(12):
                mid = 0.5 * (lo + hi)
                if FK.tcp_z(q_cur + mid * (q_t - q_cur)) >= C.Z_MIN_TCP:
                    lo = mid
                else:
                    hi = mid
            q_t = q_cur + lo * (q_t - q_cur)
            flags.append("z_floor")
        # Measured-z backstop (sag-sign-robust): if the MEASURED tcp is already
        # at the hard-stop, refuse commands that descend further; lateral and
        # upward motion stays allowed via the same bisection.
        z_meas = FK.tcp_z(q_cur)
        if z_meas <= C.Z_MEAS_HARDSTOP and FK.tcp_z(q_t) < z_meas:
            lo, hi = 0.0, 1.0
            for _ in range(12):
                mid = 0.5 * (lo + hi)
                if FK.tcp_z(q_cur + mid * (q_t - q_cur)) >= z_meas:
                    lo = mid
                else:
                    hi = mid
            q_t = q_cur + lo * (q_t - q_cur)
            flags.append("z_backstop")
        return q_t, flags

    def step(self, q_target, width):
        if not self.streaming:
            return {"ok": False, "error": "not streaming; call start_stream first"}
        state = self.get_state()
        q_cur = np.asarray(state["q"], dtype=np.float64)
        q_safe, flags = self._safety_filter(q_cur, q_target)
        if not self.dry_run:
            import torch

            try:
                self.robot.update_desired_joint_positions(torch.Tensor(q_safe))
            except Exception as exc:  # noqa: BLE001 - controller may have been bumped
                return {"ok": False, "error": "update_desired failed: %r" % (exc,)}
            if width is not None and self.gripper is not None:
                self.gripper.set_target(width)
        else:
            self._dry_q = q_safe
            if width is not None:
                self._dry_w = float(np.clip(width, 0.0, C.GRIPPER_MAX_WIDTH))
        out = self.get_state()
        out.update({"ok": True, "safety_flags": flags, "q_commanded": q_safe.tolist()})
        return out

    def stop_stream(self):
        if self.streaming and not self.dry_run:
            try:
                if self.robot.is_running_policy():
                    self.robot.terminate_current_policy()
            except Exception as exc:  # noqa: BLE001
                print("[bridge] terminate_current_policy failed: %r" % (exc,))
        self.streaming = False
        return {"ok": True}

    def open_gripper(self):
        if not self.dry_run:
            self._open_and_settle()
        else:
            self._dry_w = C.START_GRIPPER_WIDTH
        return {"ok": True}

    def estop(self):
        print("[bridge] ESTOP")
        if not self.dry_run:
            try:
                self.hand.stop(blocking=False)
            except Exception as exc:  # noqa: BLE001
                print("[bridge] gripper stop failed: %r" % (exc,))
        return self.stop_stream()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default=C.BRIDGE_BIND)
    parser.add_argument("--dry-run", action="store_true",
                        help="No robot: kinematic echo server for client/safety testing.")
    args = parser.parse_args()

    import zmq

    bridge = FrankaBridgePolymetis(dry_run=args.dry_run)
    ctx = zmq.Context()
    sock = ctx.socket(zmq.REP)
    sock.bind(args.bind)
    print("[bridge] listening on %s" % args.bind)

    handlers = {
        "ping": lambda req: {"ok": True, "dry_run": bridge.dry_run},
        "get_state": lambda req: bridge.get_state(),
        "reset_to_start": lambda req: bridge.reset_to_start(),
        "start_stream": lambda req: bridge.start_stream(),
        "step": lambda req: bridge.step(req["q"], req.get("width")),
        "stop_stream": lambda req: bridge.stop_stream(),
        "open_gripper": lambda req: bridge.open_gripper(),
        "estop": lambda req: bridge.estop(),
    }
    try:
        while True:
            req = json.loads(sock.recv().decode("utf-8"))
            handler = handlers.get(req.get("cmd"))
            if handler is None:
                reply = {"ok": False, "error": "unknown cmd %r" % (req.get("cmd"),)}
            else:
                try:
                    reply = handler(req)
                except Exception as exc:  # noqa: BLE001 - report, never crash mid-policy
                    reply = {"ok": False, "error": repr(exc)}
            sock.send(json.dumps(reply).encode("utf-8"))
    finally:
        bridge.stop_stream()


if __name__ == "__main__":
    main()
