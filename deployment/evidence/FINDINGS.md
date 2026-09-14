# White balance and gripper diagnosis — September 12

## Latest rollout: 1024sim_185600_round20_s1000000_20260912_011403

2700 recorded steps (~90 seconds). Policy gripper output: 0.079632–0.082246 m. No sample crosses the bridge's <0.045 m close threshold. Measured width remains ~0.078 m. No safety flags. Therefore the bridge was receiving OPEN commands throughout, not dropping CLOSE commands.

The first two chunked sessions (005243 and 005547) DID request closure (minimum outputs 18.05/18.49 mm); measured widths fell to ~0.56 mm. NUC gripper logs confirm grasp success at 00:53:04 and 00:56:12. This validates the command route, not successful object grasp. TE sessions also never requested close.

Last 10 seconds of latest session: mean commanded minus measured TCP = [5.60, 4.14, -3.61] mm (norm ~7.85 mm); max joint error 0.04789 rad. Positions computed with verified analytic FK. Measured TCP mean [0.65148,0.05631,-0.18554]; commanded [0.65709,0.06046,-0.18915]. This is evidence of tracking mismatch, not proof it caused the policy to stay open. The target is raw policy output; no filter flags were recorded in this session.

## Controller comparison

The checkpoint is ACT behavioral cloning, not an online torque controller. Training config identifies local/round20_physical_v3_raw224 and remote dataset path /work/hdd/bfqx/jzhou21/datasets/lerobot/round20_physical_v3_raw224_1024s, but lacks demonstration collection controller provenance. User asked for this missing config.

Local LeRobot SDF task inherits computed-torque control: tau = M(q)[300*(q_target-q)-35*qdot], clamp servo at 100 Nm, then add bias. Joint target in radians, 8th action total finger width in meters. Binary gripper inherited from medium_unified_state: close <45 mm, open >55 mm, 1.08s command delay. Live bridge uses those same width thresholds and a force-holding grasp primitive; it is not a continuous-width servo.

NUC start_joint_impedance defaults adaptive=True -> HybridJointImpedanceControl. Actual gains read from RobotInterface:
Kq=[40,30,50,25,35,25,10], Kqd=[4,6,5,5,3,2,1],
Kx=[400,400,400,15,15,15], Kxd=[37,37,37,2,2,2].
Feedback torque = (J.T Kx J + Kq)*position_error - (J.T Kxd J + Kqd)*qdot; inverse dynamics contributes Coriolis, hardware supplies gravity compensation.

Thus the local default sim and actual robot controllers differ. A local hybrid_impedance alternative has matching stiffness but intentionally inertia-scaled damping M(q)*20*qdot for sim stability, not the NUC damping law. Whether this particular training dataset used either mode remains unverified. No live gain changes were made: matching numbers across these different torque laws would not establish equivalence. Neither controller decides when ACT issues a close command.

## White-balance causes and changes

Old camera_wb_reference.json last changed at a77b425, before round20 and current physical adjustments. Right patch [y500:700,x100:500] now includes table edge/background. The saved color ratio is not a reliable target for this scene. Eval also warmed at hover, while reference-refresh happens at task reset: scene-dependent AWB had inconsistent startup pose.

Bugs fixed:
- Empty/out-of-frame patches and nonfinite ratios cannot falsely pass (old 848x480 code averaged an empty patch and NaN comparisons passed).
- Reference metadata must match serial, resolution and task-reset convention. Legacy references require explicit refresh; repeated AWB retries cannot fix them.
- Drain 8 frames after manual settings before validation.
- Check final reconvergence result (5 checks, at most 4 retries).
- Evaluation starts camera warmup at task reset, matching reference-save pose; removed unused shadow-arm warmup setup.
- Right reference patch moved entirely onto table: [y500:650,x700:1000].
- CLI and overlay j reference writers share bounds checks and metadata.
- New rollout logs record post-filter commanded joints, gripper state, actual TE mode/action-step count and WB status.

Five regression tests PASS, including final-retry success, tint mismatch, empty patch, wrong serial/resolution, legacy metadata, NaN and dark patch checks. Live preview confirms stale references are reported honestly without four futile retries.

Remaining WB calibration step: inspect the open 'WB review - NO ROBOT MOTION' window and press j to accept/save current colors (q cancels). Original reference is backed up by the diagnostic preview. Then repeat camera startup to verify session-to-session convergence; do not claim this passed until tested. New reference establishes consistency, not proof of matching training image colors.

Artifacts: latest_rollout.png, rollout_analysis.json, left_static_patch.png, right_static_patch.png. The idle eval was closed for camera access; robot stayed at task reset with gripper open and streaming false. No policy rollout or gain experiment was executed during diagnosis.
