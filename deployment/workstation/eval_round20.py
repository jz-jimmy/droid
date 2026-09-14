"""Apply archived round20 cameras, then launch the interactive real evaluation."""
import os
import runpy
from pathlib import Path
os.environ['MUJOCO_GL']='osmesa'
import torch
if not torch.cuda.is_available():
    raise SystemExit('CUDA unavailable. Resolve the NVIDIA driver mismatch before starting this session.')
from wmrl.lerobot import features
from industreal_mujoco import scene_build
old=runpy.run_path(str(Path(__file__).with_name('features_round20.py')))
for name,scene_name in [('LEFT_STATIC_CAMERA_CFG','LEFT_D455_CAMERA'),('RIGHT_STATIC_CAMERA_CFG','RIGHT_D455_CAMERA')]:
    cfg=old[name]
    getattr(features,name).update(cfg)
    getattr(scene_build,scene_name).update(pos=cfg['pos_world'],quat=cfg['quat_wxyz_world'],fovy=cfg['fovy'])
features.WRIST_CAMERA_CFG.update(old['WRIST_CAMERA_CFG'])
# Warm CUDA kernels before the first action, then clear the synthetic action queue.
import numpy as np
from wmrl.real import real_rollout, constants
_original_load = real_rollout.load_policy
def warmed_load(*args, **kwargs):
    policy, pre, post = _original_load(*args, **kwargs)
    frames = {v: np.zeros((224, 224, 3), dtype=np.uint8)
              for v in real_rollout.policy_camera_map(policy.config).values()}
    state = np.array(constants.START_ARM_QPOS + [constants.START_GRIPPER_WIDTH])
    obs = pre(real_rollout.build_observation(frames, state, features.TASK_DESCRIPTION))
    with torch.inference_mode():
        for _ in range(3):
            policy.reset()
            action = post(policy.select_action(obs))
            assert torch.isfinite(action).all()
    torch.cuda.synchronize()
    policy.reset()
    print('[preflight] CUDA policy warmup complete; action queue cleared', flush=True)
    return policy, pre, post
real_rollout.load_policy = warmed_load
from wmrl.real.eval_session import main
main()
