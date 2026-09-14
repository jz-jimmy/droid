# Real rollout controller mismatch — handover, 2026-09-14

## Start here

Next steps: [Controller alignment deployment plan](CONTROLLER_ALIGNMENT_DEPLOYMENT_PLAN.md)
contains the detailed staged diagnosis, logging contract, physical/simulation
validation gates, completed Delta replay results, and recollection decision process.
It also distinguishes proposed work and Delta-local changes from this snapshot.

This branch is a **sanitized deployment snapshot**, prepared from the robot NUC after the September 12 real-world tests. It contains the DROID source, the deployed fork of Polymetis with its nested dependencies vendored, the separate WMRL bridge, and compact real-rollout evidence. Nothing starts a robot automatically.

```bash
git clone --branch real-rollout-20260914 https://github.com/jz-jimmy/droid.git
cd droid
python -m venv .venv
. .venv/bin/activate
pip install numpy
python deployment/analyze_rollouts.py
```

No recursive submodule initialization is needed for this snapshot. Original submodule mappings are preserved as `VENDORED_SUBMODULES.txt`. The branch has fresh history because the NUC's original DROID history contains a hard-coded sudo credential. Existing destination branches are not overwritten. The sole intentional DROID source sanitization replaces that credential with `os.environ.get("DROID_SUDO_PASSWORD", "")`; configure credentials locally if using legacy DROID launch helpers. Never store them in Git. The NUC checkout itself was not changed.

## What was actually running

Workstation -> ZeroMQ -> NUC bridge -> Polymetis robot/gripper servers -> FR3 FCI.

- NUC: `robopil@128.59.17.233`, source `~/droid`, conda environment `polymetis-local`.
- Bridge: `~/wmrl_bridge/franka_bridge_polymetis.py`, port 5588. Its exact source snapshot is under `deployment/wmrl_bridge/`.
- Robot/gripper gRPC: ports 50051/50052. FCI last used `128.59.17.200`; Desk `https://128.59.17.200/desk/`. Confirm the address after robot reboot.
- Workstation: `jiayu@128.59.17.239` (robopil-zeta), workspace `/data/home_offload/jiayu/Sim2Real` (`/home/jiayu/Sim2Real` is an alias).
- Python used for rollout: `~/miniforge3/envs/industreal-mujoco-sdf/bin/python`.
- The live bridge bypasses DROID's `scripts/server/run_server.py` and `droid/franka/robot.py` high-level control. The local DROID gripper-release commit is included, but the WMRL bridge directly calls Polymetis gripper methods.
- On September 12, services were stopped at the user's request. Last measured robot state: task reset, TCP z approximately 0.17423 m, gripper open 0.078 m, streaming false. This is a historical state, not a guarantee of present robot status.

### Source provenance

| Component | Source revision |
|---|---|
| NUC DROID, branch minimum, upstream kywind/droid | `01d66a23913e6842ff20ac518e777b797abce97b` |
| NUC fairo, upstream kywind/fairo | `1ba0d856918c3e246702d9ff80e34d440c600bf6` |
| WMRL mujoco-gear, after latest pull | `7c0026b49d7040e87ba01864556325ee075fb98e` |
| industreal-mujoco robo01, after latest pull | `1e1371b4ccb5545f655fa565bf4483387fc20120` |
| LeRobot jz-jimmy/lerobot | `bdfc3d88515f5543706c80631fd5456181a4adcf` |

The rollout episodes preceded the final WMRL/industreal pull: their starting committed revisions were WMRL `46f691c`, industreal `60adcf8`, with session-only round20 camera overrides. WMRL's current uncommitted WB fixes and diagnostic logging changes are copied into `deployment/workstation/wmrl_real/`; they are not all present in upstream `7c0026b`. The newest logging additions were made AFTER the included episodes and do not retroactively add fields to their NPZs.

NUC fairo changes were limited to the two robot/gripper configuration YAMLs. Nested dependencies were clean when inspected; their exact revisions and the configuration diff are in `deployment/evidence/nuc/fairo-local-config.patch.txt`. `deployment/evidence/nuc/environment.yml` records the environment, not a guarantee that a fresh build reproduces installed binary behavior. Build artifacts are excluded; binary hashes are in `deployment/PROVENANCE.json`.

## The actual three controller paths

| Stage | Controller and action semantics |
|---|---|
| Scripted sim demo generation | Cartesian delta targets into **TSIController**; not the joint computed-torque servo |
| BC sim evaluation | Absolute 7-joint targets + total gripper width into **computed torque**, Kp=300/Kd=35 |
| Real policy rollout | Same 8D action representation into **HybridJointImpedanceControl**, with event-driven gripper |

BC is behavioral cloning; there is no torque controller running inside the ACT training optimizer. The controller difference concerns how demonstrations were generated and how predicted actions are executed.

### Demo generation

Use WMRL `docs/round20_recollect_20260908/physical_release_v3.md` and:

- `wmrl/scripts/collect_mujoco_gear_visuomotor_lerobot.py`
- `wmrl/scripts/collect_mujoco_gear_visuomotor_scripted.py`: `_TraceRecorder.step`, `_joint_position_action`, `_proprio`
- `wmrl/scripts/collect_mujoco_gear_pick_place_scripted.py`
- `wmrl/scripts/physical_stall_release.py`
- `wmrl/tasks/gear_assembly/medium_pick_place_collect_round20_physical_release.yaml` and its inherited tasks.

Collection uses TSI gains Kp `[300,300,1500,50,50,50]`, Kd `[38,38,70,1.8,1.8,1.8]`, 30 Hz environment steps through two 60 Hz internal updates, and approximately 120 Hz physics. Code: industreal-mujoco `src/industreal_mujoco/controller.py`.

The trace records images and measured proprioception **before** stepping, then records the **next measured joint positions** as the BC arm action. It does not record the expert's internal desired TCP pose as a joint target. Binary gripper action records the command at issue time; measured width lives in the observation. The collector optionally supports `--feedback-width`, default false; the experiment documentation explicitly says measured-width observation. Exact production argv/dataset metadata were not supplied in this workspace, so check them before treating that choice as independently verified for every exported episode. All nine source hashes in the archived mixed64 probe match the newly pulled scripts/configurations.

### Sim BC execution

WMRL task `medium_pick_place_eval_round20_strict.yaml` inherits `medium_pick_place_insert_rl.yaml`: computed-torque joint servo, binary gripper with 1.08 s modeled command latency, 30 Hz policy steps. Servo is approximately `M(q) @ (300*position_error - 35*qdot)`, clamped to +/-100 Nm before adding bias compensation. Thus the demo generator and BC sim evaluator already use different controllers.

### Real execution

`deployment/wmrl_bridge/franka_bridge_polymetis.py:start_stream` calls `RobotInterface.start_joint_impedance()` with default `adaptive=True`. In this fork that instantiates **HybridJointImpedanceControl**, not plain JointImpedanceControl. `step` streams `update_desired_joint_positions` at 30 Hz into the 1 kHz NUC controller.

Relevant vendored files under `droid/fairo/polymetis/polymetis/python/`:

- `polymetis/robot_interface.py`: start_joint_impedance, update_desired_joint_positions
- `torchcontrol/policies/impedance.py`: HybridJointImpedanceControl
- `torchcontrol/modules/feedback.py`: HybridJointSpacePD

Measured defaults from the running RobotInterface:

```
Kq  = [40,30,50,25,35,25,10]
Kqd = [4,6,5,5,3,2,1]
Kx  = [400,400,400,15,15,15]
Kxd = [37,37,37,2,2,2]
```

Feedback is `(J.T Kx J + Kq) @ error - (J.T Kxd J + Kqd) @ qdot`, plus Coriolis feedforward; hardware gravity compensation is enabled. Copying the sim's numerical gains into this different torque law does not establish equivalence. WMRL also has a sim `hybrid_impedance` alternative, but it deliberately uses inertia-scaled damping `M(q) @ (20*qdot)` for simulation stability, not the NUC damping law.

## Evidence: why the last rollout did not close

The latest included rollout is `1024sim_185600_round20_s1000000_20260912_011403`.

- 2700 steps; gripper command **79.632–82.246 mm** throughout.
- Real close threshold is **<45 mm**, reopen **>55 mm**. No close request occurred.
- Measured opening stayed about 78 mm; no bridge safety flags.
- Last 10 seconds: mean target minus measured TCP `[5.60,4.14,-3.61]` mm, norm about **7.85 mm**; max joint discrepancy 0.04789 rad. This is an analytic FK estimate, not an external pose measurement.
- Earlier chunked runs requested ~18 mm and hardware logs show successful close primitives. Measured width fell to ~0.56 mm, which establishes closure, not a successful object grasp.

The evidence rules out a dropped close command for the latest episode. Controller lag/compliance could affect the policy's decision, but is not proven causal. Do not raise the close threshold to force closure while the policy requests open.

The real bridge width request is binary/event-driven: GRASP at 5 N below 45 mm, OPEN above 55 mm. It uses this fork's existing `grasp_width=-0.2` call; see the vendored gripper implementation before treating it as a portable standard API value.

## Other mismatches and next experiments

1. **Measured vs virtual width:** `deployment/workstation/wmrl_real/eval_session.py` feeds virtual width until holding. Documented sim training/eval uses measured width. Add an explicit observation-mode option and select measured mode for this checkpoint; retain legacy virtual mode for checkpoints trained with it. Compare identical recorded frames/states offline first.
2. **Task/placement:** the saved launcher uses older `medium_pick_place_insert_sdf.yaml`. Training/strict eval uses loose-gear yaw pi, seated-gear yaw pi, and other real-parity settings. Use `medium_pick_place_eval_round20_strict.yaml` and regenerate seed nominals before the next deployment comparison. A matching round20 camera pose does not imply matching object orientation/physics.
3. **Endpoint labels vs compliance:** BC predicts measured next-state endpoints. Quantify how the physical joint servo follows those endpoints versus the sim servo using matched trajectories, including timing and steady-state residuals. Do not simply increase gains for contact trials.
4. **Image distribution:** WB reference was stale; right patch included the table edge/background. Local fixes reject empty patches/NaNs and wrong reference metadata, drain buffered frames, check the final retry, and use task-reset warmup. A new reference was saved locally. This establishes a reference, not proof of training-color match or successful repeatability across restarts.
5. **TE is a separate variable:** baseline is 100-action chunks with TE off. TE runs are labeled `_te`; one run has an ambiguous double-underscore tag, and old NPZs lack an explicit TE field. New local runner logs TE, commanded joints and gripper state. Do not infer mode solely from ambiguous tags.

No controller gain change, measured-width switch, corrected-task deployment, or successful post-fix policy rollout has been completed. These remain the next diagnostic steps.

## Companion repositories and artifacts

```bash
git clone --branch mujoco-gear https://github.com/WangYixuan12/wmrl.git
git clone --branch robo01 https://github.com/jz-jimmy/industreal-mujoco.git
git clone https://github.com/jz-jimmy/lerobot.git
```

Check out the revisions listed above for reproducibility. Place the repos as siblings. WMRL imports need `PYTHONPATH=/path/to/wmrl:/path/to/industreal-mujoco/src` and the matching LeRobot source/environment. Refer to WMRL's environment documentation. The saved `deployment/workstation/launch_eval.sh` is a historical machine-specific launcher, not a ready-to-run script on another host; it still needs the corrections listed above.

Included here:

- `deployment/evidence/rollouts/*/rollout.npz`: six small recordings with q, measured/feedback widths, actions, flags, wall-clock t, checkpoint and seed.
- `deployment/evidence/nuc/*20260912.log`: bridge events and gripper hardware calls.
- `deployment/evidence/latest_rollout.png`, `rollout_analysis.json`, and two historical diagnosis reports.
- `deployment/evidence/checkpoint_metadata/`: ACT configuration and processor JSON; `checkpoint_sha256.json` identifies all original checkpoint files.
- `deployment/workstation/wmrl_real/`: current local rollout/camera modifications, useful as a comparison snapshot, not an entire replacement package.
- `deployment/SHA256SUMS`: integrity hashes for regular snapshot files (excluding the manifest itself); `deployment/SYMLINKS.json` records source symlinks. Generated protobuf symlinks resolve after building Polymetis.

Not uploaded: large policy weights, rollout videos, training datasets, compiled libraries, caches, machine credential backups. Obtain weights and videos from the workstation if needed:

```bash
scp -r jiayu@128.59.17.239:/data/home_offload/jiayu/Sim2Real/wmrl/external/1024sim_demo_185600ckpt-20260912T040108Z-1-001 ./
# Video root (copy the relevant episode folders):
# /data/home_offload/jiayu/Sim2Real/wmrl/runs/mujoco_gear/real_rollouts/
```

Original training dataset path on the training server: `/work/hdd/bfqx/jzhou21/datasets/lerobot/round20_physical_v3_raw224_1024s`. Training output: `/work/nvme/bfqx/jzhou21/wmrl/runs/mujoco_gear/lerobot_act_round20_physical_v3_raw224`. Seek collection metadata, production job scripts and per-checkpoint selection results there. Seeds 1000000–1000015 were also used in checkpoint selection, so they are not an independent held-out test set.

## Build/robot bring-up notes

Read the original README and vendored Polymetis docs. The NUC used Python 3.8; workstation rollout used Python 3.11. Do not assume the workstation's GPU environment can build the NUC real-time stack. The workstation NVIDIA mismatch was resolved by reboot to driver 580.178.04; RTX4090 checkpoint inference then took ~6 ms/chunk after warmup.

For a future authorized physical session only: confirm Desk FCI and joint state; run robot/gripper launchers from a writable directory (`~/wmrl_bridge` avoided a root-owned Hydra outputs directory), authenticate sudo in the robot launch tty, then start the bridge. Bridge `ping` must report `dry_run=False`; read `get_state` before motion. The included files retain the NUC deployment paths and historical robot IP, so adapt them deliberately on a new host. Offline diagnosis requires none of these robot services.
