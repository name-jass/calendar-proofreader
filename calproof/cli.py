"""命令行入口：python -m calproof.cli 文件.pdf --year 2027

也可以直接给图片：png / jpg 等，程序会把它当成"一页稿子"来识别。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from .pipeline import run
from .report import build_report

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


def _image_to_pdf(path: str, year: int) -> str:
    """把一张图片包成单页 PDF，好让后面的流程统一处理。"""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # type: ignore

    from PIL import Image

    im = Image.open(path).convert("RGB")
    w, h = im.size
    dpi = 150.0
    doc = fitz.open()
    page = doc.new_page(width=w * 72.0 / dpi, height=h * 72.0 / dpi)
    buf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    buf.close()
    im.save(buf.name)
    page.insert_image(page.rect, filename=buf.name)
    out = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    out.close()
    doc.save(out.name)
    doc.close()
    try:
        os.remove(buf.name)
    except OSError:
        pass
    return out.name


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="calproof", description="台历日期校对（支持已转曲的矢量 PDF 与图片）")
    ap.add_argument("pdf", help="要校对的 PDF 或图片文件")
    ap.add_argument("--year", type=int, required=True, help="台历年份，如 2027")
    ap.add_argument("--first-col", choices=["auto", "mon", "sun"], default="auto",
                    help="第一列是星期几：auto=按版式自动判断（默认），mon=周一，sun=周日")
    ap.add_argument("--start-month", type=int, default=1, help="第 1 个月历页对应几月，默认 1")
    ap.add_argument("-o", "--out", default=None, help="报告输出路径")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.pdf):
        print(f"找不到文件：{a.pdf}")
        return 2

    base = os.path.dirname(os.path.abspath(a.pdf))
    stem = os.path.splitext(os.path.basename(a.pdf))[0]
    out = a.out or os.path.join(base, stem + "_校对报告.html")

    src = a.pdf
    tmp_pdf = None
    if os.path.splitext(a.pdf)[1].lower() in IMG_EXT:
        print(f"正在读图片 {os.path.basename(a.pdf)} …")
        src = tmp_pdf = _image_to_pdf(a.pdf, a.year)

    print(f"正在校对 {os.path.basename(a.pdf)} …")
    res = run(src, a.year, a.first_col, a.start_month, None,
              progress=lambda m: print("  " + m))
    if tmp_pdf:
        try:
            os.remove(tmp_pdf)
        except OSError:
            pass

    print()
    print("=" * 46)
    print(f"已校对 {res['checked']} 个月：{res['n_error']} 个错误，{res['n_warn']} 处待确认")
    for p in res["pages"]:
        if p.get("skip"):
            continue
        if p["n_error"]:
            print(f"  ✕ {a.year}年{p['month']}月（第{p['page']}页）")
            for f in p["findings"]:
                if f["severity"] == "error":
                    print(f"      - {f['msg']}")
    print("=" * 46)
    build_report(res, out)
    print(f"报告已生成：{out}")
    return 0 if res["n_error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
