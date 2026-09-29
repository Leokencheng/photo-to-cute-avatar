#!/usr/bin/env python3
"""构图自检（身体版）：量测「头部以下身体是否还在画面里」。

用法：
    python3 check_body.py <图片>

为什么需要它：
    原 check_icon.py 只管眼睛。而这个风格最容易出的翻车是——模型明明画了
    头 + 肩/上半身，后处理（包围盒缩放 + 居中 + 底部对齐）却把身体整块削掉，
    最后只剩一颗"悬浮的头"。这个脚本就是专门盯这件事的。

量测口径（基准图 icon_girl.png）：
    底5%  ≈43%   底10% ≈43%   底15% ≈52%
    四边留白  上≈86  左≈0  右≈149  下≈1
    底边肤色像素 ≈0（底部应该是衣服/肩膀，不是脸贴底）

判读：
    底5% / 底10% 任一 < 25%  → 身体基本被截没了，必须重做
    左留白 > 120             → 主体偏右，失去"从左下窥视"的裁切感
"""
import os
import subprocess
import sys
from collections import Counter

try:
    from PIL import Image
except ImportError:
    for _cand in ("/Users/leo/.workbuddy/binaries/python/envs/default/bin/python",
                  "/usr/bin/python3"):
        if os.path.exists(_cand) and subprocess.run(
                [_cand, "-c", "import PIL"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            os.execv(_cand, [_cand] + sys.argv)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "Pillow"], check=False)
    from PIL import Image  # noqa: E402


def detect_bg(im):
    w, h = im.size
    px = im.load()
    pts = [px[x, 3] for x in (3, w // 4, w // 2, 3 * w // 4, w - 4)]
    pts += [px[3, h // 3], px[w - 4, h // 3], px[3, 2 * h // 3], px[w - 4, 2 * h // 3]]
    return Counter(pts).most_common(1)[0][0]


def main():
    path = sys.argv[1]
    im = Image.open(path).convert("RGB")
    W, H = im.size
    px = im.load()
    bg = detect_bg(im)
    tol = 14
    isbg = lambda c: all(abs(q - r) <= tol for q, r in zip(c, bg))

    print(f"文件 {path}  {W}x{H}  背景色 {bg}")

    # 1) 底部三条带的内容覆盖率
    bands = {}
    for frac in (0.05, 0.10, 0.15):
        y0 = int(H * (1 - frac))
        tot = n = 0
        for y in range(y0, H, 2):
            for x in range(0, W, 2):
                tot += 1
                if not isbg(px[x, y]):
                    n += 1
        bands[frac] = n / tot * 100
    print("底部内容覆盖：" + " | ".join(f"底{f:.0%} {v:.0f}%" for f, v in bands.items()))

    # 2) 主体包围盒四边留白（一行至少 8 个非背景像素才算有内容）
    minx, miny, maxx, maxy = W, H, -1, -1
    for y in range(H):
        row = [x for x in range(W) if not isbg(px[x, y])]
        if len(row) >= 8:
            miny = min(miny, y)
            maxy = y
            minx = min(minx, row[0])
            maxx = max(maxx, row[-1])
    print(f"主体包围盒 {(maxx - minx + 1)}x{(maxy - miny + 1)} | "
          f"留白 上{miny} 下{H - 1 - maxy} 左{minx} 右{W - 1 - maxx}"
          f"  （基准 主体≈876x939 / 上≈85 下0 左0 右148）")

    # 3) 最底一行里的肤色像素数（整条都是肤色 = 脸贴底，不是身体构图）
    skin = 0
    for x in range(W):
        c = px[x, H - 2]
        if c[0] > 150 and c[0] - c[2] > 22:
            skin += 1
    print(f"底行肤色像素 {skin}px（露肩/吊带人像本身就会有一两百 px 属于正常；"
          f"接近整行宽度才是脸贴底）")

    warn = []
    if min(bands[0.05], bands[0.10]) < 25:
        warn.append("身体被截没 → 用 --keepbody 重做构图")
    if minx > 120:
        warn.append("主体偏右 → 加大 --bleedx")
    if miny > 200:
        warn.append("顶部留白过多 → 调大 --hfrac")
    if maxy < H - 20:
        warn.append("主体悬空未触底 → 调大 --bleedy")
    if skin > 0.5 * W:
        warn.append("底边整条是肤色 → 生成图缺身体")
    print("判定：" + ("✓ 身体构图达标" if not warn else "⚠️ " + "；".join(warn)))


if __name__ == "__main__":
    main()
