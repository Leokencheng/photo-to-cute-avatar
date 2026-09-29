#!/usr/bin/env python3
"""左右拼接：把成品图标和它的原图（或任意两张图）并排放成一张对照图。

用法：
    python3 side_by_side.py <左图> <右图> <输出> [--gap 12] [--h N] [--bg auto]

    --h   统一高度。**不写 = 自动取两者中较高的那个高度**（不是 1024！）。
          源图是 1718 高的人像时，自动值会把 1024 的图标放大 1.68 倍、画布变成
          3650×1718；想要图标保持原生像素就显式写 --h 1024，让照片缩下来。

为什么要写进技能：
    "把成品和原图拼在一起看看"是每次交付后最常被追加的一步，
    手写一段一次性 PIL 代码既啰嗦又容易在细节上不一致
    （中缝颜色、EXIF 方向、缩放滤镜）。这里固化成固定口径。

固定口径（重要，保证每次输出一致）：
    1. 两张图先做 EXIF 方向校正，避免 Photos 库/手机竖拍图横竖颠倒；
    2. 【等高不裁切】：以统一高度为基准，另一张按比例用 LANCZOS 缩放。
       不裁切、不变形；
    3. 中缝留 gap 像素深色分隔条，颜色默认取左图角落色
       （成品图标角落即背景色，于是中缝与图标背景连成一体）；
    4. 不做任何调色、不做白边、不加文字水印。
"""
import os
import subprocess
import sys

try:
    from PIL import Image, ImageOps
except ImportError:
    for _cand in ("/Users/leo/.workbuddy/binaries/python/envs/default/bin/python",
                  "/usr/bin/python3"):
        if os.path.exists(_cand) and subprocess.run(
                [_cand, "-c", "import PIL"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            os.execv(_cand, [_cand] + sys.argv)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "Pillow"], check=False)
    from PIL import Image, ImageOps  # noqa: E402


def load(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return im


def corner_bg(im):
    """取角落色作为中缝颜色。四角一致才算"纯色背景"，否则退回深炭灰。"""
    w, h = im.size
    cs = [im.getpixel((2, 2)), im.getpixel((w - 3, 2)),
          im.getpixel((2, h - 3)), im.getpixel((w - 3, h - 3))]
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in cs]
    if max(lum) - min(lum) <= 6 and sum(lum) / 4 < 110:
        return cs[0]
    return (24, 25, 26)


def main():
    args = [a for a in sys.argv[1:]]
    gap, height, bgspec, pos = 12, None, "auto", []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--gap":
            gap = int(args[i + 1]); i += 2
        elif a == "--h":
            height = int(args[i + 1]); i += 2
        elif a == "--bg":
            bgspec = args[i + 1]; i += 2
        else:
            pos.append(a); i += 1
    if len(pos) != 3:
        print(__doc__)
        sys.exit(2)
    left_p, right_p, out_p = pos

    left, right = load(left_p), load(right_p)
    H = height or max(left.height, right.height)

    def fit(im):
        return im.resize((max(1, round(im.width * H / im.height)), H), Image.LANCZOS)

    l, r = fit(left), fit(right)

    if bgspec == "auto":
        bg = corner_bg(left)
    else:
        bg = tuple(int(v) for v in bgspec.split(","))

    canvas = Image.new("RGB", (l.width + gap + r.width, H), bg)
    canvas.paste(l, (0, 0))
    canvas.paste(r, (l.width + gap, 0))

    d = os.path.dirname(os.path.abspath(out_p))
    if d:
        os.makedirs(d, exist_ok=True)
    canvas.save(out_p)
    print(f"saved {out_p} {canvas.width}x{canvas.height} | "
          f"左 {left.width}x{left.height}→{l.width}x{l.height} | "
          f"右 {right.width}x{right.height}→{r.width}x{r.height} | "
          f"中缝 {gap}px {bg}")


if __name__ == "__main__":
    main()
