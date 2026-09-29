#!/usr/bin/env python3
"""胶囊图标自检：量测两只胶囊眼的主轴长度/宽度/倾角，以及深色色块与背景占比。

用法：
    python3 check_icon.py <图片路径>

眼睛用"脸内部空洞"法检出（见 eyefind.py），不依赖暗度阈值——模型常把胶囊眼
画成与背景几乎同色的近黑色。长宽用「垂直于两眼中心连线」的正交框架量测，
因此旋转后依然准确（旋转会让外接盒失真，不能用外接盒算长宽比）。

判读标准（见 SKILL.md）：
    长宽比 2.5~3.0 | 倾角 15~20° 且左眼更高 | 两胶囊等大偏差 <15%
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eyefind import find_eyes, measure, pair_frame  # noqa: E402
from eyefind import Image  # noqa: E402

BG_TOL = 14
DARK_LUM = 90        # 深色（头发/眼睛）亮度上限，用于覆盖率统计


def main():
    path = sys.argv[1]
    im = Image.open(path).convert("RGB")
    W, H = im.size
    px = im.load()
    bg = px[W - 12, 12]
    eyes, px, bg = find_eyes(im)

    print(f"文件 {path}  {W}x{H}  背景色 {bg}")
    if len(eyes) < 2:
        print(f"✗ 只检出 {len(eyes)} 只胶囊眼（脸内部空洞法）——请肉眼确认眼睛是否存在/是否被遮挡")
        return

    pair, d, p, ang = pair_frame(eyes)
    for i, e in enumerate(pair, 1):
        e["len"], e["wid"], e["cu"], e["cv"] = measure(e, d, p)
        e["ar"] = e["len"] / e["wid"]
        print(f"  眼{i} 中心=({e['cx']:.0f},{e['cy']:.0f}) 长x宽={e['len']:.0f}x{e['wid']:.0f} "
              f"长宽比={e['ar']:.2f} 面积={e['n']}")

    a, b = pair
    ar = (a["ar"] + b["ar"]) / 2
    gap = abs(a["n"] - b["n"]) / max(a["n"], b["n"])
    print(f"\n两眼中心连线 dx={b['cx'] - a['cx']:.0f} dy={b['cy'] - a['cy']:.0f} → "
          f"顺时针倾角 {ang:.1f}° ({'左眼更高 ✓' if b['cy'] > a['cy'] else '右眼更高 ✗ 方向反了'})")
    print(f"平均长宽比 {ar:.2f} {'✓ 达标' if 2.5 <= ar <= 3.0 else '✗ 应为 2.5~3.0'}")
    print(f"两眼等大性偏差 {gap:.0%} {'✓' if gap < 0.15 else '✗ 两眼不等大'}")
    print(f"倾角 {'✓ 达标' if 15 <= ang <= 20 else '✗ 应为 15~20°'}")
    print("眼长轴按定义垂直于中心连线 ✓（本框架即以此为准）")

    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    step = 4
    tot = dark = skin = bgc = 0
    for y in range(0, H, step):
        for x in range(0, W, step):
            c = px[x, y]
            tot += 1
            if all(abs(q - r) <= BG_TOL for q, r in zip(c, bg)):
                bgc += 1
            elif lum(c) < DARK_LUM:
                dark += 1
            elif c[0] > 150 and c[0] - c[2] > 22:
                skin += 1
    print(f"\n覆盖：深色(头发+眼睛) {dark / tot:.0%} | 肤色 {skin / tot:.0%} | 背景 {bgc / tot:.0%}")


if __name__ == "__main__":
    main()
