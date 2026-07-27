import numpy as np
from PIL import Image
import subprocess, glob, os

src = r'C:/Users/quekm/Downloads/Telegram Desktop/IMG_3556.mp4'
out = r'C:/Users/quekm/AppData/Local/Temp/vgf_hi'
os.makedirs(out, exist_ok=True)
for f in glob.glob(out + r'\h_*.png'):
    os.remove(f)

# Extract source frames t=2.8s .. 4.2s at full 60fps (covers the onset)
subprocess.run([
    r'C:/ffmpeg/ffmpeg-2026-05-18-git-b4d11dffbf-full_build/bin/ffmpeg.exe',
    '-nostdin', '-v', 'error', '-y',
    '-ss', '2.8', '-to', '4.2', '-i', src,
    '-vf', 'fps=60', '-vcodec', 'png',
    out + r'/h_%03d.png'
], check=True)

fs = sorted(glob.glob(out + r'/h_*.png'))
imgs = [np.asarray(Image.open(f).convert('RGB'), dtype=np.float32) for f in fs]
H, W, _ = imgs[0].shape
cw, ch = 660, 1700
x0 = (W - cw) // 2
y0 = (H - ch) // 2

def mad(a, b):
    return float(np.abs(a - b).mean())

print("hi-res samples:", len(imgs), "(each step = 1/60 s = 16.7 ms)")
print("\nsrcFrame  t(s)   cropMAD")
for i in range(len(imgs) - 1):
    t = 2.8 + i / 60.0
    c = mad(imgs[i][y0:y0 + ch, x0:x0 + cw], imgs[i + 1][y0:y0 + ch, x0:x0 + cw])
    flag = '  <-- onset' if (0.05 < c < 0.5 and i > 0) else ''
    print(f"{i:3d}   {t:5.3f}   {c:6.3f}{flag}")
