"""Thin ZMQ client for the Franka bridge (used by the rollout runner and CLI verbs)."""

from __future__ import annotations

import json
from typing import Any

import zmq

from wmrl.real import constants


class BridgeClient:
    # reset_to_start runs up to 4 closed-loop iterations (~15 s); a 10 s default
    # made clients abandon resets that then completed fine on the bridge.
    def __init__(self, address: str = constants.BRIDGE_CONNECT, timeout_s: float = 60.0):
        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.REQ)
        self.sock.setsockopt(zmq.RCVTIMEO, int(timeout_s * 1000))
        self.sock.setsockopt(zmq.SNDTIMEO, int(timeout_s * 1000))
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.connect(address)

    def _call(self, **req: Any) -> dict:
        self.sock.send(json.dumps(req).encode("utf-8"))
        reply = json.loads(self.sock.recv().decode("utf-8"))
        if not reply.get("ok", True) and "q" not in reply:
            raise RuntimeError(f"bridge error for {req.get('cmd')}: {reply.get('error')}")
        return reply

    def ping(self) -> dict:
        return self._call(cmd="ping")

    def get_state(self) -> dict:
        return self._call(cmd="get_state")

    def reset_to_start(self) -> dict:
        return self._call(cmd="reset_to_start")

    def start_stream(self) -> dict:
        return self._call(cmd="start_stream")

    def step(self, q, width=None) -> dict:
        return self._call(cmd="step", q=[float(x) for x in q],
                          width=None if width is None else float(width))

    def stop_stream(self) -> dict:
        return self._call(cmd="stop_stream")

    def open_gripper(self) -> dict:
        return self._call(cmd="open_gripper")

    def estop(self) -> dict:
        return self._call(cmd="estop")
