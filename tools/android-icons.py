"""
Generate the Android launcher assets from the 1024x1024 Zugzwang icon.

Three things come out of one source:
  ic_launcher.png          the legacy square icon, full artwork
  ic_launcher_round.png    the same, circle-masked, for launchers that ask
  ic_launcher_foreground.png   the adaptive icon's foreground layer

The adaptive layer is the fiddly one: Android masks it to an arbitrary shape
and only the centre ~66% is guaranteed to survive. So the artwork's own border
is cropped off, the remaining content is measured, and that is scaled into the
safe zone on a transparent canvas - the background colour comes from the
<color> resource instead, so the two layers meet seamlessly whatever shape the
launcher cuts.
"""
from PIL import Image, ImageDraw

SRC = '/mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend/android/icon-source.png'
RES = '/mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend/android/app/src/main/res'

LEGACY = {'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}
FOREGROUND = {'mdpi': 108, 'hdpi': 162, 'xhdpi': 216, 'xxhdpi': 324, 'xxxhdpi': 432}
# Fraction of the 108dp foreground canvas the artwork may fill. Not the 66%
# "safe zone" square: a circular mask inscribes that square, so artwork that
# fills it corner to corner gets its corners cut - which is exactly what the
# first attempt did to the Z. This is sized so the content's DIAGONAL fits
# inside the 72dp circle, which is the constraint that actually binds.
SAFE = 0.48

src = Image.open(SRC).convert('RGBA')
W, H = src.size
bg = src.getpixel((4, 4))[:3]
print('background', '#%02X%02X%02X' % bg)

# Inside the drawn border, so the frame does not ride the mask edge.
inset = int(W * 0.07)
inner = src.crop((inset, inset, W - inset, H - inset))

# Where the artwork actually is, rather than where it might be.
diff = Image.new('L', inner.size)
px, dp = inner.load(), diff.load()
for y in range(inner.size[1]):
    for x in range(inner.size[0]):
        r, g, b, a = px[x, y]
        dp[x, y] = 255 if a > 8 and (abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])) > 40 else 0
box = diff.getbbox()
content = inner.crop(box)
print('content bbox', box, '->', content.size)

# The foreground layer: content scaled into the safe zone, transparent around it.
canvas = 1024
target = int(canvas * SAFE)
scale = target / max(content.size)
fitted = content.resize((max(1, round(content.size[0] * scale)),
                         max(1, round(content.size[1] * scale))), Image.LANCZOS)
foreground = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
foreground.paste(fitted, ((canvas - fitted.size[0]) // 2, (canvas - fitted.size[1]) // 2), fitted)

# The round legacy icon: the full artwork, circle-masked.
mask = Image.new('L', (W, H), 0)
ImageDraw.Draw(mask).ellipse((0, 0, W - 1, H - 1), fill=255)
round_icon = src.copy()
round_icon.putalpha(mask)

written = []
for density, size in LEGACY.items():
    for name, img in (('ic_launcher', src), ('ic_launcher_round', round_icon)):
        path = f'{RES}/mipmap-{density}/{name}.png'
        img.resize((size, size), Image.LANCZOS).save(path)
        written.append(path)
for density, size in FOREGROUND.items():
    path = f'{RES}/mipmap-{density}/ic_launcher_foreground.png'
    foreground.resize((size, size), Image.LANCZOS).save(path)
    written.append(path)

with open(f'{RES}/values/ic_launcher_background.xml', 'w') as f:
    f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
            f'    <color name="ic_launcher_background">#{bg[0]:02X}{bg[1]:02X}{bg[2]:02X}</color>\n'
            '</resources>')
written.append(f'{RES}/values/ic_launcher_background.xml')

# Keep the source in the repo, so this is reproducible without a Downloads folder.
keep = '/mnt/c/Users/David/Documents/chess-app-v3.9/chess-frontend/android/icon-source.png'
src.save(keep)
written.append(keep)
print(f'\n{len(written)} files written')
for p in written:
    print(' ', p.split('chess-app-v3.9/')[-1])
