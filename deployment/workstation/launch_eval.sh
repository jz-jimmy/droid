#!/usr/bin/env bash
set -euo pipefail
cd /home/jiayu/Sim2Real
export PYTHONPATH=/home/jiayu/Sim2Real/wmrl:/home/jiayu/Sim2Real/industreal-mujoco/src
export MUJOCO_GL=osmesa
export OMP_NUM_THREADS=4
export HF_HUB_OFFLINE=1
exec 9>/home/jiayu/Sim2Real/wmrl/runs/mujoco_gear/preflight_20260912/eval.lock
flock -n 9 || { echo "An evaluation is already running. Close its overlay with q first." >&2; exit 1; }
exec /home/jiayu/miniforge3/envs/industreal-mujoco-sdf/bin/python -u \
 wmrl/runs/mujoco_gear/preflight_20260912/round20_overlay/eval_round20.py \
 --ckpt wmrl/external/1024sim_demo_185600ckpt-20260912T040108Z-1-001/1024sim_demo_185600ckpt \
 --ckpt-tag 1024sim_185600_round20 \
 --seed 1000000 --seed-end 1000015 \
 --nominals wmrl/runs/mujoco_gear/preflight_20260912/seed_nominals \
 --seconds 90 --device cuda:0 --no-te --video --record-return \
 --file wmrl/eval-logs/real_eval_20260912_1024sim_185600_round20.md \
 --out wmrl/runs/mujoco_gear/real_rollouts "$@"
