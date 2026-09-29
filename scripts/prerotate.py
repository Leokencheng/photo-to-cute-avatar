#!/usr/bin/env python3
"""预旋转原图：把"补倾角"从后处理搬到生成之前。

为什么必须这么做：
    模型画这种头像时有个稳定偏差——原图人物是正的，它也会画成【右眼更高】
    （实测 -10° 左右）。而规范要求【左眼更高 15~20°】，差着约 27°。
    如果放到生成之后再旋转整幅图补这 27°，会把【身体/肩部一起转过去】：
    宽阔的肩部变成画面底部一道斜条，底部内容覆盖从 43% 掉到 10%，
    参考图那种"头以下身体在画面里"的构图直接没了。

    换思路——先把原图按需要的角度转好，再交给模型。模型 follow 输入姿态很准，
    生成图天生就是目标倾角，后处理只需要裁切定位，不需要旋转，身体自然完整。

用法：
    python3 prerotate.py <原图> <输出图> [--angle 27] [--zoom 1.0]

    --angle  顺时针预旋转度数。经验值 = 目标倾角(17) − 模型固有偏差(−10) ≈ 27
    --zoom   额外放大（>1 更紧，用于预旋转后构图偏松时）
    --expand 预旋转前先把画面向外撑大（占边长比例），抵消内接裁切自带的 1.22x 放大。
             紧特写（头顶已经贴边）必须加，经验值 0.15
    --expandcolor 'R,G,B'  撑大那圈的填充色。源图背景是复杂风景时必须给
             （默认边缘复制，会把山脊/天空复制成宽带，被模型抄进成品背景）

输出为正方形，四角不再出现填充色接缝：预旋转前先削掉最外圈 1px 细边，再用边缘
复制作 padding，最后按原边长的内接尺寸裁切（见 shave / edge_pad 的说明）。
"""
import argparse
import math
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


def edge_bg(img):
    """取边缘代表色当填充色（避开人脸所在的中间区域）。"""
    w, h = img.size
    px = img.load()
    pts = [px[x, 2] for x in range(0, w, max(1, w // 16))]
    pts += [px[x, h - 3] for x in range(0, w, max(1, w // 16))]
    pts += [px[2, y] for y in range(0, h, max(1, h // 16))]
    pts += [px[w - 3, y] for y in range(0, h, max(1, h // 16))]
    return Counter(pts).most_common(1)[0][0]


def shave(img, n):
    """削掉四周 n 像素。

    很多导出图（截图 / 修图软件导出）最外圈带一条 1px 纯黑或纯白细边。
    它本身肉眼几乎看不见，但旋转插值会把它变成一条【斜向虚线】，模型会当成
    真实形状抄进生成图。实测 i.png 整行 y=0 亮度为 0，预旋转后右上角出现一条
    从 (470,0) 斜到 (559,60) 的虚线。
    """
    if n <= 0:
        return img
    w, h = img.size
    if w - 2 * n < 8 or h - 2 * n < 8:
        return img
    return img.crop((n, n, w - n, h - n))


def edge_pad(img, pad):
    """用边缘像素向外复制出一圈 padding。

    为什么要 padding：直接 `rotate(fillcolor=背景色)` 时，照片边界与填充色之间
    存在硬跳变，BICUBIC 跨过这条跳变采样会产生 1px 暗色振铃——又一条会被模型
    抄走的线。先做【边缘复制】再旋转，边界两侧内容连续，振铃消失。
    画布变大，但下面的裁切窗仍按原边长的内接尺寸取，所以取景范围不变，只是把
    原本"纯色填充的角"换成了看不见的复制背景；顺带给 --bias 留出可见区外的
    余量，安全上限为 |bias| ≤ padfrac。
    """
    if pad <= 0:
        return img
    w, h = img.size
    out = Image.new("RGB", (w + 2 * pad, h + 2 * pad))
    out.paste(img, (pad, pad))
    out.paste(img.crop((0, 0, w, 1)).resize((w, pad), Image.NEAREST), (pad, 0))
    out.paste(img.crop((0, h - 1, w, h)).resize((w, pad), Image.NEAREST), (pad, pad + h))
    out.paste(img.crop((0, 0, 1, h)).resize((pad, h), Image.NEAREST), (0, pad))
    out.paste(img.crop((w - 1, 0, w, h)).resize((pad, h), Image.NEAREST), (pad + w, pad))
    for cx, cy, px, py in ((0, 0, 0, 0), (w - 1, 0, pad + w, 0),
                           (0, h - 1, 0, pad + h), (w - 1, h - 1, pad + w, pad + h)):
        out.paste(Image.new("RGB", (pad, pad), img.getpixel((cx, cy))), (px, py))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--angle", type=float, default=27.0, help="顺时针预旋转度数")
    ap.add_argument("--zoom", type=float, default=1.0, help="额外放大倍率")
    ap.add_argument("--biasx", type=float, default=0.0,
                    help="内接正方形水平偏移（占内接边长比例），-0.2 表示左移 20%%")
    ap.add_argument("--biasy", type=float, default=0.0,
                    help="内接正方形垂直偏移，-0.2 表示上移 20%%（人像竖构图常用，避免旋转裁切时削掉头顶）")
    ap.add_argument("--cropy", type=float, default=0.5,
                    help="竖构图先切正方时的取景位置：0=贴顶（保住头顶，适合大头人像），0.5=居中，1=贴底")
    ap.add_argument("--shave", type=int, default=2,
                    help="预旋转前先削掉四周 n 像素，去掉导出图常见的最外圈 1px 黑/白细边（默认 2）")
    ap.add_argument("--padfrac", type=float, default=0.12,
                    help="边缘复制 padding 占边长比例；同时决定 --bias 的安全上限（默认 0.12，即 |bias| ≤ 0.12）")
    ap.add_argument("--expand", type=float, default=0.0,
                    help="预旋转前先把画面向外撑大（占边长比例），用于抵消内接裁切自带的 1.22x 放大；"
                         "紧特写（头顶贴边）用 0.15 左右")
    ap.add_argument("--expandcolor", default="",
                    help="撑大那圈的填充色 'R,G,B'；默认（空）= 边缘复制。"
                         "源图背景是复杂风景时必须指定目标背景色（如 '24,25,26'），"
                         "否则复制出的宽带会被模型当成真实结构抄进成品背景")
    a = ap.parse_args()

    img = Image.open(a.src).convert("RGB")
    w, h = img.size
    side = min(w, h)
    # 先切成正方：竖构图默认居中会削掉头顶，用 --cropy 控制取景
    free = h - side
    y_start = int(free * a.cropy)
    img = img.crop(((w - side) // 2, y_start,
                    (w - side) // 2 + side, y_start + side))
    side = img.size[0]
    img = shave(img, a.shave)          # ① 削掉最外圈细边
    side = img.size[0]

    # ①' 向外撑大（可选）：内接裁切会把内容放大 1/(cos+sin) ≈ 1.22x，紧特写会被削掉头顶。
    #    撑大后 side 变大，内接窗随之变大，内容相对缩小，取景回到原构图。
    #    注意这一步必须发生在计算 side 之前，所以不能复用 --padfrac（那个只加安全余量）。
    if a.expand > 0:
        p = int(round(side * a.expand))
        if a.expandcolor:
            col = tuple(int(v) for v in a.expandcolor.split(","))
            big = Image.new("RGB", (side + 2 * p, side + 2 * p), col)
            big.paste(img, (p, p))
            img = big
        else:
            img = edge_pad(img, p)
        print(f"  预膨胀 {p}px（占边长 {a.expand:.3f}）"
              f"{' 填充色 ' + a.expandcolor if a.expandcolor else ' 边缘复制'}"
              f" → {img.size[0]}x{img.size[1]}")
        side = img.size[0]

    bg = edge_bg(img)
    pad = int(round(side * a.padfrac))

    # ② 边缘复制 padding 后再旋转：消除照片边界与填充色之间的 BICUBIC 暗色振铃
    #    （振铃会变成一条斜向细线，被模型当作真实形状抄进生成图）。
    # ③ 旋转（画布扩展）后裁【原边长的内接正方形】——取景范围与不加 padding 时
    #    完全一致，只是原本的纯色填充角落换成了看不见的复制背景。
    # 内接裁切本身会放大约 1/(cos+sin) 倍（15° 时约 1.22x），人像会被削掉头顶
    # ——所以用 --biasy / --biasx 把裁切窗口推向要保留的部位，上限为 ±padfrac。
    padded = edge_pad(img, pad)
    rot = padded.rotate(-a.angle, resample=Image.BICUBIC, expand=True, fillcolor=bg)
    RW, RH = rot.size
    rad = math.radians(a.angle)
    inner = int(side / (math.cos(rad) + math.sin(rad)) / a.zoom)
    cx, cy = RW // 2, RH // 2
    ox, oy = int(inner * a.biasx), int(inner * a.biasy)
    lim = int(inner * a.padfrac)
    for name, v in (("--biasx", ox), ("--biasy", oy)):
        if abs(v) > lim:
            print(f"⚠️  {name}={v/ max(inner,1):+.3f} 超出安全上限 ±{a.padfrac:.2f}"
                  f"（会带出填充角），已钳制")
    ox, oy = max(-lim, min(lim, ox)), max(-lim, min(lim, oy))
    x0 = min(max(cx - inner // 2 + ox, 0), RW - inner)
    y0 = min(max(cy - inner // 2 + oy, 0), RH - inner)
    out = rot.crop((x0, y0, x0 + inner, y0 + inner))
    out.resize((side, side), Image.LANCZOS).save(a.dst)
    print(f"saved {a.dst} | 顺时针预旋转 {a.angle}° 内接正方形裁切 "
          f"（shave {a.shave}px / pad {pad}px）| "
          f"{side}x{side} → {inner}x{inner} → {side}x{side}")


if __name__ == "__main__":
    main()
