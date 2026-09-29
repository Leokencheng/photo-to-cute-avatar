#!/usr/bin/env python3
"""补腮红：在两侧脸颊画出「小而浅的低饱和椭圆腮红」。

为什么需要它：
    规范把腮红列为必需元素，但模型极不稳定——实测两版生成图里脸颊都是纯平肤色，
    甚至同一个提示词下一版有一版没有。跟倾斜/眼型一样，这类"几何元素缺失"
    靠改提示词收敛不了，用确定性脚本补最省事。

做法：
    1. 用 eyefind 找到胶囊眼，建立正交框架（d = 两眼连线方向，p = 长轴方向）
    2. 腮红中心 = 眼中心 + 向外 0.62×眼距 + 沿 p 向下 0.55×眼距
    3. 椭圆半轴：沿 d 方向 0.30×眼距，沿 p 方向 0.22×眼距
    4. 只落在肤色像素上（不越到头发/眼睛里），中心距眼中心 < 0.8×眼长 的像素跳过
    5. 颜色由**局部肤色**派生（压绿压蓝提红），保证与原图肤色协调、低饱和
    6. 用 smoothstep 做软边，避免生硬轮廓

用法：
    python3 add_blush.py <输入图> <输出图> [--alpha 0.30] [--scale 1.0]
"""
import argparse
import math
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eyefind import find_eyes, measure, pair_frame  # noqa: E402
from eyefind import Image  # noqa: E402


def is_skin(c):
    return c[0] > 140 and c[0] - c[2] > 18 and c[0] - c[1] > 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--alpha", type=float, default=0.30,
                    help="腮红最大不透明度（0.2~0.4，越大小越明显）")
    ap.add_argument("--scale", type=float, default=1.0, help="腮红尺寸倍率")
    a = ap.parse_args()

    im = Image.open(a.src).convert("RGB")
    W, H = im.size
    px = im.load()
    eyes, px, bg = find_eyes(im)
    if len(eyes) < 2:
        im.save(a.dst)
        print(f"saved {a.dst} | 直通（只检出 {len(eyes)} 只胶囊眼，无法定位脸颊）")
        return

    pair, d, p, _ang = pair_frame(eyes)
    for e in pair:
        e["len"], e["wid"], e["cu"], e["cv"] = measure(e, d, p)
    gap = math.hypot(pair[1]["cx"] - pair[0]["cx"], pair[1]["cy"] - pair[0]["cy"])
    eye_len = sum(e["len"] for e in pair) / 2

    rx = gap * 0.30 * a.scale          # 沿 d 方向半轴
    ry = gap * 0.22 * a.scale          # 沿 p 方向半轴

    # 全局肤色的 (r−g) 中位数，作为"这处是否已经偏红"的基准
    rg = sorted(px[x, y][0] - px[x, y][1]
                for y in range(0, H, 4) for x in range(0, W, 4) if is_skin(px[x, y]))
    base_rg = rg[len(rg) // 2] if len(rg) >= 50 else 40

    def rosy_stats(cx, cy, rad):
        """目标处的 (r−g) 均值与标准差。

        实测：模型已经画了腮红的地方 r−g 标准差 9.4（有个色块）；纯平肤色只有 0.3~1.2。
        所以"标准差大"或"整体偏红"都能判出已有腮红。
        """
        vals = []
        for yy in range(max(0, int(cy - rad)), min(H, int(cy + rad) + 1)):
            for xx in range(max(0, int(cx - rad)), min(W, int(cx + rad) + 1)):
                c = px[xx, yy]
                if is_skin(c) and (xx - cx) ** 2 + (yy - cy) ** 2 <= rad * rad:
                    vals.append(c[0] - c[1])
        if len(vals) < 50:
            return None
        m = sum(vals) / len(vals)
        var = sum((v - m) ** 2 for v in vals) / len(vals)
        return m, var ** 0.5

    def is_rosy(cx, cy, rad):
        st = rosy_stats(cx, cy, rad)
        if st is None:
            return False
        m, sd = st
        return sd > 3.0 or m > base_rg + 4

    def skin_run_from(x, y, sign, limit):
        """从 (x,y) 沿 sign 方向一直走，直到不再是肤色，返回走过的距离。"""
        n = 0
        while 0 <= x + sign * (n + 1) < W and n < limit and is_skin(px[x + sign * (n + 1), y]):
            n += 1
        return n

    # 先算两颊的目标位置
    spots = []
    for idx, e in enumerate(pair):
        sign = -1.0 if idx == 0 else 1.0          # 向外 = 背离另一只眼
        # 按【脸部实际宽度】定位，而不是固定的"向外多少倍眼距"——
        # 实测固定偏移在侧脸/窄脸上会直接落到头发里（左脸整块被跳过），
        # 而"逐档向内回退"又会把腮红退到眼睛正下方，不像脸颊。
        # 做法：在眼睛下方那一行沿肤色跑一段，腮红中心放在这段的 55% 处。
        cy = min(max(e["cy"] + p[1] * gap * 0.50, 2), H - 3)
        cx = e["cx"] + p[0] * gap * 0.50
        r = skin_run_from(int(cx), int(cy), 1 if sign > 0 else -1, int(gap * 1.2))
        erx = min(gap * 0.26, r * 0.52)           # 半轴随脸宽自适应
        spots.append((idx, e, cx + sign * r * 0.55, cy, erx, erx * 0.72,
                      1 if sign > 0 else -1, r))

    # 腮红是左右对称的：任一侧已有 → 模型已经画过腮红了，整张直通，别再叠一层
    for idx, e, cu, cv, erx, ery, sign, r in spots:
        if erx > 0 and is_rosy(cu, cv, min(erx, ery) * 0.9):
            im.save(a.dst)
            print(f"saved {a.dst} | 直通（眼{idx + 1} 一带已明显偏红/有色块，"
                  f"判定模型已画腮红；基准 r−g {base_rg:.0f}）")
            return

    out = im.copy()
    op = out.load()
    painted = 0
    for idx, e, cu, cv, erx, ery, sign, r in spots:
        if r < gap * 0.15 or erx < 4:
            print(f"  眼{idx + 1} 下方找不到足够宽的肤色带，跳过")
            continue
        x0, x1 = int(cu - erx - 2), int(cu + erx + 2)
        y0, y1 = int(cv - ery - 2), int(cv + ery + 2)
        for y in range(max(0, y0), min(H, y1 + 1)):
            for x in range(max(0, x0), min(W, x1 + 1)):
                c = px[x, y]
                if not is_skin(c):
                    continue
                if math.hypot(x - e["cx"], y - e["cy"]) < 0.8 * eye_len:
                    continue                       # 别糊到眼睛上
                u = ((x - cu) * p[1] - (y - cv) * p[0]) / erx   # d 方向归一坐标
                v = ((x - cu) * p[0] + (y - cv) * p[1]) / ery   # p 方向归一坐标
                r2 = u * u + v * v
                if r2 >= 1.0:
                    continue
                t = 1.0 - r2
                w = t * t * (3 - 2 * t)             # smoothstep 软边
                al = a.alpha * w
                rose = (min(255, c[0] + 6), max(0, c[1] - 54), max(0, c[2] - 60))
                op[x, y] = (round(c[0] * (1 - al) + rose[0] * al),
                            round(c[1] * (1 - al) + rose[1] * al),
                            round(c[2] * (1 - al) + rose[2] * al))
                painted += 1
    out.save(a.dst)
    print(f"saved {a.dst} | 眼距 {gap:.0f} 眼长 {eye_len:.0f} | "
          f"参考半轴 {rx:.0f}x{ry:.0f} | 上色 {painted}px")


if __name__ == "__main__":
    main()
