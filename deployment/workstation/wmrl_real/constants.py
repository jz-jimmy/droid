"""Real-robot deployment constants. Python 3.8 compatible (imported by the bridge container).

All values trace to the committed simulation convention; the sim is the single source of
truth. Regenerate the start-state block by resetting the MuJoCo task env
(medium_pick_place_insert_lerobot.yaml, any seed) and reading arm qpos / TCP.
"""

# --- Episode start state (from the sim reset: natural front-facing configuration,
# --- TCP at the industreal_xz_y0 preset; verified as the policy's fixed point) ---
# Rotated workspace (2026-07-12): gripper start yaw +pi/2 -> joint7 flips sign.
# Re-derived from the sim reset fixed point (deterministic across seeds).
START_ARM_QPOS = [0.003736, 0.048172, -0.003715, -2.457633, 0.000301, 2.505804, -0.785616]
START_TCP_POS = [0.480024, 0.0, 0.174282]        # robot-base frame, meters
START_GRIPPER_WIDTH = 0.078                      # commanded open width at reset (max 0.08)

# --- Workspace geometry (robot-base frame) ---
# CONTACT-ANCHORED 2026-07-14: tape-measured mount->table = 210 mm, confirmed by a
# guarded fingertip contact probe (tips touched at model tcp -0.1997; stock fingers
# extend 10.3 mm below the tcp site). The previous anchor (-0.2316) was ~21 mm stale
# (base/riser changed); the wrist cam2gripper z carried the compensating bias.
PHYSICAL_TABLE_Z = -0.2100
# Board-top plane = physical table + 6 mm checkerboard (cameras/z-floor reference).
TABLE_Z = -0.2040
# Stock-finger tips below the fingertip-centered TCP site (matches the sim hand).
TCP_TO_FINGERTIP_Z = 0.0103
# Minimum allowed z for the model TCP such that the REAL fingertips stay >= 2 mm
# above the physical table: -0.2100 + 0.0103 + 0.002 = -0.1977.
Z_MIN_TCP = PHYSICAL_TABLE_Z + TCP_TO_FINGERTIP_Z + 0.002
# Feedback backstop on the MEASURED tcp z: the command floor above assumes the
# arm sags upward, but at far reach the impedance equilibrium flips and the
# measured z runs ~3 mm BELOW the command - a confused policy pressed the
# fingertips into the table straight through the command floor (2026-07-25).
# When measured z is at/below this, the bridge refuses any deeper command.
Z_MEAS_HARDSTOP = PHYSICAL_TABLE_Z + TCP_TO_FINGERTIP_Z + 0.004

# --- Safety limits enforced by the bridge ---
MAX_JOINT_STEP_RAD = 0.10        # per 30 Hz step, per joint; demo data moves <0.01 rad/step
JOINT_LIMITS_LOW = [-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973]
JOINT_LIMITS_HIGH = [2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973]

# --- Gripper execution (event-quantized; the real hand cannot stream width setpoints) ---
# The hand executes BINARY events with hysteresis: policy width target falling below
# GRIPPER_CLOSE_CMD_THRESH fires the force-holding `grasp` primitive (a `move` stops at
# contact WITHOUT holding force and cannot carry the gear); rising above
# GRIPPER_OPEN_CMD_THRESH fires an open move. Demo gripper trajectories are effectively
# binary (open 0.078 / squeeze ~0.02), so the quantization is faithful.
GRIPPER_CLOSE_CMD_THRESH = 0.045
GRIPPER_OPEN_CMD_THRESH = 0.055
GRIPPER_GRASP_FORCE = 5.0        # N, same as DROID's grasp_gripper on this hand
GRIPPER_EVENT_THRESHOLD = 0.005  # legacy width-staircase threshold (frankapy bridge)
GRIPPER_SPEED = 0.08             # m/s
GRIPPER_MAX_WIDTH = 0.08

# --- Control rates ---
CONTROL_HZ = 30.0

# --- Kinematics: panda_hand -> fingertip-centered TCP (matches the sim's tcp site) ---
HAND_TO_TCP_Z = 0.1034

# --- Bridge networking ---
# The bridge runs ON THE NUC (128.59.17.233) in the `polymetis-local` env, next to the
# polymetis servers (franka_bridge_polymetis.py; this rig has no frankapy/ROS). The
# workstation client connects across the lab network.
BRIDGE_BIND = "tcp://*:5588"
BRIDGE_CONNECT = "tcp://128.59.17.233:5588"

# --- Cameras (serials/calibration mirror wmrl/lerobot/features.py) ---
# Sides SWAPPED in the 2026-08-21 remount (round 17). Before that (old room):
#   LEFT was 338122303713, RIGHT was 235422302222 (and the LEFT/RIGHT principal/focal
#   pairs below were the other way around) - pre-2026-08-21 datasets use that mapping.
LEFT_STATIC_SERIAL = "235422302222"   # D455, robot-left
RIGHT_STATIC_SERIAL = "338122303713"  # D455, robot-right
WRIST_MODEL = "D405"                  # matched by product line, serial auto-detected
CAM_WIDTH = 848
CAM_HEIGHT = 480
CAM_FPS = 30
# Principal points (from real intrinsics) used as square-crop centers so the cropped
# real image matches the sim's centered square pinhole render.
LEFT_STATIC_PRINCIPAL = (429.3864440917969, 242.79046630859375)
RIGHT_STATIC_PRINCIPAL = (421.3064880371094, 246.91749572753906)
# Focal lengths at 848x480 (factory K, sim_handover_new.yaml) for range projection.
LEFT_STATIC_FOCAL = (422.9227294921875, 422.3628234863281)
RIGHT_STATIC_FOCAL = (428.6227111816406, 427.96759033203125)
WRIST_PRINCIPAL = (421.9, 242.1)  # D405 at 848x480, sim_handover_new.yaml
POLICY_IMAGE_SIZE = 224
