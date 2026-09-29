#!/usr/bin/env python3
"""胶囊眼瘦身：把生成图里偏胖的胶囊眼沿宽度方向收窄到规范长宽比。

用法：
    python3 slim_eyes.py <输入图> <输出图> [--ratio 2.75]

背景：图像模型很难稳定画到"长度 = 宽度 2.5~3 倍"的细长胶囊（实测常见 1.75~2.2）。
本脚本用 eyefind.py 定位两只胶囊眼，并按"垂直于两眼中心连线"的正交框架量出真实
长与宽，再把两侧多余的像素替换为周边底色，把长宽比**精确**推到目标值，同时保持：
  - 胶囊中心、长度、圆头位置不变
  - 纯色无缝填充（不含任何内部细节）
  - 靠相邻色块差异区分形状，不引入描边

⚠️ 先跑 check_icon.py：长宽比已在 2.5~3.0 内就不要跑本脚本。
"""
import argparse
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eyefind import find_eyes, measure, pair_frame  # noqa: E402
from eyefind import Image  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--ratio", type=float, default=2.75, help="目标长宽比（规范 2.5~3.0）")
    a = ap.parse_args()

    im = Image.open(a.src).convert("RGB")
    W, H = im.size
    eyes, px, bg = find_eyes(im)
    if len(eyes) < 2:
        raise SystemExit(f"只检出 {len(eyes)} 只胶囊眼（脸内部空洞法），未做处理")

    eyes, d, p, _ = pair_frame(eyes)
    bg_lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]

    def xy(pu, pv):
        return pu * p[0] + pv * d[0], pu * p[1] + pv * d[1]

    mask = set()
    for e in eyes:
        mask.update(e["pts"])

    out = im.copy()
    op = out.load()
    report = []
    for idx, e in enumerate(eyes, 1):
        L, Wd, cu, cv = measure(e, d, p)
        tw = L / a.ratio
        if Wd <= tw * 1.02 or L < tw * 2.2:
            report.append(f"眼{idx}: {L:.0f}x{Wd:.0f} 长宽比 {L / Wd:.2f} → 无需处理")
            continue
        rt = tw / 2.0
        half = L / 2.0

        # 周边代表底色（兜底用）：亮度明显高于胶囊的邻近像素
        ring = Counter()
        for q in e["pts"][::5]:
            qu, qv = q[0] * p[0] + q[1] * p[1], q[0] * d[0] + q[1] * d[1]
            for off in (Wd, Wd * 1.5):
                for s in (1, -1):
                    qx, qy = xy(qu, qv + s * off)
                    qx, qy = int(qx), int(qy)
                    if 0 <= qx < W and 0 <= qy < H and (qx, qy) not in mask:
                        c = px[qx, qy]
                        if 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] > bg_lum + 8:
                            ring[c] += 1
        fallback = ring.most_common(1)[0][0] if ring else bg

        def outside(u_pu, sign):
            """从 (u_pu, cv) 沿 ±d 向外取"干净"的外侧底色。

            先越过深色胶囊本体，再跳过抗锯齿过渡带（约 5px）取真正的平坦底色，
            否则填充色会是灰蒙蒙的过渡色、在胶囊边上留下亮暗条纹。
            """
            for step in range(1, int(Wd) + 24):
                qx, qy = xy(u_pu, cv + sign * step)
                qx, qy = int(round(qx)), int(round(qy))
                if not (0 <= qx < W and 0 <= qy < H):
                    return None
                if (qx, qy) in mask:
                    continue
                qx2, qy2 = xy(u_pu, cv + sign * (step + 5))
                qx2, qy2 = int(round(qx2)), int(round(qy2))
                if not (0 <= qx2 < W and 0 <= qy2 < H):
                    return None
                c = px[qx2, qy2]
                if 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] > bg_lum + 8:
                    return c
                return None
            return None

        # 处理集合 = 胶囊像素 + 周围 2px 抗锯齿灰边（灰边不清掉会留条纹）
        proc = set(e["pts"])
        frontier = set(e["pts"])
        for _ in range(2):
            nxt = set()
            for (fx, fy) in frontier:
                for ddx in (-1, 0, 1):
                    for ddy in (-1, 0, 1):
                        q = (fx + ddx, fy + ddy)
                        if 0 <= q[0] < W and 0 <= q[1] < H and q not in proc:
                            proc.add(q)
                            nxt.add(q)
            frontier = nxt

        ro = Wd / 2.0
        for (x, y) in proc:
            q_pu = x * p[0] + y * p[1]
            q_pv = x * d[0] + y * d[1]
            du = q_pu - cu
            dv = q_pv - cv
            if abs(dv) > ro + 2:
                continue                      # 只处理胶囊附近的像素，不动眼镜/皮肤
            cap = math.copysign(half - rt, du) if du else 0.0
            keep = abs(dv) <= rt and (abs(du) <= half - rt
                                      or math.hypot(du - cap, dv) <= rt)
            if keep:
                continue
            cl = outside(du + cu, -1)
            cr = outside(du + cu, +1)
            if cl and cr:
                t = min(1.0, max(0.0, (dv + ro) / (2 * ro)))
                col = tuple(int(round(cl[i] + (cr[i] - cl[i]) * t)) for i in range(3))
            else:
                col = cl or cr or fallback
            op[x, y] = col
        report.append(f"眼{idx}: {L:.0f}x{Wd:.0f} → {L:.0f}x{tw:.0f}  长宽比 {L / Wd:.2f} → {a.ratio:.2f}")

    out.save(a.dst)
    print(f"saved {a.dst}")
    for r in report:
        print("  " + r)


if __name__ == "__main__":
    main()
