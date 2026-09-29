#!/usr/bin/env python3
"""
photo-to-cute-avatar 后处理：倾斜矫正 + 水印去除 + 背景纯色化 + 构图定位。

用法（默认 = 头+肩身体构图，推荐）：
    python3 fix_tilt.py <输入图> <输出图> --keepbody --angle 27

用法（变体 A：仅头部特写，下巴触底）：
    python3 fix_tilt.py <输入图> <输出图> --angle 18 --overscan 1.02

处理顺序：
1. 去水印：右下角平台水印用同行左侧背景色逐行填充（比裁切好，不丢构图）
2. 背景纯色化：把接近背景色的杂色统一成纯色，消除旋转接缝
3. 顺时针旋转 angle 度补足倾斜（模型常画成逆时针或完全不倾斜）
4. 构图定位，二选一：
   --keepbody（默认推荐）：把旋转后的【整幅主体】等比缩到目标高度（--hfrac，默认 0.92），
        再按「左下锚定」贴到 1024 画布上——身体/肩部自然溢出底边与左边。
        ***只等比缩放，绝不按包围盒裁切填充***。
   不加 --keepbody（变体 A）：包围盒裁切 + 按 overscan 放大 + 底部对齐，下巴触底、不画身体。

⚠️⚠️ 身体被削掉的唯一原因就是第 4 步走错模式：
   「包围盒裁切 + 放大填充」会把宽阔的肩部当作主体宽度，整幅等比放大到宽度撑满，
   高度随之溢出画布 → 底部对齐时又把溢出部分裁掉 → 结果只剩一颗悬浮的头。
   参考图 icon_girl.png 的达标值是：主体 bbox 宽≈86% / 高≈92% 画布，
   留白 上≈8% 左≈0 右≈15% 下≈0，底5%/底10% 内容覆盖 ≈43%，
   底行肤色像素 ≈0（底边应该是衣服/肩膀，不是脸）。改完必须跑 check_body.py 复验。

⚠️ --tol（背景纯色化容差）默认仅 7 且带亮度保护：胶囊眼约 rgb(16,21,30)、
   深炭灰背景约 rgb(38,46,50)，各通道差都在 22 以内——容差一旦放大到 20+，
   背景纯色化会把两只胶囊眼一起刷成背景色、**直接抹掉第一优先级元素**。
   调大 --tol 之后必须用 check_icon.py 复验两只眼睛仍然存在。

⚠️ 严禁旋转后居中裁切：居中裁切会切掉画面底部约 15~20%，底部构图直接被毁
"""
import argparse
import os
import subprocess
import sys
from collections import Counter

try:
    from PIL import Image
except ImportError:
    # 托管 Python (3.13) 默认没有 Pillow，自动切换到已装 PIL 的解释器
    for _cand in ("/Users/leo/.workbuddy/binaries/python/envs/default/bin/python",
                  "/usr/bin/python3"):
        if os.path.exists(_cand) and subprocess.run(
                [_cand, "-c", "import PIL"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            os.execv(_cand, [_cand] + sys.argv)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "Pillow"], check=False)
    from PIL import Image  # noqa: E402


def detect_bg(img):
    """取背景代表色：只采上边与左右两侧（避开右下角水印区）。"""
    w, h = img.size
    pts = [img.getpixel((x, 3)) for x in (3, w // 4, w // 2, 3 * w // 4, w - 4)]
    pts += [img.getpixel((3, h // 3)), img.getpixel((w - 4, h // 3)),
            img.getpixel((3, 2 * h // 3)), img.getpixel((w - 4, 2 * h // 3))]
    return Counter(pts).most_common(1)[0][0]


def near(c, bg, tol):
    return all(abs(a - b) <= tol for a, b in zip(c, bg))


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def bg_tones(px, w, h, step=6, tol=6):
    """从边框收集【所有】背景色调——背景不一定是单色。

    教训：模型有时把背景画成**两档灰**（实测 (56,59,61) 一大片 + (39,46,50) 一大片），
    只按"边框最常见的那一个颜色"做纯色化，另一档会完整留在画面里，成品背景看起来
    是两块拼接的色块。这里改成先收集所有色调，再逐一声调统一。

    收集规则（只收"背景相"的色）：
      · 低饱和：max−min ≤ 22           —— 排除衣领(173,176,149)、锈色衣(176,114,64)
      · 够暗：max ≤ 110                —— 同上；乳白背景会全部被过滤掉，走 detect_bg 兜底
      · 同族：与最常见那档亮度差 ≤ 30   —— 免得把贴边的深色头发/衣服当成背景
    """
    pts = [px[x, 1] for x in range(0, w, step)] + [px[x, h - 2] for x in range(0, w, step)]
    pts += [px[1, y] for y in range(0, h, step)] + [px[w - 2, y] for y in range(0, h, step)]
    cand = []
    for c in pts:
        if max(c) - min(c) > 22 or max(c) > 110:
            continue
        if all(not near(c, t, tol) for t in cand):
            cand.append(c)
    if not cand:
        return []
    cand.sort(key=lambda t: -sum(1 for c in pts if near(c, t, tol)))
    return [cand[0]] + [t for t in cand[1:] if abs(lum(t) - lum(cand[0])) <= 30]


def bg_areas(px, w, h, tones, tol=6):
    """统计每档色调在【全图】里占多少像素（只统计"背景相"的色）。

    为什么要按全图面积而不是按边框采样数：边框采样会高估那些"恰好贴在边上"的
    杂色（主体深色头发/白衣服的边缘抗锯齿像素也贴在边框上）。实测 `icon_i2.png`
    边框能采到 (2,7,9) 这种近纯黑，但它全图只有 236 px（0.05%），纯粹是抗锯齿；
    而真正铺满画面的背景是 (30,37,42)（99.4%）。按"最暗的一档"统一会整幅变黑。
    """
    cnt = [0] * len(tones)
    for y in range(h):
        for x in range(w):
            c = px[x, y]
            if max(c) - min(c) > 22 or max(c) > 110:
                continue
            best, bestd = None, None
            for i, t in enumerate(tones):
                if not near(c, t, tol):
                    continue
                if lum(c) < lum(t) - 8:      # 亮度保护：不把更暗的主体当背景
                    continue
                d = sum(abs(a - b) for a, b in zip(c, t))
                if bestd is None or d < bestd:
                    best, bestd = i, d
            if best is not None:
                cnt[best] += 1
    return cnt


def grow_background(px, w, h, bg, rounds=3, lum_span=40):
    """把背景向紧邻的"背景相"像素扩张，吃掉两档色调交界处的残环/光晕。

    两种灰调统一成一种之后，它们交界处的过渡像素（既不匹配 A 也不匹配 B）会留下
    一条亮度介于两者之间的细环，看起来像主体外面套了一圈淡淡的描边。
    这里做几轮 4-邻域扩张，把「低饱和 + 亮度在 [bg, bg+40] + 紧贴背景」的像素也刷成背景。

    安全边界：主体里最接近背景的深色（头发 lum≈32 / 胶囊眼 lum≈33）比背景（lum≈43）
    更暗，落在下界之外，永远不会被吃掉。
    """
    bg_l = lum(bg)
    for _ in range(rounds):
        add = []
        for y in range(h):
            for x in range(w):
                c = px[x, y]
                if max(c) - min(c) > 22:
                    continue
                lc = lum(c)
                if lc < bg_l or lc > bg_l + lum_span:
                    continue
                hit = False
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny] == bg:
                        hit = True
                        break
                if hit:
                    add.append((x, y))
        if not add:
            break
        for x, y in add:
            px[x, y] = bg


def content_bbox(img, bg, tol=12, min_run=8):
    """主体包围盒。底边判定：一行至少 min_run 个非背景像素，避免抗锯齿杂点。"""
    w, h = img.size
    px = img.load()
    minx, miny, maxx, maxy = w, h, -1, -1
    for y in range(h):
        cnt = 0
        rowmin, rowmax = w, -1
        for x in range(w):
            if not near(px[x, y], bg, tol):
                cnt += 1
                rowmin = min(rowmin, x)
                rowmax = max(rowmax, x)
        if cnt >= min_run:
            miny = min(miny, y)
            maxy = y
            minx = min(minx, rowmin)
            maxx = max(maxx, rowmax)
    if maxy < 0:
        raise SystemExit("未检出主体内容")
    return minx, miny, maxx, maxy


def report_body(canvas, bg, size):
    """身体构图自检：底部三条带覆盖率 + 四边留白 + 底行肤色像素。"""
    cp = canvas.load()
    bands = []
    for frac in (0.05, 0.10, 0.15):
        y0 = int(size * (1 - frac))
        tot = n = 0
        for y in range(y0, size, 2):
            for x in range(0, size, 2):
                tot += 1
                if not near(cp[x, y], bg, 26):
                    n += 1
        bands.append(n / tot * 100)
    b = content_bbox(canvas, bg)
    skin = sum(1 for x in range(size)
               if cp[x, size - 2][0] > 150 and cp[x, size - 2][0] - cp[x, size - 2][2] > 22)
    warn = []
    if min(bands[0], bands[1]) < 25:
        warn.append("身体被截没 → 调大 --hfrac / 确认生成图本身画了肩部")
    if b[0] > int(size * 0.12):
        warn.append("主体偏右 → 调大 --bleedx")
    if skin > 0.5 * size:
        # 露肩/吊带/大领口人像底边本来就会有一两百 px 是颈部或肩膀的皮肤，
        # 只有接近整行宽度才说明"脸贴在底边"（身体确实被截没）。与 check_body.py 口径一致。
        warn.append("底边几乎是整行肤色 → 生成图缺身体")
    print("  底部内容覆盖 " + " | ".join(f"底{f:.0%} {v:.0f}%" for f, v in
                                    zip((0.05, 0.10, 0.15), bands)))
    print(f"  留白 上{b[1]} 下{size - 1 - b[3]} 左{b[0]} 右{size - 1 - b[2]} | 底行肤色 {skin}px")
    print("  " + ("✓ 身体构图达标" if not warn else "⚠️ " + "；".join(warn)))


def is_bg(c, bg, tol):
    """背景判定：各通道都在容差内，且亮度也不比背景暗太多。

    亮度保护用于防止深色主体（尤其胶囊眼）被"背景纯色化"一并刷掉。
    """
    if not near(c, bg, tol):
        return False
    return lum(c) >= lum(bg) - 8


def remove_watermark(px, w, h, bg, tol=12):
    """清右下角平台水印（"AI生成 / WORKBUDDY"）——只清字形，绝不覆盖主体。

    两代实现的教训：
    ① 最初是"取同一行 x=0.84w 的颜色往右刷"，那一列落在头发上时会把整片头发色
       刷到右下角，形成一块突兀的矩形（实测：棕色长发那张右下角一大块棕色）。
    ② 改成"灰白且明显亮于背景"后，遇到**半透明深灰水印**（只比背景亮一点）又抓不到，
       留下淡淡残影。
    现在的判据是**形态学**的：水印字形 = 角落区域内「低饱和 + 连通块很小 + 细长」，
    主体（头发/衣服）要么高饱和、要么是连成一大片的色块，都会被保留。
    """
    y0, x0 = int(h * 0.86), int(w * 0.84)
    zw, zh = w - x0, h - y0
    # 字形是"碎块"：实测 42 个连通块，单个 60~700 px、bbox 最大约 35x35；
    # 主体色块（衣服/头发被光照亮的部分）一旦落进角落都是几千 px 的整块。
    zone_max_area = 1500
    zone_max_side = 70
    bg_lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]

    def near_bg(c):
        return near(c, bg, tol)

    def wm_cand(c):
        """水印字形候选：低饱和 + 明显亮于背景。

        实测水印是 rgb(138,143,145) 的浅灰（max−min=7，亮度 142），
        而背景是 rgb(21,31,35)（亮度 29）——亮度条件一卡就分开了。
        反过来深色西装/深色裤子虽然也低饱和，但亮度不超过背景，天然排除。
        """
        lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
        # 阈值取 bg_lum + 10 而不是 +25：字形边缘与背景混合出的暗灰晕圈
        # （实测 (40,45,48)，亮度 44）会被 +25 漏掉，留下轮廓残影；
        # 放宽后由下面的"连通块面积/稀疏度"条件兜底，实心的深色衣物仍会被排除。
        return max(c) - min(c) < 30 and lum > bg_lum + 10

    cand = bytearray(zw * zh)
    for yy in range(zh):
        for xx in range(zw):
            c = px[x0 + xx, y0 + yy]
            if near_bg(c):
                px[x0 + xx, y0 + yy] = bg        # 背景杂色统一
            elif wm_cand(c):
                cand[yy * zw + xx] = 1

    seen = bytearray(zw * zh)
    for yy in range(zh):
        for xx in range(zw):
            if seen[yy * zw + xx] or not cand[yy * zw + xx]:
                continue
            stack = [(xx, yy)]
            seen[yy * zw + xx] = 1
            pts = []
            while stack:
                cxx, cyy = stack.pop()
                pts.append((cxx, cyy))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cxx + dx, cyy + dy
                    if 0 <= nx < zw and 0 <= ny < zh and not seen[ny * zw + nx] \
                            and cand[ny * zw + nx]:
                        seen[ny * zw + nx] = 1
                        stack.append((nx, ny))
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
            # 面积小 + 外接盒小 ⇒ 字形；实心大色块（衣服/头发）两项都会超
            if len(pts) <= zone_max_area and max(bw, bh) <= zone_max_side:
                for p in pts:
                    px[x0 + p[0], y0 + p[1]] = bg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--angle", type=float, default=18.0, help="顺时针旋转度数")
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--tol", type=int, default=7,
                    help="背景纯色化容差（默认 7，带亮度保护；调大可能抹掉胶囊眼，须复验）")
    ap.add_argument("--overscan", type=float, default=1.02,
                    help="[变体 A] 放大倍率，1.00~1.10；仅在不加 --keepbody 时生效")
    ap.add_argument("--overshoot", type=float, default=0.05,
                    help="[变体 A] 主体底边超出画布底边的比例（0.03~0.08）")
    ap.add_argument("--biasx", type=float, default=-0.04,
                    help="[变体 A] 水平偏移比例，负值偏左")
    ap.add_argument("--nocrop", action="store_true",
                    help="跳过第 4 步（包围盒裁切/缩放/底部对齐）。构图已达标时使用")
    # ---- 身体构图（默认推荐）----
    ap.add_argument("--keepbody", action="store_true",
                    help="身体构图模式：整幅等比缩放 + 左下锚定，保留头+肩/上半身（推荐默认）")
    ap.add_argument("--hfrac", type=float, default=0.92,
                    help="[--keepbody] 主体高度占画布比例，参考值 0.92（留白约上 8%%）")
    ap.add_argument("--bleedx", type=float, default=0.00,
                    help="[--keepbody] 左边溢出比例，参考值 0.00（主体左边贴/略出画布左缘）")
    ap.add_argument("--bleedy", type=float, default=0.02,
                    help="[--keepbody] 底边溢出比例，参考值 0.02（肩部自然溢出底边）")
    a = ap.parse_args()

    img = Image.open(a.src).convert("RGB")
    w, h = img.size
    px = img.load()

    # 0) 定背景色：模型可能把背景画成两档灰，先收集全部色调，再从"真正铺开"的档位里
    #    取最暗的一档（规范要求"近黑色深炭灰"）。
    #    ⚠️ 不能直接取所有档里最暗的：边框常采到主体边缘的抗锯齿近黑色（占比 0.05%），
    #       拿它当背景会把整幅刷黑。所以只有占比 ≥5% 的档位才算"真·背景档"。
    TONES = bg_tones(px, w, h)
    if TONES:
        cnt = bg_areas(px, w, h, TONES)
        tot = max(sum(cnt), 1)
        strong = [t for t, n in zip(TONES, cnt) if n >= 0.05 * tot]
        BG = min(strong or TONES, key=lum)
        if len(TONES) > 1:
            detail = "、".join(f"{t}(lum {lum(t):.0f} {100*n/tot:.1f}%)"
                              for t, n in zip(TONES, cnt))
            print(f"  背景色调 {len(TONES)} 档 → 基准 {BG}（占比 ≥5% 的档：{len(strong)} 档）；{detail}")
    else:
        BG = detect_bg(img)
        TONES = [BG]

    # 1) 背景纯色化（多色调；带亮度保护，避免刷掉深色胶囊眼）
    for y in range(h):
        for x in range(w):
            if any(is_bg(px[x, y], t, a.tol) for t in TONES):
                px[x, y] = BG

    # 2) 背景向紧邻的"背景相"像素扩张几轮，吃掉两档色调交界处的过渡残环
    grow_background(px, w, h, BG)

    # 3) 去水印（右下角）——放在纯色化之后：背景已经是单色，字形判据不会误抓另一档灰
    remove_watermark(px, w, h, BG)

    # 3) 旋转（扩展画布）
    rot = img.rotate(-a.angle, resample=Image.BICUBIC, expand=True, fillcolor=BG)
    rp = rot.load()
    RW, RH = rot.size

    if a.keepbody:
        # === 身体构图（默认推荐）===
        # 只做「整幅等比缩放 + 左下锚定」，绝不按包围盒裁切填充——
        # 这样才能保住头以下的身体/肩部，让它们自然溢出底边与左边。
        minx, miny, maxx, maxy = content_bbox(rot, BG)
        bw, bh = maxx - minx + 1, maxy - miny + 1
        s = min(a.size * a.hfrac / bh, a.size / bw)
        nw, nh = max(1, int(bw * s)), max(1, int(bh * s))
        body = rot.crop((minx, miny, maxx + 1, maxy + 1)).resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (a.size, a.size), BG)
        xoff = -int(a.size * a.bleedx)
        yoff = a.size - nh + int(a.size * a.bleedy)
        canvas.paste(body, (xoff, yoff))
        canvas.save(a.dst)
        print(f"saved {a.dst} | --keepbody scale={s:.2f} 补转 {a.angle}° | "
              f"主体 {bw}x{bh} → {nw}x{nh} | 背景色 {BG}")
        report_body(canvas, BG, a.size)
        return

    if a.nocrop:
        # 构图已达标的直通模式：保留原画布尺寸，只做去水印 + 背景纯色化（+可选旋转）
        if a.angle == 0:
            img.save(a.dst)
            print(f"saved {a.dst} | --nocrop 直通（未裁切缩放）| 背景色 {BG}")
        else:
            # 小幅补转后回正为正方形：取中心 a.size 见方
            cx, cy = RW // 2, RH // 2
            half = a.size // 2
            rot.crop((cx - half, cy - half, cx - half + a.size,
                      cy - half + a.size)).save(a.dst)
            print(f"saved {a.dst} | --nocrop 补转 {a.angle}° 后中心回正（未再缩放）| 背景色 {BG}")
        return

    # 4) 找主体包围盒：底边要求一行至少 8 个非背景像素，避免抗锯齿杂点导致底部留白
    minx, miny, maxx, maxy = RW, RH, -1, -1
    for y in range(RH):
        cnt = 0
        rowmin, rowmax = RW, -1
        for x in range(RW):
            if not near(rp[x, y], BG, 12):
                cnt += 1
                rowmin = min(rowmin, x)
                rowmax = max(rowmax, x)
        if cnt >= 8:
            miny = min(miny, y)
            maxy = y
            minx = min(minx, rowmin)
            maxx = max(maxx, rowmax)
    if maxy < 0:
        raise SystemExit("未检出主体内容")

    bb = rot.crop((minx, miny, maxx + 1, maxy + 1))
    s = a.size * a.overscan / max(bb.width, bb.height)
    bb = bb.resize((max(1, int(bb.width * s)), max(1, int(bb.height * s))), Image.LANCZOS)
    # 底部对齐：主体底边略超出画布底边 overshoot，保证下巴接触/超出底边，
    # 同时不再靠大倍率放大去顶底边（那会让整张图溢出、失去留白）
    xoff = (a.size - bb.width) // 2 + int(a.size * a.biasx)
    yoff = a.size - bb.height + int(a.size * a.overshoot)
    canvas = Image.new("RGB", (a.size, a.size), BG)
    canvas.paste(bb, (xoff, yoff))
    canvas.save(a.dst)

    # 自检 1：底行主体覆盖率（背景占比越低说明下巴/肩部越完整地抵达底边）
    cp = canvas.load()
    n = sum(1 for x in range(a.size) if near(cp[x, a.size - 1], BG, 26))
    cov = 100 - n / a.size * 100
    # 自检 2：四边溢出比例——任一边 100% 贴边即说明主体溢出过多、失去留白
    def edge_frac(pts):
        c = sum(1 for p in pts if not near(p, BG, 26))
        return c / len(pts)
    top = edge_frac([cp[x, 0] for x in range(a.size)])
    left = edge_frac([cp[0, y] for y in range(a.size)])
    right = edge_frac([cp[a.size - 1, y] for y in range(a.size)])
    warn = []
    if cov < 20:
        warn.append("底部悬空 → 调大 --overshoot")
    if top > 0.55:
        warn.append("顶部溢出过多 → 调小 --overscan")
    if left > 0.55 or right > 0.55:
        warn.append("左右溢出过多 → 调小 --overscan")
    flag = "OK" if not warn else "⚠️ " + "；".join(warn)
    print(f"saved {a.dst} | scale={s:.2f} | 底行覆盖 {cov:.0f}% | "
          f"边溢出 上{top:.0%} 左{left:.0%} 右{right:.0%} | {flag}")


if __name__ == "__main__":
    main()
