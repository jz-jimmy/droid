"""Offline analysis; requires only numpy and never connects to a robot."""
from pathlib import Path
import importlib.util
import numpy as np

root = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('deployed_fk', root/'wmrl_bridge/franka_fk.py')
fk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fk)
for path in sorted((root/'evidence/rollouts').glob('*/rollout.npz')):
    with np.load(path, allow_pickle=False) as z:
        a, q, t = z['action'], z['q'], z['t']
        tail = t >= t[-1] - 10
        delta = np.array([fk.tcp_position(x[:7]) - fk.tcp_position(y)
                          for x,y in zip(a[tail], q[tail])]) * 1000
        print(path.parent.name)
        print(f'  steps={len(a)}; command width={a[:,7].min()*1000:.3f}..{a[:,7].max()*1000:.3f} mm; close requests={int((a[:,7]<.045).sum())}')
        print(f'  measured width={z["width"].min()*1000:.3f}..{z["width"].max()*1000:.3f} mm')
        print(f'  last 10s mean target-minus-measured TCP mm={delta.mean(0).round(3).tolist()}')
