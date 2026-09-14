#!/bin/bash
# WMRL bridge launcher (run after launch_robot.sh + launch_gripper.sh are up)
source ~/miniconda3/etc/profile.d/conda.sh
conda activate polymetis-local
exec python ~/wmrl_bridge/franka_bridge_polymetis.py "$@"
