# Controller alignment and recollection decision: deployment handoff

Date: 2026-09-14. Audience: the agent working on the real workstation/NUC and
the agent maintaining the Delta simulation/training pipeline.

## 1. Objective and current decision

Establish a common, measured control contract for real demonstration collection,
real ACT execution, sim demonstration collection, and raw/augmented-sim ACT
evaluation. Then decide whether to regenerate the 1,024 sim demonstrations.

**Keep demo styling paused. Preserve the existing datasets, ACT checkpoint and
Cosmos checkpoint. Start with the deployed compliant controller as the reference
to investigate; do not copy the simulation's 300/35 gains onto the robot.**

The immediate deliverable is a reproducible diagnosis and a validated pilot,
not a new full dataset. Existing sim success does not establish physical tracking
or contact fidelity. Conversely, zero real success does not by itself isolate
the controller as the sole cause.

This document is a plan, not a motion launcher. Publishing it does not authorize
robot motion, controller restarts, gain changes, deletion, or another Slurm
allocation. The deployment agent should complete read-only inspection, local
implementation and offline checks first, then use the operator's authorization
for the specific physical session. Preserve existing robot safeguards. On Delta,
every new allocation still needs the user's explicit approval; no Python probes,
simulation, data processing or inference may run on its shared login node.

### Completion criteria

- Controller identity, model/frame, timing, gripper behavior and observation
  semantics are explicit and recorded for every experimental condition.
- Matched real/sim trajectories explain or materially reduce the observed
  tracking discrepancy, with limitations quantified.
- A common controller path accepts both expert-generated joint commands and ACT
  joint commands; replayable command labels are retained.
- A small collection/replay/ACT pilot passes before scaling.
- A written reuse/recollection decision distinguishes the old baseline from the
  new experiment. No historical success rate is silently redefined.

## 2. Evidence and provenance to start from

Read [the deployment handover](CONTROLLER_MISMATCH_HANDOVER.md) first. The
snapshot was inspected at DROID commit
`72fcdd2d3b377a4b9216a04a26fd85b3b5094f5a` on branch
`real-rollout-20260914`. See [PROVENANCE.json](deployment/PROVENANCE.json) for
the source/binary provenance. A source snapshot does not prove what is running
now; services were stopped after the recorded September 12 session.

Relevant revisions at the Delta audit:

| Repository | Revision |
| --- | --- |
| WMRL, mujoco-gear | `7c0026b49d7040e87ba01864556325ee075fb98e` |
| industreal-mujoco, robo01 | `1e1371b4ccb5545f655fa565bf4483387fc20120` |
| LeRobot on Delta | `a3f843ca5506b024f13b5cd6725124a21c664ec2` |
| LeRobot in real deployment handover | `bdfc3d88515f5543706c80631fd5456181a4adcf` |

Do not assume the two LeRobot revisions or the workstation's uncommitted files
are equivalent. The historical episodes preceded the final WMRL/industreal pull;
their starting revisions and session camera overrides are described in the
handover. WB/diagnostic changes in the snapshot include changes made after the
episodes. Do not claim those fixes were tested in those recordings.

### Confirmed findings

| Path | Arm path | Labels/observations |
| --- | --- | --- |
| Scripted sim demos | Cartesian delta planner → TSI torque | Pre-step images and measured q/width; **post-step measured q** as arm labels |
| Raw and augmented sim ACT | Absolute q targets → computed torque | Measured width; 30 Hz, 100-action chunks, TE off in current selection |
| WMRL scripted real demos | Cartesian planner with tracking compensation → commanded-state DiffIK → bridge → hybrid impedance | Historically requested `q_cmd` plus command width; measured state |
| Recorded real ACT | Absolute q targets → bridge → hybrid impedance | Historically virtual width until the bridge latch/width rule switches to measured |

Real video collection provenance must still be pinned per dataset. The original
100-real-demo handover says scripted collection; do not assume the later Cosmos
v4/v5 inputs came from exactly the same collector revision. If a set used DROID
teleoperation, inspect that separate 15 Hz target-generation path as well.

The latest supplied episode,
`1024sim_185600_round20_s1000000_20260912_011403`, requested widths
79.632–82.246 mm for all 2,700 actions. It never crossed the <45 mm close
threshold. Mean target-minus-measured FK displacement during its last ten
seconds was `[5.601, 4.143, -3.607]` mm. Earlier runs did request closure.
These observations establish neither successful grasping nor why the later
policy kept requesting open. Do not increase the close threshold to force it.

### Completed Delta diagnostic

User-approved Slurm job **3151839** completed on **gh027**, exit 0, in 13 seconds:

- Eight offline tests passed for measured/legacy width, explicit command-label
  selection, and invalid/sparse/reordered sample rejection.
- `deployment/analyze_rollouts.py` reproduced the six archived episode summaries.
- A 600-action replay of the latest no-close episode yielded:

| Existing sim servo | TCP trajectory RMSE vs recorded real FK | Final sim-minus-real TCP, mm |
| --- | --- | --- |
| computed_torque | 10.1588 mm | `[5.6543, 6.7854, -5.7001]` |
| existing hybrid approximation | 9.4250 mm | `[5.7049, 6.7885, -5.4978]` |

Real median/max sample intervals were 33.411/33.693 ms. Sim policy/servo
intervals were 33.3332/16.6666 ms. Initial real velocity was unavailable and set
to zero; objects used nominal sim placement; there was no camera feedback.
Neither replay terminated in 600 steps. This is an open-loop sensitivity check,
not a closed-loop ACT result or an identified physical controller. The similar
remaining offsets do not validate the existing hybrid option as the fix.

The output is on Delta at
`/work/nvme/bfqx/jzhou21/wmrl/runs/mujoco_gear/controller_audit_3151839/summary.json`;
the job output is `jobs/controller_audit_3151839.log` under that workspace.
Those generated artifacts are not bundled into this DROID plan.

## 3. Preserve the assets and separate implementation from deployment

The following dataset directories and metadata were checked on Delta; this was
not a complete video-integrity check:

| Asset | Path under `/work/hdd/bfqx/jzhou21/datasets/` | Metadata |
| --- | --- | --- |
| Original real LeRobot demos | `real_demos_lerobot` | 100 episodes, 315,752 frames |
| Real v4 LeRobot demos | `real_demos_v4_lerobot` | 100 episodes, 107,097 frames |
| Current sim LeRobot demos | `lerobot/round20_physical_v3_raw224_1024s` | 1,024 episodes, 1,430,940 frames |

The local `wmrl/act-real-demos/raw` on Delta contains only a handover document;
it is not proof that original raw videos are locally present. Locate the raw
recordings on the workstation and record their provenance. The
`post-train-dataset_v4` directory also exists on Delta, but its full contents were
not verified in this audit.

Keep ACT step 185600 and its processors unchanged for diagnosis. Its selection
record shows 48/48 raw-sim successes. Keep Cosmos Nano multi-v2 iter1000; changing
control labels does not automatically invalidate a video-only style model.
New motion/appearance distributions may still need a later coverage check.

### Local source changes that are NOT in this DROID deployment snapshot

The Delta WMRL checkout contains uncommitted work. This plan does not ship those
Python changes, and pulling this DROID branch will not install them:

- `wmrl/real/gripper_observation.py`: explicit measured or legacy virtual mode.
- `wmrl/real/eval_session.py`, `real_rollout.py`: measured width by default,
  chunked baseline, additional settings/state logging; eval_session's task
  default changed to strict round20.
- `wmrl/real/scripted_session.py`: retain requested `q_cmd` and bridge-returned
  `q_commanded`, velocity, flags, gripper latch, observation/command timestamps;
  select latest camera frames before issuing the action.
- `wmrl/scripts/convert_real_demos_to_lerobot.py`: measured width and filtered
  commands by default; historical episodes require explicit
  `--arm-action-source requested`; sparse/reordered frame ticks are rejected;
  output records its control contract. Legacy virtual conversion remains an
  approximation of asynchronous gripper events.
- `wmrl/real/tests/`: the eight passing offline contract tests.
- `wmrl/scripts/probe_controller_replay.py`: the existing-servo replay probe.

Obtain a reviewed source patch from the Delta agent or port these requirements
deliberately. Preserve the workstation's newer camera/WB changes. Do not replace
its package wholesale with the partial `deployment/workstation/wmrl_real/` copy.
Build an integration branch, inspect its diff, and test the actual launcher.

## 4. Phase A — establish what actually runs, without motion

**Owner: deployment agent. Output: `baseline_manifest.json` and a source diff.**

1. Record working directories, git SHAs, dirty diffs, Python/environment identity,
   imported module paths, policy/processor hashes and the exact launcher argv.
   Hash named relevant artifacts deliberately; do not index the whole workspace.
2. Identify which workstation entry point executes: the saved launcher uses a
   wrapper `eval_round20.py`, which alters cameras and warms ACT before calling
   `eval_session`. Its effective arguments matter more than package defaults.
   Record the camera overrides and verify the warmup resets the action queue.
3. Locate the live bridge file and its imported `constants.py`/FK file. Verify
   which process owns the bridge port and whether it is a dry-run instance.
   Record service state without starting/restarting it as part of inspection.
4. Inspect the installed Polymetis model and configuration, not only source YAML.
   Record `ee_link_name`, gain arrays, frequency, gravity/payload configuration,
   rate limiting, filter cutoff, safety limits and installed binary provenance.
   The config names `panda_arm.urdf` while the rig is described as FR3; determine
   the actual model/tool parameters before calling this a model error.
5. Establish active controller identity. `start_stream` currently reuses an
   already-running policy without checking its type, and catches startup errors
   by trying another entry point. A future startup must explicitly establish or
   verify the expected policy and report the result. Do not terminate an unknown
   live controller automatically while merely collecting metadata.
6. Identify the collector/revision for each real dataset and the source episodes
   used for Cosmos. Record whether episodes include preinsert-only or full task
   runs, cropping changes, frame alignment conventions, and calibration rounds.

**Gate A:** explain the exact import/launch/controller chain. Unknown live fields
are recorded as unknown, not filled from defaults. Resolve these before claiming
a simulator replica.

### Files to inspect

- [Live bridge snapshot](deployment/wmrl_bridge/franka_bridge_polymetis.py),
  [constants](deployment/wmrl_bridge/constants.py), [FK](deployment/wmrl_bridge/franka_fk.py).
- [Historical launcher](deployment/workstation/launch_eval.sh),
  [round20 wrapper](deployment/workstation/eval_round20.py),
  [real evaluator snapshot](deployment/workstation/wmrl_real/eval_session.py).
- [Polymetis interface](droid/fairo/polymetis/polymetis/python/polymetis/robot_interface.py),
  [impedance policies](droid/fairo/polymetis/polymetis/python/torchcontrol/policies/impedance.py),
  [feedback law](droid/fairo/polymetis/polymetis/python/torchcontrol/modules/feedback.py),
  [gripper interface](droid/fairo/polymetis/polymetis/python/polymetis/gripper_interface.py).
- [Hardware configuration](droid/fairo/polymetis/polymetis/conf/robot_client/franka_hardware.yaml),
  [model configuration](droid/fairo/polymetis/polymetis/conf/robot_model/franka_panda.yaml).

## 5. Phase B — freeze the common contract and instrument it

**Owner: both agents. Output: versioned contract, offline tests, run manifest.**

### Target contract

| Component | Baseline choice |
| --- | --- |
| Policy state | Measured q, seven radians, plus measured total finger width in meters |
| Arm action | Absolute seven-joint position target; save both requested and bridge-filtered target |
| Gripper action | Physical-width intent interpreted by explicit 45/55 mm hysteresis |
| Policy cadence | 30 Hz target updates; 100-action ACT chunks; TE off |
| Cameras | Named three-camera mapping, calibrated crop/intrinsics/extrinsics, 224-pixel policy inputs |
| Sample ordering | State/images available before command; capture timestamps retained separately |
| Initial actuator reference | Verified deployed hybrid controller and deployed 5 N grasp |
| Task | Explicit versioned reset/nominals and success definition |

Measured width is the choice for the current sim-trained checkpoint. Do not
silently change feedback width for legacy real-trained policies; mark those
experiments explicitly. Preserve requested policy outputs before clipping so a
later analyst can see what the policy actually decided.

### Required recording

Every episode should save a manifest plus aligned sample/event streams:

- Policy checkpoint and processor identity; source SHAs/diff identifiers; full
  effective config; seed; task/nominal/calibration IDs; controller profile ID.
- Requested action, filtered joint command returned by bridge, measured q/dq,
  actual width, width used in the observation, bridge flags and success/error.
- Host monotonic timestamps for observation receipt, inference start/end,
  command send/acknowledgment; robot timestamps for measured states when exposed.
  Do not subtract unsynchronized host and robot clocks as if they shared an epoch.
- Camera frame number, device timestamp and host receipt time for every camera;
  the exact frame IDs consumed by each ACT chunk. Save the consumed images.
- Chunk index/action index, actual action intervals, overruns and missed deadlines.
  Never hide overruns by reporting nominal 30 Hz alone or issue a burst of queued
  actions merely to catch up with an old deadline.
- Controller torque stages when available: requested policy torque, safened/
  filtered torque, measured torque, with timestamp and gravity conventions.
  Keep 1 kHz logging off the time-critical path using existing buffered facilities;
  benchmark logging overhead before enabling dense traces.
- Gripper intent, primitive issue/start/completion/error timestamps, measured
  opening, and hardware success/grasp indicators where available.

The bridge's `gripper_state=closed` is an intent latch set before completion. Give
intent and hardware outcome separate names. A queued RPC is not proof of physical
motion. Do not infer force holding from width alone.

Use one versioned action convention for the new dataset. Recommended labels are
the target actually passed through the shared filter to the arm controller;
retain requested targets and measured next state as diagnostics. Ensure replay
applies the same filter convention and verify its effect rather than assuming
that a second filter pass is harmless.

**Gate B:** synthetic/offline tests cover units/order, measured width during
gripper latency, clipping, invalid replies, episode reset/chunk reset, frame-tick
alignment, and logs of the command actually issued. Any field unavailable on the
hardware is explicitly marked unavailable. Old archives remain immutable.

## 6. Phase C — correct task and observation inputs before causal claims

**Owner: deployment agent, with simulation agent supplying resolved configs.**

1. Use the explicitly resolved
   `medium_pick_place_eval_round20_strict.yaml` configuration for the historical
   comparison. The saved launcher/nominals used an older task context. Merely
   changing a parser default does not replace existing nominal files.
2. Regenerate nominals in a new directory from that exact task and pinned source.
   Record generation arguments and resolved configuration. Compare gear yaw,
   base pose, loose/seated gear placement, reset q/TCP and camera extrinsics.
3. Preserve the appropriate round20 camera overrides for the current checkpoint;
   later camera calibrations are separate conditions. Use the existing overlay
   gate to check object placement and geometry before an authorized rollout.
4. Integrate the snapshot's WB fixes deliberately. Reject invalid reference
   metadata and empty/NaN patches, drain stale frames, warm cameras at the actual
   task reset, and log the chosen reference. Re-blessing WB is not evidence that
   colors match the training distribution; compare saved consumed images.
5. Replay identical stored images/states through ACT offline with measured vs
   legacy width, the same processors, chunk resets and execution configuration.
   Report changes in joint predictions and the first close request. This isolates
   the software observation change from physical feedback; it does not predict
   closed-loop success. For the latest all-open run, both width signals remain
   near open, so a large improvement is not assumed.

**Gate C:** a new launcher has explicit task, nominal path, calibration identity,
measured-width setting and TE-off setting. Its imported code is verified. It
does not silently inherit old wrapper arguments. Offline inference outputs and
consumed image samples are saved before testing the robot.

## 7. Phase D — identify arm tracking in free space

**Owner: deployment agent, during an operator-authorized physical session.**

Start without an object or contact. Use the verified deployed gains unchanged.
Choose short, smooth, bounded trajectories entirely within an operator-checked
free-space region; determine amplitudes, speed and clearance from the rig and
existing limits. Do not use this document as a generic numeric motion command.

### Experiments

1. At several safe task-relevant poses, hold a constant target long enough to
   distinguish transient lag from steady residual. Include poses above the
   approach and preinsert regions, with no table/object contact.
2. Execute small smooth motions in each relevant direction and back to the
   starting pose. Use gradual trajectories before considering any step response.
   Repeat at least three times to estimate repeatability and direction dependence.
3. Replay a vetted, free-space portion of an expert command trajectory. Compare
   recorded **commands**, not only measured endpoints. Exclude unreviewed contact
   segments of old full-task episodes from this stage.
4. Repeat with the proposed observation/logging pipeline active to measure
   inference/camera/network overhead. Keep gains and trajectory fixed.

For each experiment report q tracking, FK TCP tracking, transient lag, settling,
overshoot, steady residual, velocity, flags and saturation. FK comparisons use
the same TCP transform for command and measurement; obtain independent physical
pose checks where possible. FK consistency alone cannot validate calibration.

Diagnostic interpretation:

- Persistent hold error suggests compliance, friction, model/payload or gravity
  issues worth separating; it is not automatically a rate problem.
- Error during motion but little hold error suggests bandwidth, delay or filtering.
- Direction/load dependence motivates friction/payload investigation.
- Command clamps identify a target/filter mismatch before any torque-law tuning.
- Timing/camera staleness with otherwise good tracking is a separate policy-input
  issue. Preserve it as a separate condition in subsequent trials.

**Gate D:** choose task-relevant tracking tolerances from geometry and repeatability
before comparing controller candidates. The real expert contains submillimeter
alignment gates; a several-millimeter residual cannot simply be waved through.
Avoid declaring a universal 1 mm threshold without checking calibration noise,
clearances, and task stage. Pass requires repeatable bounded behavior, no unexplained
safety intervention, and residual/lag small enough for the next stage's geometric
margin. Otherwise resolve model/tool/load/timing issues and repeat this stage.

## 8. Phase E — characterize the gripper separately

**Owner: deployment agent. Keep the deployed thresholds and 5 N grasp initially.**

In the authorized session, first test empty opening/closing, then a supervised
grasp of the task object. Measure request-to-motion onset, travel time, terminal
width, and holding/release behavior separately for close and open. Do not assume
the same 1.08-second delay or travel rate in both directions.

Inspect the full asynchronous chain: bridge worker → Polymetis command queue →
server → physical primitive. The current worker uses `blocking=False`, has a
zero refractory interval, and updates its latch before completion. Determine
what happens to reversals issued while busy; do not provoke rapid chatter on the
robot merely to find out. Start with source/offline queue tests and then paced,
operator-reviewed reversals. Distinguish queued, superseded, failed and completed
commands. Ensure the final desired state is not silently lost.

DROID's higher-level code contains a stop-before-open path after a grasp, while
the deployed bridge calls gripper methods directly. Determine whether release
requires that behavior on the installed fork using source and paced physical
evidence; do not blindly copy the high-level path. The negative `grasp_width`
value is fork-specific and is not a portable standard API setting.

**Gate E:** a documented event/state diagram and measured onset/travel/holding
behavior. The sim gripper should reproduce those observables within stated
tolerances. A delayed position ramp with the same thresholds is insufficient
evidence of force-holding or reversal parity.

## 9. Phase F — implement and validate a separate sim reference profile

**Owner: simulation agent; deployment agent supplies Phase A/D/E traces.**

Retain `computed_torque` and the existing hybrid approximation for historical
reproduction. Add a new versioned, opt-in controller profile; do not change old
task defaults or label an approximation as hardware parity.

The inspected deployed feedback law, with zero desired joint velocity, is:

```text
Kp(q) = J(q).T Kx J(q) + Kq
Kd(q) = J(q).T Kxd J(q) + Kqd
tau_feedback = Kp(q) (q_target - q) - Kd(q) qdot
```

Inspected defaults:

```text
Kq  = [40,30,50,25,35,25,10]
Kqd = [4,6,5,5,3,2,1]
Kx  = [400,400,400,15,15,15]
Kxd = [37,37,37,2,2,2]
```

The policy adds inverse-dynamics feedforward at zero desired acceleration with
gravity removed; hardware provides gravity compensation. The configured Jacobian
reference is `panda_link8`, not the fingertip TCP. Verify row order, frame axes,
reference point and installed model. The fingertip TCP remains useful for task
metrics even when the control Jacobian uses the flange.

The existing sim hybrid instead uses `M(q) @ (20*qdot)` damping and a uniform
87 Nm clamp after bias. It is not the law above. The inspected hardware config
specifies `[86,86,86,86,11.5,11.5,11.5]` torque bounds, 100 Hz low-pass filtering,
rate limiting, a safety controller and 1 kHz execution. Inspect the client code
to reproduce the actual order and compensation conventions. Do not apply these
bounds blindly to total gravity-inclusive MuJoCo torque.

### Implementation and validation order

1. Unit-test feedback algebra against the inspected Polymetis implementation at
   identical q, dq, target and Jacobian. Test joint ordering, frame translation,
   gain units and limit/filter stages independently.
2. Separate 30 Hz target updates from fast servo/physics integration. The current
   60 Hz torque update held across approximately 120 Hz physics is not a faithful
   discretization. Run a timestep/servo-rate convergence study while holding the
   target schedule and physical duration fixed. Account for noninteger ratios.
3. Validate free-space trajectories from Phase D before contact. Align actual
   timestamps and initial q/dq; do not repeatedly pin the simulated arm to real
   q during this dynamics comparison. Render-only pinning is for image matching,
   not controller identification.
4. Compare multiple poses/directions and reserve some trajectories as a holdout.
   Fit identifiable delay/model/friction terms only with reported uncertainty;
   avoid fitting every mismatch by altering stiffness. Report remaining residuals.
5. Add the measured gripper event model and then controlled contact cases. Audit
   simulator assistance, contact parameters, passive damping, actuator limits
   and planar/hold hooks that could mask errors.
6. Wire the new profile through `wmrl/lerobot/features.py` validation, Gym adapter,
   collection, replay, metadata and both raw/augsim entry points. The current
   LeRobot validator rejects a non-computed-torque mode; a YAML-only switch will
   not suffice. Serialize the full profile, not just `kp/kd` fields it ignores.

**Gate F:** agreement on withheld tracking traces within preregistered task
tolerances, converged numerical behavior, reproducible contact/gripper tests,
and explicit limitations. If the physical controller itself changes, version
that condition and reidentify it; do not call old/new traces the same baseline.

## 10. Phase G — execute the expert through that same actuator path

**Owner: both agents. Output: a small new command-labeled dataset.**

The high-level expert may remain a Cartesian state machine. Change its output
path so Cartesian targets produce joint targets through an explicit IK/target
generator, then the same filters and arm/gripper controller used by ACT.

Choose and document IK integration state, nullspace bias, target lead limits
and any tracking compensation. The real WMRL expert integrates commanded q and
contains grasp/preinsert compensation; the sim TSI expert currently uses a
different mechanism. Do not reuse its numeric lead/compensation values without
checking behavior under the new actuator.

Record at each tick:

```text
observation: available pre-command images, measured q and width
requested action: planner/IK joint target and gripper intent
applied action label: target accepted by the shared command/filter path
diagnostics: measured next q, target TCP, stage, contact/gripper events, timing
```

If finer-rate target interpolation occurs, retain its parameters or stream so
the 30 Hz action sequence reconstructs the same actuator inputs. Never substitute
measured next q for a command label. At contact, different commanded deflections
can produce the same measured endpoint but different forces.

Start with a proposed **16–32 episode pilot**, covering representative placement
extremes, approach/grasp/lift, insertion, ordinary release and stall recovery if
that remains in the scientific task. The pilot size is a proposal, not approval
for collection or compute. Track all attempts and failures, not just retained
successes. Keep real/sim reset and object geometry documented.

### Replay gate before training

Replay each pilot's action labels from its saved initial condition using the
same controller/filter/event model, with the expert disabled. Compare per-stage
trajectory, gripper events and outcome. In deterministic same-state sim replay,
require all retained demonstrations to reproduce their intended success; any
failure is investigated before scaling. With stochastic components, save their
state/seeds and characterize repeatability explicitly.

Check dataset round-trip sample ordering, units, commanded-vs-measured fields,
episode-end padding and contiguous 30 Hz timing. Do not drop static frames or
subsample actions to remove pauses; that changes chunk execution time.

**Gate G:** replayable pilot and aligned labels. Visually convincing expert
videos alone do not pass this gate.

## 11. Phase H — pilot ACT and controlled real comparisons

**Owner: simulation agent for training/eval; deployment agent for real tests.**

1. First evaluate the existing ACT checkpoint under old and new sim profiles to
   quantify controller sensitivity without retraining. Its old endpoint labels
   may be inappropriate for the new profile; failure is diagnostic, not proof
   that the new controller is worse.
2. Train a pilot ACT on new command-labeled raw demos. Verify a train-scene
   overfit/replay sanity check, then evaluate held-out pilot placements. Do not
   demand full generalization from 16–32 examples or confuse overfit success
   with a deployment result.
3. In authorized real trials compare clearly labeled conditions. A useful order:
   existing ACT with corrected inputs and unchanged verified real controller;
   then pilot ACT under the same controller. Keep cameras, task, width, TE and
   timing fixed. If controller parameters change, add a separate condition.
4. Begin with a small diagnostic placement set and repeated trials. Report
   approach error, first close request, grasp/lift/engagement/release progression,
   tracking and event logs in addition to binary success. Preserve failed runs.
5. Use ablations one factor at a time where feasible. A simultaneous camera,
   nominal, width and controller change can test an integrated candidate but
   cannot attribute its improvement to the controller alone.

Proposed evidence matrix:

| Policy | Observation domain | Controller | Purpose |
| --- | --- | --- | --- |
| Existing 185600 | Raw sim | Historical computed torque | Reproduction baseline |
| Existing 185600 | Raw sim | New reference profile | Controller sensitivity |
| Existing 185600 | Real, corrected inputs | Verified real reference | Input correction diagnostic |
| Pilot command-labeled ACT | Raw sim | New reference profile | New contract learnability |
| Pilot command-labeled ACT | Real | Same verified real reference | Physical transfer pilot |

**Gate H:** learnable/replayable commands, repeatable stage progression, and
physical tracking adequate for the task. If a pilot has insufficient visual
coverage but passes controller/replay gates, collect targeted examples before
diagnosing a new controller failure. Report uncertainty and trial counts.

## 12. Phase I — decide reuse versus recollection

Write `recollection_decision.md` containing the evidence, accepted contract,
remaining discrepancies and one of these decisions:

| Finding | Decision |
| --- | --- |
| Observation/launcher fixes suffice, and old-label replay/tracking meets the chosen task contract | Retain old demos as a candidate baseline; document why recollection is unnecessary |
| New actuator response requires different target lead/contact commands or changes object trajectories | Recollect under the new shared path after pilot gates |
| Model/frame/timing/contact mismatch remains unexplained | Continue diagnosis; do not scale collection or styling |
| Old data has enough extra information for a proposed retargeting | Treat retargeting as a separate method; validate command replay and resulting observations before adopting it |

The current recommendation is **likely recollection for the definitive aligned
experiment, conditional on pilot success**. Old endpoint labels alone do not
identify the original force-producing commands. Attaching different labels to
old videos can also mismatch the new object trajectory. Preserve those demos
for historical baselines and analysis even if a replacement is collected.

Do not retrain Cosmos solely because action semantics changed. Inspect whether
the new sim motions/views remain supported by its real-video training set.

## 13. Phase J — restore the raw/styled research comparison

Only after the controller/pilot decision, obtain authorization for full
collection, training and styling. Use distinct dataset/run names with controller
and label-contract versions; do not overwrite `round20_physical_v3` outputs.

### Preserve what AugSim actually measures

The active path is:

```text
jobs/cosmos3/cosmos3_eval_closedloop.sbatch
  → augsim/eval/run.py
  → WMRL LeRobot Gym adapter
  → ObsStyler / optional CausalStyler
  → normal MuJoCo dynamics and ACT action execution
```

In independent mode, current-state RGB/seg/depth controls are repeated into a
25-frame clip and frame 0 is used. Each camera is generated separately. Styling
occurs at 0,100,200,... and ACT consumes that image when its 100-action queue
empties. Physics does not advance during blocking inference. This evaluates
changed visual observations under sim dynamics, not real-time model latency.

The opt-in causal endpoint mode uses only the already-executed interval and its
previous anchor; it is a distinct condition. An offline autoregressive video is
not evidence of the images consumed by the policy. Save temporal mode and exact
consumed endpoints. No future simulator controls may enter an earlier policy
observation.

For the canceled independent training plan, held frames between decisions are
storage placeholders. The sampler chooses only decision observations while
retaining every action target. Control this sampling difference in raw-vs-styled
training too; otherwise image domain is not the only changed variable.

### Selection and success protocol

- The actual old 1,024-demo plan screened all 18 checkpoints on seeds
  1000000–1000015, confirmed three finalists on 1000016–1000047, and selected by
  combined successes, confirmation successes, then later step. Validation loss
  was report-only. These 48 seeds are selection data.
- The user described a validation-loss shortlist → sim selection → AugSim SOP.
  Record explicitly which SOP the new experiment adopts before running it; do
  not silently reuse the old all-checkpoint override. If styled policies are
  selected in AugSim, disclose that selection domain and keep final tests separate.
- Reserve new, untouched test seeds/physical placements after selection. Use
  paired placements, trial counts and stated uncertainty. Repeated real trials
  are needed to estimate placement/reset variability.
- Historical strict evaluation uses 2,700 policy steps, no timeout opening, and
  geometric `fully_seated`. It does **not** require gripper release. If the task
  requires release and stable seating, define a separately versioned criterion
  (including a justified dwell interval) in sim and real, and re-evaluate.
- Keep actions, controller profile, physics, task, camera mapping and selection
  budget matched across raw/styled branches. Include stage metrics and actual
  consumed observations so failures can be separated from visual artifacts.

## 14. Handoff outputs and immediate next actions

Store each experimental session in its own directory with:

```text
baseline_manifest.json
effective_contract.json
source_changes.patch
offline_checks.md
tracking_trials/          # command/state/timing data and per-trial summaries
gripper_trials/           # event traces and measured response summaries
policy_trials/           # consumed observations, actions, manifests, grading
sim_parity_report.md
pilot_replay_report.md
recollection_decision.md
```

These are proposed outputs to implement, not files already provided by this
branch. Record units and schema versions in every machine-readable stream.
Transfer compact summaries/source changes back to the Delta agent; move large
recordings only through an explicitly chosen artifact-transfer workflow.

**The deployment agent should start now with Phases A–C:** inventory the actual
workstation/NUC paths, resolve real-data provenance, prepare the explicit
measured-width/chunk/task launcher, integrate logging without losing WB fixes,
and run offline checks. Then present the operator with the concrete free-space
and gripper trial set for the physical session. The simulation agent can prepare
the new profile/tests concurrently with that work, but must use the resulting
physical metadata/traces before claiming parity or starting full recollection.
