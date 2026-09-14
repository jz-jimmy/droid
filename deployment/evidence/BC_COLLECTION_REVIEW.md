# BC collection review after upstream update

Updated wmrl mujoco-gear to 7c0026b and industreal-mujoco robo01 to 1e1371b. Local white-balance fixes and accepted reference preserved. No robot motion or controller changes in this review.

## Dataset chain identified

Checkpoint train_config.json names `round20_physical_v3_raw224_1024s`. The newly available `docs/round20_recollect_20260908/physical_release_v3.md` documents this 1024-demo experiment. Native LeRobot collector: `wmrl/scripts/collect_mujoco_gear_visuomotor_lerobot.py`, which wraps the staged pick/place expert with `_TraceRecorder` from `collect_mujoco_gear_visuomotor_scripted.py`. The expert selects `PhysicalStallRelease` from `physical_stall_release.py` using `medium_pick_place_collect_round20_physical_release.yaml`.

The physical-stall expert adds grasp/alignment/tilt perturbations, detects contact stalls, and issues normal recorded OPEN actions; direct insertions also finish with explicit opening. It is not the source of initial grasp closure. All 9 source hashes in the archived 64-seed mixed-outcome probe match the pulled files, including the expert, task inheritance files, and environment. Production job scripts mentioned in the handover are not included at this workspace's jobs/ paths; the exact production argv and dataset metadata remain unavailable locally. The documented measured-width choice agrees with the collector default (`feedback_width=False`).

## Three distinct controllers

1. Demo generation: delta Cartesian targets + TSIController. Resolved collection environment has joint_position_action=False, policy_action_repeat=2 (30 Hz steps from 60 Hz internal target updates), physics ~120 Hz. Task stiffness [300,300,1500,50,50,50], damping [38,38,70,1.8,1.8,1.8]. It does not use the computed-torque joint controller.
2. BC sim evaluation: `medium_pick_place_eval_round20_strict.yaml` -> joint_pos_plus_gripper_width + computed_torque, kp300/kd35, 30 Hz. The TSIController object still exists in the backend but the active joint-action path bypasses it.
3. Real: NUC adaptive=True start_joint_impedance -> HybridJointImpedanceControl, actual gains captured in FINDINGS.md. It differs from both paths above.

Important correction to the earlier diagnosis: computed torque describes the BC SIM EVALUATOR, not the controller that GENERATED these demonstrations.

## Action and observation alignment

_TraceRecorder.step records RGB and proprio BEFORE real_step, then records `_joint_position_action` AFTER the physics step. BC arm action is the next measured expert joint position, not the expert's internal Cartesian target nor a torque command. Binary gripper action is the commanded open/close setpoint at issue time; state width is measured unless --feedback-width is explicitly enabled.

This pairing is intentional, not an accidental one-frame shift. But feeding next-state endpoints to a compliant real joint servo does not guarantee reaching those endpoints on the next 30 Hz step. It can accumulate lag or retain tracking offsets. Latest real no-close rollout had ~7.85 mm mean TCP target/measurement difference in its last 10 seconds. This supports investigating transfer mismatch, not a proof of causality.

## Deployment discrepancies

- Width observation: documented sim training/eval uses measured width. `wmrl/real/eval_session.py` currently supplies virtual width when not holding. This is a different proprioceptive input, particularly during the 1.08s physical gripper latency. Provide an explicit measured-width option and select it for this checkpoint; keep virtual mode for older real-trained checkpoints.
- Task: session launcher defaults to `medium_pick_place_insert_sdf.yaml`, while dataset uses the round20 physical-release/strict task family. The latter pins loose-gear yaw pi (hole robot-left), seated gear yaw pi, repeat=2 and additional physics settings. The old task uses loose yaw 0. The launcher/seed nominals should use `medium_pick_place_eval_round20_strict.yaml`; physical placement should match those views. Repeat affects simulation only, not the live 30 Hz loop.
- Collection TSI, simulated computed torque, and physical hybrid impedance are different dynamics. Do not copy numerical gains between them and call it matching. First compare measured-width deployment and correct scene placement; then quantify controller tracking using matched trajectories and real feedback.

Gripper execution thresholds match: binary close below .045 m, open above .055 m, modeled latency 1.08s. The latest real policy output stayed above .0796 m throughout, so the close decision was never issued. Earlier logged hardware closes succeeded. Changing the close threshold would force a behavior the policy did not request and would not resolve the transfer discrepancy.

## Verification

Resolved both collection and strict evaluation YAML inheritance and instantiated both environments without moving the robot. Verified collection TSI mode and 30 Hz repeat; verified strict eval joint mode and computed-torque configuration. Compared all archived probe hashes. Five local WB regression tests still pass; git diff --check passes after merging.

No full recollection/training job or policy rollout was run. This review identifies the actual chain and mismatches; it does not claim these changes alone will restore successful grasping.
