"""Analytic Franka Panda forward kinematics (Python 3.8 compatible, numpy only).

Used by the bridge for the z-floor safety check on commanded joint targets, and
cross-validated against MuJoCo FK by wmrl/real/check_fk_parity.py. Modified-DH
parameters from the official Franka Control Interface documentation.
"""

import numpy as np

# (alpha_{i-1}, a_{i-1}, d_i) for joints 1..7 plus the flange frame.
_DH = [
    (0.0, 0.0, 0.333),
    (-np.pi / 2, 0.0, 0.0),
    (np.pi / 2, 0.0, 0.316),
    (np.pi / 2, 0.0825, 0.0),
    (-np.pi / 2, -0.0825, 0.384),
    (np.pi / 2, 0.0, 0.0),
    (np.pi / 2, 0.088, 0.0),
]
_FLANGE_D = 0.107
# Fingertip-centered TCP sits HAND_TO_TCP_Z below the hand body along its z axis; the
# hand is mounted on the flange with a -45 degree z rotation (irrelevant for TCP z).
# Duplicated from constants.HAND_TO_TCP_Z so this module stays loadable standalone
# inside the bridge container (no package import required).
HAND_TO_TCP_Z = 0.1034


def _dh_transform(alpha, a, d, theta):
    ca, sa = np.cos(alpha), np.sin(alpha)
    ct, st = np.cos(theta), np.sin(theta)
    return np.array([
        [ct, -st, 0.0, a],
        [st * ca, ct * ca, -sa, -d * sa],
        [st * sa, ct * sa, ca, d * ca],
        [0.0, 0.0, 0.0, 1.0],
    ])


def flange_pose(q):
    """4x4 pose of the flange in the robot base frame for joint vector q (7,)."""
    q = np.asarray(q, dtype=np.float64).reshape(7)
    T = np.eye(4)
    for (alpha, a, d), theta in zip(_DH, q):
        T = T @ _dh_transform(alpha, a, d, theta)
    T = T @ _dh_transform(0.0, 0.0, _FLANGE_D, 0.0)
    return T


def tcp_position(q):
    """Fingertip-centered TCP position (3,) in the robot base frame."""
    T = flange_pose(q)
    # Hand z axis == flange z axis (the 45 degree hand mounting rotates about z only).
    return T[:3, 3] + T[:3, 2] * HAND_TO_TCP_Z


def tcp_z(q):
    """TCP height above the robot base; the quantity the z-floor guard clamps."""
    return float(tcp_position(q)[2])
