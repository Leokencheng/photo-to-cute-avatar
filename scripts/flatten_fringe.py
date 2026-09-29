#!/usr/bin/env python3
"""刘海整平：把发际线重绘到滚动中位线上，去除锯齿尖角与缺口，还原"齐整平刘海"。

用法：
    python3 flatten_fringe.py <输入图> <输出图> [--window 0.2] [--maxskin 45] [--maxhair 30]

背景：模型画头发时经常在阔额上留下向下戳的三角尖角（锯齿刘海），或反过来在
刘海中间留一个向上的皮肤缺口，两者都违反"头发简化为大而平滑的块面、
不描绘零散发丝"。本脚本：

1. 逐列求"最上方的皮肤像素" y_top(x)，即发际线边界
2. 对 y_top(x) 取**滚动中位数**得到平滑主发际线（尖角与缺口是少数派，被中位数滤掉）
3. 按中位线重绘：
   - y_top 在中位线下方 → 用皮肤色填平下垂尖角（最多 --maxskin px）
   - y_top 在中位线上方 → 用发色补齐缺口（最多 --maxhair px）

只处理"上方确实是头发"的列，并限制在下垂/上抬幅度内，避免误伤侧发与脸型。
"""
import argparse
import os
import subprocess
import sys

try:
    from PIL import Image
except ImportError:
    for _c in ("/Users/leo/.workbuddy/binaries/python/envs/default/bin/python",
               "/usr/bin/python3"):
        if os.path.exists(_c) and subprocess.run(
                [_c, "-c", "import PIL"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            os.execv(_c, [_c] + sys.argv)
    raise


def median(vals):
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def bail(im, dst, why):
    """无可处理目标时也必须写出输出文件。

    ⚠️ 早期版本这里直接 raise 退出，于是目标路径留着【上一轮的旧图】，
    下游 check_icon / check_body 量测的是旧图，看起来"处理成功"其实全错。
    无论是否处理，都必须产出文件。
    """
    im.save(dst)
    print(f"saved {dst} | 直通（{why}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--window", type=float, default=0.2,
                    help="滚动中位数窗口占发际线列数的比例（越大越平，0.15~0.3）")
    ap.add_argument("--maxskin", type=int, default=45, help="单列最多填平的尖角深度")
    ap.add_argument("--maxhair", type=int, default=30, help="单列最多补齐的缺口深度")
    a = ap.parse_args()

    im = Image.open(a.src).convert("RGB")
    W, H = im.size
    px = im.load()
    bg = px[W - 12, 12]

    def lum(c):
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    def is_skin(c):
        return c[0] > 150 and c[0] - c[2] > 20 and c[0] - c[1] > 10

    def is_bgish(c):
        return all(abs(q - r) <= 14 for q, r in zip(c, bg))

    # ⚠️ 不能用"比背景更暗"判头发：深炭灰背景 rgb(22,32,35) 时 max(bg)−8 = 27，
    # 于是只有接近纯黑才算头发——栗棕/棕色头发（lum≈51）全被判为非头发，脚本直接失效。
    # 改用**相对肤色**的判据：比肤色明显暗、且不是背景色，即头发/深色衣物。
    skin_lums = sorted(lum(px[x, y]) for y in range(0, H, 5) for x in range(0, W, 5)
                       if is_skin(px[x, y]))
    skin_ref = skin_lums[len(skin_lums) // 2] if len(skin_lums) >= 50 else 210.0
    dark_max = skin_ref * 0.62

    def is_hair(c):
        return lum(c) < dark_max and not is_bgish(c)

    # 头发色块外接盒（画面上半部内最大的深色块）
    # 只在 y < 0.62H 内做连通块搜索：深色衣物（深蓝格纹等）也在 dark_max 以下，
    # 若放任它与头发连通，整块会一直延伸到画面底部，发际线搜索窗就被带偏。
    vis = bytearray(W * H)
    best = None
    ylimit = int(H * 0.62)
    for y0 in range(ylimit):
        row = y0 * W
        for x0 in range(W):
            if vis[row + x0] or not is_hair(px[x0, y0]):
                continue
            stack = [(x0, y0)]
            vis[row + x0] = 1
            xs, ys = [], []
            while stack:
                cx, cy = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < W and 0 <= ny < ylimit and not vis[ny * W + nx] \
                            and is_hair(px[nx, ny]):
                        vis[ny * W + nx] = 1
                        stack.append((nx, ny))
            if best is None or len(xs) > best[0]:
                best = (len(xs), min(xs), min(ys), max(xs), max(ys))
    if best is None:
        bail(im, a.dst, "未找到深色头发色块（浅色/银发不适用本脚本）")
        return
    # 反例：银发人像里"最大深色块"其实是【胶囊眼】，会被误当头发。
    # 头发块必须够大且位于画面上半部，否则判定为不适用。
    if best[0] < W * H * 0.02 or best[2] > H * 0.5:
        bail(im, a.dst, "最大深色块过小或位于下半部（疑似误判为眼睛/衣服，不适用）")
        return
    _, hx0, hy0, hx1, hy1 = best

    col_top = {}
    for x in range(hx0, hx1 + 1):
        for y in range(hy0, min(H, hy1 + 1)):
            if is_skin(px[x, y]):
                if any(is_hair(px[x, yy]) for yy in range(max(hy0, y - 40), max(hy0, y - 6) + 1)):
                    col_top[x] = y
                break

    if len(col_top) < 20:
        bail(im, a.dst, "有效发际线列太少（头发色块过小或非深色）")
        return
    top_min = min(col_top.values())
    # 只保留发际线附近的列：排除下滑到侧发/太阳穴的位置
    cols = [x for x in sorted(col_top) if top_min - 20 <= col_top[x] <= top_min + 160]
    if len(cols) < 20:
        bail(im, a.dst, "发际线主体列太少")
        return

    win = max(5, int(len(cols) * a.window))
    if win % 2 == 0:
        win += 1
    half = win // 2
    out = im.copy()
    op = out.load()
    n_skin = n_hair = c_skin = c_hair = 0
    for i, x in enumerate(cols):
        seg = [col_top[cols[j]] for j in range(max(0, i - half), min(len(cols), i + half + 1))]
        line = int(round(median(seg)))
        y_top = col_top[x]
        cut = y_top - line
        if cut >= 3 and cut <= a.maxskin:              # 下垂尖角 → 填皮肤
            sample = [px[x, yy] for yy in range(y_top + 4, min(H, y_top + 12))]
            if not sample:
                continue
            sample.sort(key=lambda c: c[0])
            col = sample[len(sample) // 2]
            for y in range(line, y_top):
                op[x, y] = col
            n_skin += 1
            c_skin = max(c_skin, cut)
        elif cut <= -3 and -cut <= a.maxhair:          # 缺口 → 补发色
            sample = [px[x, yy] for yy in range(max(0, line - 12), max(1, line - 2))]
            sample = [c for c in sample if is_hair(c)]
            if not sample:
                continue
            sample.sort(key=lambda c: c[0])
            col = sample[len(sample) // 2]
            for y in range(y_top, line):
                op[x, y] = col
            n_hair += 1
            c_hair = max(c_hair, -cut)

    out.save(a.dst)
    print(f"saved {a.dst} | 发际线列 {len(cols)} | 去尖 {n_skin} 列(最深 {c_skin}px) | "
          f"补缺口 {n_hair} 列(最深 {c_hair}px)")


if __name__ == "__main__":
    main()
