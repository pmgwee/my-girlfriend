import numpy as np, subprocess, glob, os
from PIL import Image

FF = r'C:/ffmpeg/ffmpeg-2026-05-18-git-b4d11dffbf-full_build/bin/ffmpeg.exe'

def clip_mad(path, fps=5):
    d = r'C:/Users/quekm/AppData/Local/Temp/probe_' + os.path.basename(path).replace('.','')
    os.makedirs(d, exist_ok=True)
    for f in glob.glob(d + r'\*.png'): os.remove(f)
    subprocess.run([FF,'-nostdin','-v','error','-y','-i',path,'-vf',f'fps={fps}','-vcodec','png',d+r'/f_%03d.png'], check=True)
    fs = sorted(glob.glob(d+r'/f_*.png'))
    imgs = [np.asarray(Image.open(f).convert('RGB', dtype=np.float32) if False else Image.open(f).convert('RGB'), np.float32) for f in fs]
    diffs = [float(np.abs(imgs[i]-imgs[i+1]).mean()) for i in range(len(imgs)-1)]
    a = np.array(diffs)
    return len(imgs), float(a.min()), float(a.max()), float(a.mean())

for p in [r'./web/public/avatar/idle.mp4', r'./web/public/avatar/talking.mp4']:
    n,mn,mx,me = clip_mad(p)
    print(f"{p}: samples={n} min={mn:.2f} max={mx:.2f} mean={me:.2f}")
