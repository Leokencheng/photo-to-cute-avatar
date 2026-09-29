#!/usr/bin/env python3
"""共用工具：胶囊眼的检出与量测。

为什么不用"比背景更暗"来判眼睛：模型经常把胶囊眼画成与深炭灰背景几乎同色的
近黑色（实测 眼 rgb(33,34,35) vs 背景 rgb(32,34,35)，各通道差 ≤2），任何基于
暗度阈值的判据都会失效。

改用**调色板无关**的"脸内部空洞"法：
  1. 先取出肤色掩码（亮、暖、r-b 明显为正）
  2. 逐行/逐列求肤色的跨度；既在行跨度内、又在列跨度内的非肤色像素 = 脸的"空洞"
  3. 空洞里"填充率高"的连通块就是胶囊眼（细长、实心）；眼镜框这类细环填充率很低，会被排除

优点：不依赖具体色值、旋转后依然成立、对深色/浅色背景都适用。
"""
import os
import subprocess
import sys


def import_pil():
    """导入 PIL；托管 Python 3.13 无 Pillow 时自动切换解释器。"""
    try:
        from PIL import Image
        return Image
    except ImportError:
        for cand in ("/Users/leo/.workbuddy/binaries/python/envs/default/bin/python",
                     "/usr/bin/python3"):
            if os.path.exists(cand) and subprocess.run(
                    [cand, "-c", "import PIL"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                os.execv(cand, [cand] + sys.argv)
        raise


Image = import_pil()

MIN_EYE_AREA = 1200          # 胶囊眼最小像素数
MIN_FILL = 0.45              # 填充率下限（细环状眼镜框约 0.2，胶囊约 0.7）
MAX_AREA_FRAC = 0.04         # 单只眼睛最大占比（用于排除头发等大色块）


def is_skin(c):
    return c[0] > 140 and c[0] - c[2] > 18 and c[0] - c[1] > 8


def find_eyes(im, want=2):
    """返回 (眼睛列表, px, bg)。每只眼睛 = dict(pts, cx, cy, n)。

    ⚠️ 只按面积取 Top-N 会出错：实拍遇到过头/肩人像里「头发与手臂之间围出的
    颈部空洞」面积（23708）比真正的胶囊眼（5797/5750）还大，直接把一只眼睛挤出榜单，
    于是倾角量出 −88.6°、长宽比 0.74，全是假的。
    因此这里改为**成对优选**：两只胶囊眼必然「面积接近 + 左右并排 + 填充率高 + 近黑」，
    用这四条约束一起打分选对，比单看面积稳得多。
    """
    W, H = im.size
    px = im.load()
    bg = px[W - 12, 12]

    skin = bytearray(W * H)
    for y in range(H):
        base = y * W
        for x in range(W):
            if is_skin(px[x, y]):
                skin[base + x] = 1

    row_min = [W] * H
    row_max = [-1] * H
    for y in range(H):
        base = y * W
        for x in range(W):
            if skin[base + x]:
                if x < row_min[y]:
                    row_min[y] = x
                if x > row_max[y]:
                    row_max[y] = x
    col_min = [H] * W
    col_max = [-1] * W
    for x in range(W):
        for y in range(H):
            if skin[y * W + x]:
                if y < col_min[x]:
                    col_min[x] = y
                if y > col_max[x]:
                    col_max[x] = y
    col_ok = [col_max[x] > col_min[x] for x in range(W)]

    # 脸内部空洞
    hole = bytearray(W * H)
    for y in range(H):
        base = y * W
        if row_max[y] < 0:
            continue
        for x in range(row_min[y] + 1, row_max[y]):
            if not skin[base + x] and col_ok[x] and col_min[x] < y < col_max[x]:
                hole[base + x] = 1

    vis = bytearray(W * H)
    cands = []
    max_area = MAX_AREA_FRAC * W * H
    for y in range(H):
        base = y * W
        for x in range(W):
            if vis[base + x] or not hole[base + x]:
                continue
            stack = [(x, y)]
            vis[base + x] = 1
            pts = []
            while stack:
                cx0, cy0 = stack.pop()
                pts.append((cx0, cy0))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx0 + dx, cy0 + dy
                    if 0 <= nx < W and 0 <= ny < H and not vis[ny * W + nx] \
                            and hole[ny * W + nx]:
                        vis[ny * W + nx] = 1
                        stack.append((nx, ny))
            n = len(pts)
            if n < MIN_EYE_AREA or n > max_area:
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
            fill = n / (bw * bh)
            if fill < MIN_FILL:
                continue                      # 细环（眼镜框等）排除
            if bw > 0.5 * W or bh > 0.5 * H:
                continue
            lum = sum(0.299 * px[q[0], q[1]][0] + 0.587 * px[q[0], q[1]][1]
                      + 0.114 * px[q[0], q[1]][2] for q in pts) / n
            cands.append(dict(pts=pts, n=n, cx=sum(xs) / n, cy=sum(ys) / n,
                              fill=fill, lum=lum))

    if not cands:
        return [], px, bg

    # 成对优选：面积接近 + 左右并排 + 填充率高 + 近黑
    best, best_score = None, None
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            a, b = cands[i], cands[j]
            dx = abs(a["cx"] - b["cx"])
            dy = abs(a["cy"] - b["cy"])
            if dx < dy:
                continue                      # 胶囊眼必然左右并排
            dev = abs(a["n"] - b["n"]) / max(a["n"], b["n"])
            if dev > 0.35:
                continue                      # 两眼等大
            score = (dev * 2.0
                     + (2.0 - a["fill"] - b["fill"])
                     + (dy / max(dx, 1.0)) * 0.4
                     + (a["lum"] + b["lum"]) / 2 / 255 * 0.6)
            if best_score is None or score < best_score:
                best, best_score = (a, b), score

    if best is None:
        cands.sort(key=lambda c: -c["n"])
        return cands[:want], px, bg
    rest = [c for c in cands if c is not best[0] and c is not best[1]]
    rest.sort(key=lambda c: -c["n"])
    # 调用方按 eyes[0] / eyes[1] 取两只眼，这里必须裁到 want 以内，不能多返回
    return ([best[0], best[1]] + rest)[:max(want, 2)], px, bg



def pair_frame(eyes):
    """以两眼中心连线建立正交框架：d = 连线方向，p = 长轴方向（垂直连线）。

    注意只取前两只：find_eyes 可能多返回候选，多取会污染连线方向。
    """
    import math
    pair = sorted(eyes, key=lambda c: c["cx"])[:2]
    if len(pair) < 2:
        raise ValueError("需要两只眼睛才能建立正交框架")
    dx, dy = pair[1]["cx"] - pair[0]["cx"], pair[1]["cy"] - pair[0]["cy"]
    dist = math.hypot(dx, dy) or 1.0
    d = (dx / dist, dy / dist)
    p = (-d[1], d[0])
    return pair, d, p, math.degrees(math.atan2(dy, dx))


def measure(eye, d, p):
    """返回 (主轴长, 宽, 投影中心 cu, cv)。"""
    pu = [q[0] * p[0] + q[1] * p[1] for q in eye["pts"]]
    pv = [q[0] * d[0] + q[1] * d[1] for q in eye["pts"]]
    return (max(pu) - min(pu) + 1, max(pv) - min(pv) + 1,
            (max(pu) + min(pu)) / 2, (max(pv) + min(pv)) / 2)
