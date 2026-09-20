# -*- coding: utf-8 -*-
"""
VM Probe MCP Server — 虚拟机巡检探针

通过 SSH 采集远程 Linux 主机的基础信息与实时性能指标，并支持生成 PDF 巡检报告。
以 MCP（stdio）方式对外暴露以下工具：
    probe_vm                   探测单台主机（详细）
    probe_vms                  批量探测多台主机（摘要）
    generate_inspection_report 采集并生成 PDF 巡检报告

注意：stdio 模式下 stdout 是 MCP 协议通道，任何日志都必须写到 stderr。
"""
import os
import sys
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from typing import List, Optional

# FastMCP 在 mcp 2.x 中已改名为 MCPServer，这里做兼容导入，两个大版本都能跑
try:                                              # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _MCPServer
except ImportError:                               # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _MCPServer

import collector
import report as report_mod

mcp = _MCPServer("vm-probe")


# ----------------------------------------------------------------------------
# 格式化辅助
# ----------------------------------------------------------------------------
def _size(mb):
    """MB → 人类可读"""
    try:
        mb = float(mb or 0)
    except (TypeError, ValueError):
        return "-"
    if mb <= 0:
        return "-"
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _pct(v):
    return "-" if v is None else f"{v}%"


def _kv_rows(pairs):
    return "\n".join(f"| {k} | {v if v not in (None, '') else '-'} |" for k, v in pairs)


def _host_detail(idx, r):
    """单台主机详情（Markdown）"""
    if not r.get("ok"):
        return (f"### {idx}. {r.get('host')}:{r.get('port')} — ❌ 采集失败\n\n"
                f"- 错误信息：{r.get('error')}\n")

    d = r["data"]
    b, c, m, dk = d["basic"], d["cpu"], d["memory"], d["disk"]
    ifaces = (d.get("network") or {}).get("interfaces") or []
    procs = d.get("processes") or []

    title = f"### {idx}. {r.get('host')}:{r.get('port')}"
    if b.get("hostname"):
        title += f"  ·  {b['hostname']}"
    out = [title, "", "**基础信息**", "",
           "| 项 | 值 |", "|---|---|",
           _kv_rows([
               ("主机名", b.get("hostname")),
               ("操作系统", b.get("os_name")),
               ("内核版本", b.get("kernel")),
               ("系统架构", b.get("arch")),
               ("CPU 型号", b.get("cpu_model")),
               ("CPU 核心", f"{b.get('cpu_cores')} 核" if b.get("cpu_cores") else "-"),
               ("虚拟化", b.get("virt_type")),
               ("内存总量", f"{b.get('mem_total_gb')} GB"),
               ("内存规格", b.get("mem_spec") or "未获取到（需 root / 物理机）"),
               ("Swap 容量", f"{b.get('swap_total_gb')} GB" if b.get("swap_total_gb") else "未启用"),
               ("IP 地址", ", ".join(b.get("ip_addresses") or []) or "-"),
               ("运行时间", b.get("uptime")),
               ("启动时间", b.get("boot_time")),
               ("平均负载", f"{b.get('load_1')} / {b.get('load_5')} / {b.get('load_15')}"),
               ("系统时间", b.get("current_time")),
               ("时区", b.get("timezone")),
           ]), ""]

    # CPU
    out += ["**CPU**", "",
            "| 总使用率 | 用户态 | 内核态 | IO 等待 | 逻辑核数 |",
            "|---|---|---|---|---|",
            f"| {_pct(c.get('usage_percent'))} | {_pct(c.get('user'))} | "
            f"{_pct(c.get('system'))} | {_pct(c.get('iowait'))} | {b.get('cpu_cores')} |", ""]

    # 内存
    out += ["**内存**", "",
            "| 使用率 | 总量 | 已用 | 可用 | 缓存 | Swap 已用/总量 |",
            "|---|---|---|---|---|---|",
            f"| {_pct(m.get('usage_percent'))} | {_size(m.get('total_mb'))} | "
            f"{_size(m.get('used_mb'))} | {_size(m.get('available_mb'))} | "
            f"{_size(m.get('cached_mb'))} | {m.get('swap_used_mb')} / {m.get('swap_total_mb')} MB |", ""]

    # 磁盘
    out.append("**磁盘分区**")
    out.append("")
    if dk.get("partitions"):
        out += ["| 设备 | 挂载点 | 总容量 | 已用 | 可用 | 使用率 |",
                "|---|---|---|---|---|---|"]
        for p in dk["partitions"]:
            out.append(f"| {p.get('device')} | {p.get('mount')} | {p.get('total_gb')} GB | "
                       f"{p.get('used_gb')} GB | {p.get('avail_gb')} GB | {_pct(p.get('usage_percent'))} |")
    else:
        out.append("未获取到磁盘分区信息")
    out.append("")

    if dk.get("io"):
        out += ["**磁盘 IO**", "", "| 磁盘 | 读取 | 写入 |", "|---|---|---|"]
        for i in dk["io"]:
            out.append(f"| {i.get('device')} | {i.get('read_mb_s')} MB/s | {i.get('write_mb_s')} MB/s |")
        out.append("")

    # 网络
    out.append("**网络接口**")
    out.append("")
    if ifaces:
        out += ["| 网卡 | 下行 | 上行 | 累计接收 | 累计发送 |", "|---|---|---|---|---|"]
        for n in ifaces:
            out.append(f"| {n.get('name')} | {n.get('rx_kb_s')} KB/s | {n.get('tx_kb_s')} KB/s | "
                       f"{n.get('rx_total_mb')} MB | {n.get('tx_total_mb')} MB |")
    else:
        out.append("未获取到网络接口信息")
    out.append("")

    # 进程
    if procs:
        out += ["**CPU 占用 Top 5 进程**", "", "| PID | 用户 | CPU% | 内存% | 运行时长 | 命令 |",
                "|---|---|---|---|---|---|"]
        for p in procs[:5]:
            out.append(f"| {p.get('pid')} | {p.get('user')} | {p.get('cpu')} | {p.get('mem')} | "
                       f"{p.get('etime')} | {p.get('command')} |")

    return "\n".join(out)


def _host_brief(idx, r):
    """单台主机摘要（一行块）"""
    if not r.get("ok"):
        return f"{idx}. `{r.get('host')}:{r.get('port')}` ❌ 采集失败 — {r.get('error')}"
    d = r["data"]
    b, c, m, dk = d["basic"], d["cpu"], d["memory"], d["disk"]
    peak = max([p["usage_percent"] for p in dk["partitions"]] or [0])
    return (f"{idx}. `{r.get('host')}:{r.get('port')}` **{b.get('hostname') or '-'}** — "
            f"{b.get('os_name')}｜CPU {_pct(c.get('usage_percent'))}｜"
            f"内存 {_pct(m.get('usage_percent'))}｜磁盘峰值 {_pct(peak)}｜"
            f"负载 {b.get('load_1')}")


def _resolve(hosts, username, password, port):
    """解析主机列表并校验"""
    targets = collector.parse_target_list(hosts, username, password, port)
    if not targets:
        return None, "未解析到有效主机。请在 hosts 中填写 IP，或形如 10.0.0.1:2222、root:密码@10.0.0.1 的条目。"
    missing = [f"{t[0]}:{t[1]}" for t in targets if not t[2]]
    if missing:
        return None, (f"以下主机未指定用户名：{', '.join(missing[:5])}"
                      f"{' 等' if len(missing) > 5 else ''}。请填写 username，"
                      f"或在条目里写成 user:password@host 形式。")
    if len(targets) > collector.MAX_HOSTS:
        return None, f"一次最多探测 {collector.MAX_HOSTS} 台，当前 {len(targets)} 台。"
    return targets, None


# ----------------------------------------------------------------------------
# 工具 1：单台探测
# ----------------------------------------------------------------------------
@mcp.tool()
def probe_vm(host: str, username: str = "", password: str = "", port: int = 22) -> str:
    """
    探测单台 Linux 主机的详细状态：基础信息（主机名/操作系统/内核/架构/CPU 型号与核数/内存容量与规格/
    Swap/虚拟化类型/IP/运行时间/负载）与实时性能指标（CPU 使用率及用户态·内核态·IO等待拆解、
    内存与 Swap 使用率、各磁盘分区使用率与读写速率、各网卡上下行速率、CPU 占用 Top5 进程）。

    需要目标主机开启 SSH 服务。数据为采集瞬间的实时值，单次采集约 3-5 秒。

    Args:
        host: 目标主机 IP 或域名，也支持 "10.0.0.1:2222" 或 "[2001:db8::1]:22" 形式
        username: SSH 用户名；若 host 写成 "user:password@host" 形式可省略
        password: SSH 密码
        port: SSH 端口，默认 22

    Returns:
        Markdown 格式的探测结果（基础信息表 + CPU + 内存 + 磁盘 + 网络 + Top5 进程）
    """
    targets, err = _resolve([host], username, password, port)
    if err:
        return f"❌ 参数有误：{err}"

    _h, _p, _u, _pw = targets[0]
    results, elapsed = collector.probe_many(targets)
    head = f"## 主机探测结果（耗时 {elapsed}s）\n"
    return head + "\n" + _host_detail(1, results[0])


# ----------------------------------------------------------------------------
# 工具 2：批量探测
# ----------------------------------------------------------------------------
@mcp.tool()
def probe_vms(hosts: List[str], username: str = "", password: str = "", port: int = 22) -> str:
    """
    批量探测多台 Linux 主机（并发执行，最多 50 台）。返回每台主机的一行摘要与整体统计；
    需要某台的完整详情时，再用 probe_vm 单独查询该主机。

    Args:
        hosts: 主机列表。每项可以是 "10.0.0.1"、"10.0.0.1:2222"、"user:password@10.0.0.1:2222"，
               也可以用 "10.0.0.1,10.0.0.2" 这样的字符串一次性传入多台
        username: 默认 SSH 用户名（条目内自带凭据时以条目为准）
        password: 默认 SSH 密码
        port: 默认 SSH 端口，默认 22

    Returns:
        Markdown 格式的批量探测摘要（总数/成功/失败/耗时 + 各主机一行摘要）
    """
    targets, err = _resolve(hosts, username, password, port)
    if err:
        return f"❌ 参数有误：{err}"

    results, elapsed = collector.probe_many(targets)
    ok_count = sum(1 for r in results if r.get("ok"))

    lines = [
        "## 批量探测结果",
        "",
        f"- 主机总数：**{len(results)}** 台",
        f"- 采集成功：**{ok_count}** 台",
        f"- 采集失败：**{len(results) - ok_count}** 台",
        f"- 总耗时：**{elapsed}** 秒",
        "",
        "| # | 主机 | 主机名 | 系统 | CPU | 内存 | 磁盘峰值 | 负载 | 状态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        if not r.get("ok"):
            lines.append(f"| {i} | {r.get('host')}:{r.get('port')} | - | - | - | - | - | - | ❌ 失败 |")
            continue
        d = r["data"]
        b, c, m, dk = d["basic"], d["cpu"], d["memory"], d["disk"]
        peak = max([p["usage_percent"] for p in dk["partitions"]] or [0])
        lines.append(
            f"| {i} | {r.get('host')}:{r.get('port')} | {b.get('hostname') or '-'} | {b.get('os_name') or '-'} | "
            f"{_pct(c.get('usage_percent'))} | {_pct(m.get('usage_percent'))} | {_pct(peak)} | "
            f"{b.get('load_1')} | ✅ 正常 |")

    failed = [(i, r) for i, r in enumerate(results, 1) if not r.get("ok")]
    if failed:
        lines += ["", "**失败明细**", ""]
        for i, r in failed:
            lines.append(f"- {i}. `{r.get('host')}:{r.get('port')}` — {r.get('error')}")

    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 工具 3：生成 PDF 巡检报告
# ----------------------------------------------------------------------------
@mcp.tool()
def generate_inspection_report(
    hosts: List[str],
    username: str = "",
    password: str = "",
    port: int = 22,
    title: str = "虚拟机巡检报告",
    inspector: str = "",
    note: str = "",
    output_dir: str = "",
) -> str:
    """
    对一批 Linux 主机执行实时 SSH 采集，并生成带封面的 PDF 格式巡检报告。
    报告包含：封面与告警汇总、巡检总览表（每台一行的 CPU/内存/磁盘/负载）、
    以及每台主机的详情页（基础信息、CPU、内存、磁盘分区与 IO、网络、Top10 进程）。
    中文已内嵌字体，无需目标机安装任何字体。

    告警阈值：CPU/内存/磁盘 ≥ 80%/85%/85% 标黄，≥ 90%/95%/95% 标红；1 分钟负载 ≥ 核数时提示负载偏高。

    Args:
        hosts: 主机列表，格式同 probe_vms
        username: 默认 SSH 用户名
        password: 默认 SSH 密码
        port: 默认 SSH 端口，默认 22
        title: 报告标题，默认「虚拟机巡检报告」
        inspector: 巡检人，显示在封面
        note: 备注说明，显示在封面告警区下方
        output_dir: PDF 输出目录，默认为当前工作目录

    Returns:
        PDF 文件的绝对路径与采集统计信息
    """
    targets, err = _resolve(hosts, username, password, port)
    if err:
        return f"❌ 参数有误：{err}"

    results, elapsed = collector.probe_many(targets)
    ok_count = sum(1 for r in results if r.get("ok"))

    try:
        pdf = report_mod.build_report(results, title=title, inspector=inspector, note=note)
    except Exception as e:
        return f"❌ PDF 生成失败：{type(e).__name__}: {e}"

    out_dir = (output_dir or "").strip() or os.getcwd()
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        out_dir = os.getcwd()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"VM巡检报告_{ts}_{len(results)}台.pdf"
    path = os.path.join(out_dir, fname)
    try:
        with open(path, "wb") as f:
            f.write(pdf)
    except Exception as e:
        return f"❌ PDF 写入失败（{out_dir}）：{type(e).__name__}: {e}"

    alerts = report_mod.collect_alerts(results)
    lines = [
        "## ✅ 巡检报告已生成",
        "",
        f"- 文件路径：`{path}`",
        f"- 文件大小：{len(pdf) / 1024:.1f} KB",
        f"- 巡检主机：**{len(results)}** 台（成功 {ok_count} 台，失败 {len(results) - ok_count} 台）",
        f"- 采集耗时：{elapsed} 秒",
        f"- 告警项：**{len(alerts)}** 项",
    ]
    if alerts:
        lines += ["", "| 主机 | 告警项 | 详情 |", "|---|---|---|"]
        for a in alerts[:10]:
            lines.append(f"| {a['host']} | {a['item']} | {a['detail']} |")
        if len(alerts) > 10:
            lines.append(f"\n（仅显示前 10 项，完整内容见 PDF）")
    else:
        lines.append("")
        lines.append("本次巡检未发现超过阈值的告警项。")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
