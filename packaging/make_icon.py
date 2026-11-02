"""Draw the app icon (packaging/icon.ico): a blue tile with a rising chart line."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=(47, 111, 237))
pts = [(190, 700), (380, 520), (520, 610), (820, 290)]
d.line(pts, fill="white", width=90, joint="curve")
for x, y in pts:
    d.ellipse([x - 45, y - 45, x + 45, y + 45], fill="white")
d.polygon([(860, 230), (700, 260), (830, 390)], fill="white")  # arrow head
out = Path(__file__).with_name("icon.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256)).save(out.with_suffix(".png"))
print("wrote", out)
