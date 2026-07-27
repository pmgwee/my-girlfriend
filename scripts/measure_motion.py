import numpy as np
from PIL import Image
import glob

src = r'C:/Users/quekm/Downloads/Telegram Desktop/IMG_3556.mp4'
fs = sorted(glob.glob(r'C:/Users/quekm/AppData/Local/Temp/vgf/f_*.png'))
imgs = [np.asarray(Image.open(f).convert('RGB'), dtype=np.float32) for f in fs]
H, W, _ = imgs[0].shape
print("frame WxH:", W, H, "n samples:", len(imgs))

cw, ch = 660, 1700
x0 = (W - cw) // 2
y0 = (H - ch) // 2

def mad(a, b):
    return float(np.abs(a - b).mean())

full = [mad(imgs[i], imgs[i + 1]) for i in range(len(imgs) - 1)]
crop = [mad(imgs[i][y0:y0 + ch, x0:x0 + cw], imgs[i + 1][y0:y0 + ch, x0:x0 + cw]) for i in range(len(imgs) - 1)]

def lum(a):
    return a.mean(axis=2)

lcrop = [mad(lum(imgs[i][y0:y0 + ch, x0:x0 + cw]), lum(imgs[i + 1][y0:y0 + ch, x0:x0 + cw])) for i in range(len(imgs) - 1)]

print("\nidx->idx:  crop   full   lumcrop   (t = idx*0.2s)")
for i in range(len(crop)):
    t = i * 0.2
    print(f"{i:2d}->{i+1:2d}  t={t:4.1f}s  {crop[i]:6.2f} {full[i]:6.2f} {lcrop[i]:6.2f}")

# rolling stats
arr = np.array(crop)
print("\ncrop MAD summary: min %.3f max %.3f mean %.3f" % (arr.min(), arr.max(), arr.mean()))
print("samples <=0.10:", int((arr <= 0.10).sum()))
print("samples 0.10-0.70:", int(((arr > 0.10) & (arr <= 0.70)).sum()))
print("samples >5:", int((arr > 5).sum()))
