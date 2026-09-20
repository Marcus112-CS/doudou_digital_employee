# -*- coding: utf-8 -*-
"""
VM Probe — 巡检报告 PDF 生成模块

用法:
    from report import build_report
    pdf = build_report(results, title="虚拟机巡检报告")
    open("巡检报告.pdf", "wb").write(pdf)

results 为 probe_one() 返回的列表，元素结构:
    {host, port, username, ok, data, error}

依赖: reportlab（离线包内已带）
字体: 优先用包内 fonts/，其次系统字体，最后回退 reportlab 内置 CID 字体 STSong-Light
"""
from __future__ import annotations

import datetime
import os
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------- 告警阈值
WARN = {"cpu": 80.0, "mem": 85.0, "disk": 85.0}
CRIT = {"cpu": 90.0, "mem": 95.0, "disk": 95.0}

C_MAIN = colors.HexColor("#1e40af")
C_HEAD_BG = colors.HexColor("#eef2ff")
C_GRID = colors.HexColor("#d5dbe3")
C_TEXT = colors.HexColor("#1f2937")
C_MUTED = colors.HexColor("#6b7280")
C_OK = colors.HexColor("#16a34a")
C_WARN = colors.HexColor("#d97706")
C_CRIT = colors.HexColor("#dc2626")

PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# ---------------------------------------------------------------- 字体
_HERE = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_HERE, "fonts")

_PKG_FONTS = (
    ("VPSans", os.path.join(_FONT_DIR, "VPSans-Regular.ttf")),
    ("VPSans-Bold", os.path.join(_FONT_DIR, "VPSans-Bold.ttf")),
)

# 系统常见中文字体（按优先级）
_SYS_FONTS = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)

_FONT_STATE = {"done": False, "regular": "Helvetica", "bold": "Helvetica-Bold"}


def _try_ttf(name, path):
    """注册 TTF/TTC/OTF 字体；TTC 依次尝试子字体索引"""
    if not os.path.isfile(path):
        return False
    for idx in (0, 1, 2):
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
            return True
        except Exception:
            continue
    return False


def setup_fonts():
    """注册中文字体，返回 (常规字体名, 粗体字体名)"""
    if _FONT_STATE["done"]:
        return _FONT_STATE["regular"], _FONT_STATE["bold"]

    reg = bld = ""

    # 1) 包内字体（随包分发，最可靠）
    if _try_ttf(*_PKG_FONTS[0]):
        reg = _PKG_FONTS[0][0]
    if _try_ttf(*_PKG_FONTS[1]):
        bld = _PKG_FONTS[1][0]

    # 2) 系统字体
    if not reg:
        for path in _SYS_FONTS:
            if _try_ttf("VPSansSys", path):
                reg = "VPSansSys"
                break
    if not bld and reg:
        bld = reg      # 系统字体常只有常规体，粗体用同一个顶替

    # 3) 兜底：reportlab 内置 Adobe 中文 CID 字体（无需字体文件）
    if not reg:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            reg = bld = "STSong-Light"
        except Exception:
            reg, bld = "Helvetica", "Helvetica-Bold"

    if reg == bld and reg not in ("Helvetica", "STSong-Light"):
        try:
            pdfmetrics.registerFontFamily(reg, normal=reg, bold=bld,
                                          italic=reg, boldItalic=bld)
        except Exception:
            pass

    _FONT_STATE.update(done=True, regular=reg, bold=bld)
    return reg, bld


# ---------------------------------------------------------------- 样式
def _styles():
    reg, bld = setup_fonts()
    return {
        "cover_title": ParagraphStyle("ct", fontName=bld, fontSize=26, leading=34,
                                      alignment=TA_CENTER, textColor=C_MAIN),
        "cover_sub": ParagraphStyle("cs", fontName=reg, fontSize=11, leading=18,
                                    alignment=TA_CENTER, textColor=C_MUTED),
        "h1": ParagraphStyle("h1", fontName=bld, fontSize=14, leading=20,
                             textColor=C_MAIN, spaceBefore=2, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName=bld, fontSize=10.5, leading=15,
                             textColor=C_TEXT, spaceBefore=8, spaceAfter=4),
        "cell": ParagraphStyle("cell", fontName=reg, fontSize=8, leading=11,
                               textColor=C_TEXT),
        "cellb": ParagraphStyle("cellb", fontName=bld, fontSize=8, leading=11,
                                textColor=C_TEXT),
        "head": ParagraphStyle("head", fontName=bld, fontSize=8.2, leading=11,
                               textColor=C_TEXT, alignment=TA_CENTER),
        "small": ParagraphStyle("small", fontName=reg, fontSize=7.6, leading=11,
                                textColor=C_MUTED),
        "note": ParagraphStyle("note", fontName=reg, fontSize=9, leading=14,
                               textColor=C_TEXT, alignment=TA_LEFT),
    }


def P(text, style):
    """可自动换行的单元格内容"""
    return Paragraph(escape("" if text is None else str(text)), style)


# ---------------------------------------------------------------- 工具
def _fmt_gb(mb):
    if mb is None:
        return "-"
    mb = float(mb)
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _level(value, kind):
    if value is None:
        return "ok"
    if value >= CRIT.get(kind, 95):
        return "crit"
    if value >= WARN.get(kind, 85):
        return "warn"
    return "ok"


_LEVEL_COLOR = {"ok": C_TEXT, "warn": C_WARN, "crit": C_CRIT}


def _pct_cell(value, kind, st, suffix="%"):
    """按阈值着色的百分比单元格"""
    lv = _level(value, kind)
    txt = "-" if value is None else f"{value}{suffix}"
    style = ParagraphStyle(
        "pc", parent=st["cell"],
        textColor=_LEVEL_COLOR[lv],
        fontName=st["cellb"].fontName if lv != "ok" else st["cell"].fontName,
        alignment=TA_CENTER,
    )
    return Paragraph(escape(txt), style)


def _kv_table(pairs, st, col1=30 * mm):
    """四列键值表（键/值 交替）"""
    data, row = [], []
    for k, v in pairs:
        row += [P(k, st["cell"]), P(v, st["cell"])]
        if len(row) == 4:
            data.append(row)
            row = []
    if row:
        row += [P("", st["cell"]), P("", st["cell"])] * ((4 - len(row)) // 2)
        data.append(row)

    t = Table(data, colWidths=[col1, CONTENT_W / 2 - col1] * 2)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, C_GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, -1), C_HEAD_BG),
        ("BACKGROUND", (2, 0), (2, -1), C_HEAD_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _grid_table(header, rows, widths, st, aligns=None):
    """通用数据表格"""
    data = [[P(h, st["head"]) for h in header]] + rows
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, C_GRID),
        ("BACKGROUND", (0, 0), (-1, 0), C_HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#fafbfc")]),
    ]
    for idx, al in enumerate(aligns or []):
        style.append(("ALIGN", (idx, 0), (idx, -1), al))
    t.setStyle(TableStyle(style))
    return t


# ---------------------------------------------------------------- 页眉页脚
def _on_page(canvas, doc, meta, st):
    canvas.saveState()
    reg = st["small"].fontName

    canvas.setFont(reg, 7.5)
    canvas.setFillColor(C_MUTED)
    canvas.drawString(MARGIN, PAGE_H - MARGIN + 6 * mm,
                      meta.get("title", "虚拟机巡检报告"))
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 6 * mm,
                           "生成时间 " + meta.get("generated_at", ""))
    canvas.setStrokeColor(C_GRID)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, PAGE_H - MARGIN + 4 * mm,
                PAGE_W - MARGIN, PAGE_H - MARGIN + 4 * mm)

    canvas.line(MARGIN, MARGIN - 4 * mm, PAGE_W - MARGIN, MARGIN - 4 * mm)
    canvas.setFont(reg, 7.5)
    canvas.drawString(MARGIN, MARGIN - 8 * mm,
                      "VM Probe 自动生成 · 数据来源于实时 SSH 采集")
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 8 * mm,
                           "第 %d 页" % canvas.getPageNumber())
    canvas.restoreState()


# ---------------------------------------------------------------- 内容
def _cover(st, meta, stats, alerts):
    items = [
        Spacer(1, 34 * mm),
        Paragraph(escape(meta.get("title", "虚拟机巡检报告")), st["cover_title"]),
        Paragraph("VM Probe 自动巡检 · 实时 SSH 采集", st["cover_sub"]),
        Spacer(1, 14 * mm),
    ]

    info = [
        ("报告生成时间", meta.get("generated_at", "")),
        ("巡检主机总数", "%d 台" % stats["total"]),
        ("采集成功", "%d 台" % stats["ok"]),
        ("采集失败", "%d 台" % stats["failed"]),
        ("告警项", "%d 项" % len(alerts)),
    ]
    if meta.get("inspector"):
        info.insert(0, ("巡检人", meta["inspector"]))

    data = [[P(k, st["cell"]), P(v, st["cell"])] for k, v in info]
    t = Table(data, colWidths=[40 * mm, 74 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, C_GRID),
        ("BACKGROUND", (0, 0), (0, -1), C_HEAD_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    t.hAlign = "CENTER"
    items.append(t)

    if alerts:
        items += [Spacer(1, 12 * mm),
                  Paragraph("巡检发现以下告警", st["h2"])]
        rows = [[P(a["host"], st["cell"]), P(a["item"], st["cell"]),
                 P(a["detail"], st["cell"])] for a in alerts[:12]]
        items.append(_grid_table(["主机", "告警项", "详情"], rows,
                                 [34 * mm, 28 * mm, CONTENT_W - 62 * mm], st))
        if len(alerts) > 12:
            items.append(Spacer(1, 2 * mm))
            items.append(Paragraph("（仅显示前 12 项，完整情况见各主机详情页）",
                                   st["small"]))
    else:
        items += [Spacer(1, 12 * mm),
                  Paragraph("本次巡检未发现超过阈值的告警项。", st["note"])]

    if meta.get("note"):
        items += [Spacer(1, 8 * mm), Paragraph(escape(meta["note"]), st["note"])]
    return items


def _summary_table(results, st):
    header = ["#", "IP 地址", "主机名", "操作系统", "CPU", "内存",
              "磁盘(最高)", "负载(1m)", "状态"]
    widths = [8 * mm, 27 * mm, 25 * mm, 33 * mm, 13 * mm, 13 * mm,
              18 * mm, 16 * mm, 21 * mm]
    ok_style = ParagraphStyle("ok", parent=st["cell"], textColor=C_OK,
                              alignment=TA_CENTER)
    err_style = ParagraphStyle("err", parent=st["cell"], textColor=C_CRIT,
                               alignment=TA_CENTER)

    rows = []
    for i, r in enumerate(results, 1):
        if not r.get("ok"):
            rows.append([P(i, st["cell"]), P(r.get("host"), st["cell"]),
                         P("-", st["cell"]), P("-", st["cell"]), P("-", st["cell"]),
                         P("-", st["cell"]), P("-", st["cell"]), P("-", st["cell"]),
                         Paragraph("采集失败", err_style)])
            continue
        d = r["data"]
        b, c, m = d["basic"], d["cpu"], d["memory"]
        peak = max([p["usage_percent"] for p in d["disk"]["partitions"]] or [0])
        rows.append([
            P(i, st["cell"]),
            P("%s:%s" % (r.get("host"), r.get("port")), st["cell"]),
            P(b.get("hostname"), st["cell"]),
            P(b.get("os_name"), st["cell"]),
            _pct_cell(c.get("usage_percent"), "cpu", st),
            _pct_cell(m.get("usage_percent"), "mem", st),
            _pct_cell(peak, "disk", st),
            P(b.get("load_1"), st["cell"]),
            Paragraph("正常", ok_style),
        ])
    return _grid_table(header, rows, widths, st,
                       aligns=["CENTER", "LEFT", "LEFT", "LEFT", "CENTER",
                               "CENTER", "CENTER", "CENTER", "CENTER"])


def _host_section(r, idx, st):
    """单台主机详情"""
    out = []
    title = "%d. %s" % (idx, r.get("host"))
    if r.get("ok") and r["data"]["basic"].get("hostname"):
        title += "  ·  " + str(r["data"]["basic"]["hostname"])
    out.append(Paragraph(escape(title), st["h1"]))

    if not r.get("ok"):
        out.append(Paragraph("采集失败：" + escape(str(r.get("error"))),
                             ParagraphStyle("e", parent=st["note"],
                                            textColor=C_CRIT)))
        out.append(_kv_table([("IP 地址", r.get("host")),
                              ("端口", r.get("port")),
                              ("用户名", r.get("username"))], st))
        return out

    d = r["data"]
    b, c, m, dk = d["basic"], d["cpu"], d["memory"], d["disk"]
    net, procs = d.get("network") or {}, d.get("processes") or []

    # 基础信息
    out.append(Paragraph("基础信息", st["h2"]))
    swap = _fmt_gb(b["swap_total_gb"] * 1024) if b.get("swap_total_gb") else "未启用"
    out.append(_kv_table([
        ("主机名", b.get("hostname")),
        ("操作系统", b.get("os_name")),
        ("内核版本", b.get("kernel")),
        ("系统架构", b.get("arch")),
        ("CPU 型号", b.get("cpu_model")),
        ("CPU 核心", "%s 核" % b["cpu_cores"] if b.get("cpu_cores") else "-"),
        ("虚拟化", b.get("virt_type") or "未知"),
        ("内存总量", "%s GB" % b["mem_total_gb"] if b.get("mem_total_gb") else "-"),
        ("内存规格", b.get("mem_spec") or "-"),
        ("Swap 容量", swap),
        ("IP 地址", b.get("ip_addresses")),
        ("运行时间", b.get("uptime")),
        ("启动时间", b.get("boot_time")),
        ("平均负载", "%s / %s / %s" % (b.get("load_1"), b.get("load_5"),
                                       b.get("load_15"))),
        ("系统时间", b.get("current_time")),
        ("时区", b.get("timezone")),
        ("登录用户", b.get("logged_users") or "无"),
    ], st))

    # CPU
    out.append(Paragraph("CPU", st["h2"]))
    out.append(_grid_table(
        ["总使用率", "用户态", "内核态", "IO 等待", "逻辑核数"],
        [[_pct_cell(c.get("usage_percent"), "cpu", st),
          _pct_cell(c.get("user"), "cpu", st),
          _pct_cell(c.get("system"), "cpu", st),
          _pct_cell(c.get("iowait"), "cpu", st),
          P(b.get("cpu_cores"), st["cell"])]],
        [CONTENT_W * 0.2] * 5, st, aligns=["CENTER"] * 5))

    # 内存
    out.append(Paragraph("内存", st["h2"]))
    out.append(_grid_table(
        ["使用率", "总量", "已用", "可用", "缓存", "Swap 已用/总量"],
        [[_pct_cell(m.get("usage_percent"), "mem", st),
          P(_fmt_gb(m.get("total_mb")), st["cell"]),
          P(_fmt_gb(m.get("used_mb")), st["cell"]),
          P(_fmt_gb(m.get("available_mb")), st["cell"]),
          P(_fmt_gb(m.get("cached_mb")), st["cell"]),
          P("%s / %s MB" % (m.get("swap_used_mb"), m.get("swap_total_mb")),
            st["cell"])]],
        [CONTENT_W * x for x in (0.14, 0.17, 0.17, 0.17, 0.17, 0.18)],
        st, aligns=["CENTER"] * 6))

    # 磁盘
    out.append(Paragraph("磁盘分区", st["h2"]))
    if dk.get("partitions"):
        rows = [[P(p.get("device"), st["cell"]),
                 P(p.get("mount"), st["cell"]),
                 P("%s GB" % p.get("total_gb"), st["cell"]),
                 P("%s GB" % p.get("used_gb"), st["cell"]),
                 P("%s GB" % p.get("avail_gb"), st["cell"]),
                 _pct_cell(p.get("usage_percent"), "disk", st)]
                for p in dk["partitions"]]
        out.append(_grid_table(
            ["设备", "挂载点", "总容量", "已用", "可用", "使用率"], rows,
            [CONTENT_W * x for x in (0.24, 0.24, 0.13, 0.13, 0.13, 0.13)], st,
            aligns=["LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT", "CENTER"]))
    else:
        out.append(Paragraph("未获取到磁盘分区信息", st["small"]))

    if dk.get("io"):
        out.append(Spacer(1, 3 * mm))
        out.append(_grid_table(
            ["磁盘", "读取速率", "写入速率"],
            [[P(i.get("device"), st["cell"]),
              P("%s MB/s" % i.get("read_mb_s"), st["cell"]),
              P("%s MB/s" % i.get("write_mb_s"), st["cell"])] for i in dk["io"]],
            [CONTENT_W * x for x in (0.34, 0.33, 0.33)], st,
            aligns=["LEFT", "RIGHT", "RIGHT"]))

    # 网络
    out.append(Paragraph("网络接口", st["h2"]))
    ifaces = net.get("interfaces") or []
    if ifaces:
        out.append(_grid_table(
            ["网卡", "下行", "上行", "累计接收", "累计发送"],
            [[P(n.get("name"), st["cell"]),
              P("%s KB/s" % n.get("rx_kb_s"), st["cell"]),
              P("%s KB/s" % n.get("tx_kb_s"), st["cell"]),
              P("%s MB" % n.get("rx_total_mb"), st["cell"]),
              P("%s MB" % n.get("tx_total_mb"), st["cell"])] for n in ifaces],
            [CONTENT_W * x for x in (0.24, 0.19, 0.19, 0.19, 0.19)], st,
            aligns=["LEFT"] + ["RIGHT"] * 4))
    else:
        out.append(Paragraph("未获取到网络接口信息", st["small"]))

    # 进程
    out.append(Paragraph("CPU 占用 Top 10 进程", st["h2"]))
    if procs:
        out.append(_grid_table(
            ["PID", "用户", "CPU%", "内存%", "运行时长", "命令"],
            [[P(p.get("pid"), st["cell"]), P(p.get("user"), st["cell"]),
              P(p.get("cpu"), st["cell"]), P(p.get("mem"), st["cell"]),
              P(p.get("etime"), st["cell"]), P(p.get("command"), st["cell"])]
             for p in procs[:10]],
            [CONTENT_W * x for x in (0.08, 0.11, 0.08, 0.09, 0.14, 0.50)], st,
            aligns=["CENTER", "LEFT", "RIGHT", "RIGHT", "CENTER", "LEFT"]))
    else:
        out.append(Paragraph("未获取到进程信息", st["small"]))

    return out


# ---------------------------------------------------------------- 入口
def collect_alerts(results):
    """汇总告警项"""
    alerts = []
    for r in results or []:
        if not r.get("ok"):
            alerts.append({"host": r.get("host"), "item": "采集失败",
                           "detail": str(r.get("error"))[:60]})
            continue
        d = r["data"]
        c, m = d["cpu"], d["memory"]
        if (c.get("usage_percent") or 0) >= WARN["cpu"]:
            alerts.append({"host": r["host"], "item": "CPU 使用率高",
                           "detail": "当前 %s%%（阈值 %s%%）"
                                     % (c["usage_percent"], WARN["cpu"])})
        if (m.get("usage_percent") or 0) >= WARN["mem"]:
            alerts.append({"host": r["host"], "item": "内存使用率高",
                           "detail": "当前 %s%%（阈值 %s%%）"
                                     % (m["usage_percent"], WARN["mem"])})
        for p in d["disk"]["partitions"]:
            if p["usage_percent"] >= WARN["disk"]:
                alerts.append({"host": r["host"], "item": "磁盘空间不足",
                               "detail": "%s 已用 %s%%（剩余 %s GB）"
                                         % (p["mount"], p["usage_percent"],
                                            p["avail_gb"])})
        try:
            cores = int(d["basic"].get("cpu_cores") or 0)
            load1 = float(d["basic"].get("load_1") or 0)
            if cores and load1 >= cores:
                alerts.append({"host": r["host"], "item": "系统负载偏高",
                               "detail": "1 分钟负载 %s，CPU %d 核" % (load1, cores)})
        except (TypeError, ValueError):
            pass
    return alerts


def build_report(results, title="虚拟机巡检报告", inspector="", note=""):
    """生成巡检报告 PDF，返回 bytes"""
    st = _styles()
    results = list(results or [])
    ok_list = [r for r in results if r.get("ok")]

    meta = {
        "title": title,
        "inspector": inspector,
        "note": note,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    stats = {"total": len(results), "ok": len(ok_list),
             "failed": len(results) - len(ok_list)}
    alerts = collect_alerts(results)

    buf = BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN,
                          title=title, author="VM Probe")
    frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body")
    doc.addPageTemplates([PageTemplate(
        id="main", frames=[frame],
        onPage=lambda cv, dc: _on_page(cv, dc, meta, st))])

    story = _cover(st, meta, stats, alerts)
    story.append(PageBreak())
    story.append(Paragraph("一、巡检总览", st["h1"]))
    story.append(Spacer(1, 2 * mm))
    story.append(_summary_table(results, st))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "说明：CPU ≥ %d%% 、内存 ≥ %d%% 、磁盘 ≥ %d%% 标黄；分别达到 %d%% / %d%% / %d%% 标红。"
        "数据为采集瞬间的实时值。" %
        (WARN["cpu"], WARN["mem"], WARN["disk"],
         CRIT["cpu"], CRIT["mem"], CRIT["disk"]), st["small"]))

    for i, r in enumerate(results, 1):
        story.append(PageBreak())
        story.extend(_host_section(r, i, st))

    doc.build(story)
    return buf.getvalue()


def suggest_filename(results=None):
    """建议的文件名（避免中文乱码问题，同时给出可读版本）"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    n = len(results) if results else 0
    return "VM巡检报告_%s_%d台.pdf" % (ts, n)
