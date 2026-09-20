# -*- coding: utf-8 -*-
"""
VM Probe — SSH 采集模块

从远程 Linux 主机采集基础信息与实时性能指标。
本模块只依赖 paramiko，不依赖任何 Web 框架，供 MCP 脚本工具调用。
"""
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import paramiko

DEFAULT_PORT = 22
CONNECT_TIMEOUT = 8
SAMPLE_INTERVAL = 1      # CPU / 网络采样间隔（秒）
MAX_HOSTS = 50           # 单次批量探测上限
MAX_WORKERS = 10         # 并发采集线程数


# ----------------------------------------------------------------------------
# SSH 基础
# ----------------------------------------------------------------------------
def connect(host, port, username, password):
    """建立 SSH 连接"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        password=password,
        timeout=CONNECT_TIMEOUT,
        allow_agent=False,
        look_for_keys=False,
        banner_timeout=15,
    )
    return client


def exec_out(client, cmd, timeout=20):
    """执行单条命令，返回 (stdout, stderr, exit_code)"""
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return out, err, code


def sh(client, cmd, timeout=20, default=""):
    """执行命令，失败返回默认值"""
    try:
        out, _, code = exec_out(client, cmd, timeout)
        return out.strip() if code == 0 and out.strip() else default
    except Exception:
        return default


# ----------------------------------------------------------------------------
# 采集：基础信息
# ----------------------------------------------------------------------------
def collect_basic(client):
    info = {}

    info["hostname"] = sh(client, "hostname")

    os_rel = sh(client, "cat /etc/os-release")
    pretty, os_id = "", ""
    for line in os_rel.splitlines():
        if line.startswith("PRETTY_NAME="):
            pretty = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("ID="):
            os_id = line.split("=", 1)[1].strip().strip('"')
    if not pretty:
        pretty = sh(client, "cat /etc/redhat-release 2>/dev/null || cat /etc/issue 2>/dev/null | head -1")
    info["os_name"] = pretty or "未知"
    info["os_id"] = os_id

    info["kernel"] = sh(client, "uname -r")
    info["arch"] = sh(client, "uname -m")
    info["platform"] = sh(client, "uname -s")

    # 运行时间
    uptime_raw = sh(client, "cat /proc/uptime")
    try:
        seconds = float(uptime_raw.split()[0])
        d, rem = divmod(int(seconds), 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        info["uptime"] = f"{d} 天 {h} 小时 {m} 分钟" if d else f"{h} 小时 {m} 分钟"
        info["uptime_seconds"] = int(seconds)
    except Exception:
        info["uptime"] = "未知"
        info["uptime_seconds"] = 0

    # 启动时间
    info["boot_time"] = sh(client, "uptime -s 2>/dev/null || who -b 2>/dev/null | awk '{print $3,$4}'")

    # CPU 型号与核心
    cpu_model = sh(client, "lscpu 2>/dev/null | grep -i 'model name' | head -1 | cut -d: -f2")
    if not cpu_model:
        cpu_model = sh(client, "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2")
    if not cpu_model:
        cpu_model = sh(client, "grep -m1 'Hardware' /proc/cpuinfo | cut -d: -f2")
    info["cpu_model"] = (cpu_model or "未知").strip()

    try:
        info["cpu_cores"] = int(sh(client, "nproc", default="0") or 0)
    except ValueError:
        info["cpu_cores"] = 0

    info["cpu_sockets"] = sh(client, "lscpu 2>/dev/null | grep -i '^socket(s)' | awk '{print $2}'")
    info["cpu_threads_per_core"] = sh(client, "lscpu 2>/dev/null | grep -i '^thread(s) per core' | awk '{print $4}'")

    # 虚拟化类型
    virt = sh(client, "systemd-detect-virt 2>/dev/null || dmidecode -s system-product-name 2>/dev/null | head -1")
    if not virt or virt.lower() in ("none", ""):
        virt = sh(client, "grep -m1 'hypervisor' /proc/cpuinfo >/dev/null && echo '虚拟机' || echo '物理机/未知'")
    info["virt_type"] = virt

    # IP 地址
    ips = sh(client, "hostname -I 2>/dev/null")
    if not ips:
        ips = sh(client, "ip -4 addr show scope global 2>/dev/null | grep inet | awk '{print $2}' | cut -d/ -f1 | tr '\\n' ' '")
    info["ip_addresses"] = [x for x in ips.split() if x]

    # 时区与时间
    info["timezone"] = sh(client, "cat /etc/timezone 2>/dev/null || timedatectl 2>/dev/null | grep 'Time zone' | awk '{print $3}'")
    local_time = sh(client, "date '+%Y-%m-%d %H:%M:%S'")
    info["local_time"] = local_time
    info["current_time"] = local_time          # 与 report.py 字段名对齐

    # 当前登录用户
    info["logged_users"] = sh(client, "who 2>/dev/null | head -5")

    # 平均负载
    info["loadavg"] = sh(client, "cat /proc/loadavg")

    # ------------------------------------------------------------------
    # 内存信息（容量 + 硬件规格）
    # ------------------------------------------------------------------
    mem_total_kb = swap_total_kb = 0
    for line in sh(client, "cat /proc/meminfo").splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        try:
            num = int(val.split()[0])          # 单位 kB
        except (ValueError, IndexError):
            continue
        if key.strip() == "MemTotal":
            mem_total_kb = num
        elif key.strip() == "SwapTotal":
            swap_total_kb = num
    info["mem_total_gb"] = round(mem_total_kb / 1048576, 2) if mem_total_kb else 0
    info["swap_total_gb"] = round(swap_total_kb / 1048576, 2) if swap_total_kb else 0

    # 物理内存条规格：dmidecode（需 root，虚拟机/云主机通常拿不到，拿不到就留空）
    info["mem_spec"] = ""
    info["mem_modules"] = 0      # 已插内存条数
    info["mem_slots"] = 0        # 主板插槽总数（含空槽）
    dmi = sh(client, "dmidecode -t memory 2>/dev/null", timeout=15)
    if dmi:
        sizes, types, speeds, total = [], [], [], 0
        for blk in dmi.split("Memory Device")[1:]:
            total += 1
            size = typ = spd = ""
            for line in blk.splitlines():
                s = line.strip()
                if s.startswith("Size:") and not size:
                    size = s.split(":", 1)[1].strip()
                elif s.startswith("Type:") and not typ:
                    typ = s.split(":", 1)[1].strip()
                elif s.startswith("Speed:") and not spd:
                    spd = s.split(":", 1)[1].strip()
            if not size or "No Module Installed" in size or size.lower().startswith("unknown"):
                continue
            sizes.append(size)
            if typ and typ.lower() not in ("unknown", "other"):
                types.append(typ)
            if spd and not spd.lower().startswith("unknown"):
                speeds.append(spd)

        info["mem_modules"] = len(sizes)
        info["mem_slots"] = total
        parts = []
        if types:
            parts.append(max(set(types), key=types.count))
        if speeds:
            parts.append(max(set(speeds), key=speeds.count))
        if parts:
            info["mem_spec"] = " ".join(parts)

    return info


# ----------------------------------------------------------------------------
# 采集：CPU 使用率（两次采样 /proc/stat 求差）
# ----------------------------------------------------------------------------
def collect_cpu(client):
    raw = sh(client, f"cat /proc/stat | head -1; sleep {SAMPLE_INTERVAL}; cat /proc/stat | head -1", timeout=25)
    lines = [l for l in raw.splitlines() if l.startswith("cpu ")]
    if len(lines) < 2:
        return {"usage_percent": 0.0, "user": 0.0, "system": 0.0,
                "iowait": 0.0, "nice": 0.0, "detail": {}}

    def parse(line):
        p = [float(x) for x in line.split()[1:]]
        # user nice system idle iowait irq softirq steal
        idle = p[3] + (p[4] if len(p) > 4 else 0)
        return p, sum(p), idle

    p1, t1, i1 = parse(lines[0])
    p2, t2, i2 = parse(lines[1])
    dt = t2 - t1
    di = i2 - i1
    usage = round((1 - di / dt) * 100, 1) if dt > 0 else 0.0
    usage = max(0.0, min(100.0, usage))

    # 拆解必须用两次采样的差值，否则算出来是开机以来的累计占比
    n = min(len(p1), len(p2))
    d = [p2[k] - p1[k] for k in range(n)]
    tot = sum(d) or 1.0
    user = round(d[0] / tot * 100, 1) if n > 0 else 0.0
    nice = round(d[1] / tot * 100, 1) if n > 1 else 0.0
    system = round(d[2] / tot * 100, 1) if n > 2 else 0.0
    iowait = round(d[4] / tot * 100, 1) if n > 4 else 0.0
    user, nice, system, iowait = (max(0.0, x) for x in (user, nice, system, iowait))

    return {
        "usage_percent": usage,
        "user": user, "system": system, "iowait": iowait, "nice": nice,
        "detail": {"user": user, "system": system, "iowait": iowait, "nice": nice},
    }


# ----------------------------------------------------------------------------
# 采集：内存
# ----------------------------------------------------------------------------
def collect_memory(client):
    raw = sh(client, "cat /proc/meminfo")
    m = {}
    for line in raw.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            try:
                m[k.strip()] = int(v.split()[0])   # kB
            except (ValueError, IndexError):
                pass

    def mb(key):
        return round(m.get(key, 0) / 1024, 1)

    total = mb("MemTotal")
    available = mb("MemAvailable") if "MemAvailable" in m else mb("MemFree") + mb("Buffers") + mb("Cached")
    free = mb("MemFree")
    buffers = mb("Buffers")
    cached = mb("Cached")
    used = round(total - available, 1)

    swap_total = mb("SwapTotal")
    swap_free = mb("SwapFree")
    swap_used = round(swap_total - swap_free, 1)

    return {
        "total_mb": total,
        "used_mb": used,
        "free_mb": free,
        "available_mb": available,
        "buffers_mb": buffers,
        "cached_mb": cached,
        "usage_percent": round(used / total * 100, 1) if total else 0,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_used,
        "swap_usage_percent": round(swap_used / swap_total * 100, 1) if swap_total else 0,
    }


# ----------------------------------------------------------------------------
# 采集：磁盘
# ----------------------------------------------------------------------------
def collect_disk(client):
    raw = sh(client, "df -P -x tmpfs -x devtmpfs -x squashfs 2>/dev/null | tail -n +2")

    SKIP_PREFIX = (
        "/dev", "/sys", "/proc", "/run", "/snap",
        "/mnt/wsl", "/mnt/wslg", "/usr/lib/wsl", "/init",
        "/var/lib/docker",
    )

    disks = {}
    for line in raw.splitlines():
        p = line.split()
        if len(p) < 6:
            continue
        mount = p[5]
        if any(mount == s or mount.startswith(s + "/") for s in SKIP_PREFIX):
            continue
        try:
            total_kb = int(p[1])
            used_kb = int(p[2])
            avail_kb = int(p[3])
            if total_kb == 0:
                continue
            percent = int(p[4].replace("%", ""))
            item = {
                "device": p[0],
                "total_gb": round(total_kb / 1048576, 2),
                "used_gb": round(used_kb / 1048576, 2),
                "avail_gb": round(avail_kb / 1048576, 2),
                "usage_percent": percent,
                "mount": mount,
            }
        except (ValueError, IndexError):
            continue

        # 同一设备多个挂载点只保留挂载点最短的一个
        dev = item["device"]
        if dev not in disks or len(mount) < len(disks[dev]["mount"]):
            disks[dev] = item

    partitions = sorted(disks.values(), key=lambda x: -x["usage_percent"])

    # 磁盘 IO
    io_raw = sh(client, f"cat /proc/diskstats; sleep {SAMPLE_INTERVAL}; cat /proc/diskstats", timeout=25)
    lines = io_raw.splitlines()
    io_result = []
    if lines:
        half = len(lines) // 2
        first, second = lines[:half], lines[half:]

        def parse_disks(ls):
            d = {}
            for l in ls:
                p = l.split()
                if len(p) >= 14:
                    name = p[2]
                    if name.startswith(("loop", "ram", "sr", "dm-", "fd")):
                        continue
                    if name[-1].isdigit():
                        continue
                    try:
                        d[name] = {"read_sectors": int(p[5]), "write_sectors": int(p[9])}
                    except (ValueError, IndexError):
                        pass
            return d

        d1, d2 = parse_disks(first), parse_disks(second)
        for name in d2:
            if name in d1:
                r = max(0, d2[name]["read_sectors"] - d1[name]["read_sectors"]) * 512 / SAMPLE_INTERVAL
                w = max(0, d2[name]["write_sectors"] - d1[name]["write_sectors"]) * 512 / SAMPLE_INTERVAL
                io_result.append({
                    "device": name,
                    "read_mb_s": round(r / 1048576, 2),
                    "write_mb_s": round(w / 1048576, 2),
                })
        io_result.sort(key=lambda x: -(x["read_mb_s"] + x["write_mb_s"]))
        io_result = io_result[:5]

    return {"partitions": partitions, "io": io_result}


# ----------------------------------------------------------------------------
# 采集：网络
# ----------------------------------------------------------------------------
def collect_network(client):
    raw = sh(client, f"cat /proc/net/dev; sleep {SAMPLE_INTERVAL}; cat /proc/net/dev", timeout=25)
    lines = raw.splitlines()
    if len(lines) < 4:
        return {"interfaces": []}

    def parse(ls):
        d = {}
        for l in ls:
            if ":" not in l:
                continue
            name, rest = l.split(":", 1)
            name = name.strip()
            p = rest.split()
            if len(p) >= 16:
                try:
                    d[name] = {
                        "rx_bytes": int(p[0]),
                        "tx_bytes": int(p[8]),
                        "rx_packets": int(p[1]),
                        "tx_packets": int(p[9]),
                    }
                except (ValueError, IndexError):
                    pass
        return d

    body = [l for l in lines if ":" in l]
    half = len(body) // 2
    d1, d2 = parse(body[:half]), parse(body[half:])

    result = []
    for name in d2:
        if name == "lo" or name not in d1:
            continue
        rx = max(0, d2[name]["rx_bytes"] - d1[name]["rx_bytes"]) / SAMPLE_INTERVAL
        tx = max(0, d2[name]["tx_bytes"] - d1[name]["tx_bytes"]) / SAMPLE_INTERVAL
        result.append({
            "name": name,
            "rx_mb_s": round(rx / 1048576, 2),
            "tx_mb_s": round(tx / 1048576, 2),
            "rx_kb_s": round(rx / 1024, 1),
            "tx_kb_s": round(tx / 1024, 1),
            "rx_total_mb": round(d2[name]["rx_bytes"] / 1048576, 1),
            "tx_total_mb": round(d2[name]["tx_bytes"] / 1048576, 1),
        })
    result.sort(key=lambda x: -(x["rx_mb_s"] + x["tx_mb_s"]))
    return {"interfaces": result}


# ----------------------------------------------------------------------------
# 采集：进程
# ----------------------------------------------------------------------------
def collect_processes(client):
    raw = sh(client, "ps -eo pid,user,pcpu,pmem,etime,comm --sort=-pcpu 2>/dev/null | head -11")
    procs = []
    for line in raw.splitlines()[1:]:
        p = line.split(None, 5)
        if len(p) < 6:
            continue
        try:
            procs.append({
                "pid": p[0],
                "user": p[1],
                "cpu": float(p[2]),
                "mem": float(p[3]),
                "etime": p[4],
                "command": p[5],
            })
        except (ValueError, IndexError):
            continue
    return procs


# ----------------------------------------------------------------------------
# 主采集入口
# ----------------------------------------------------------------------------
def collect_all(host, port, username, password):
    client = None
    try:
        client = connect(host, port, username, password)
        load_raw = sh(client, "cat /proc/loadavg")
        load = load_raw.split()[:3] if load_raw else ["0", "0", "0"]

        data = {
            "basic": collect_basic(client),
            "cpu": collect_cpu(client),
            "memory": collect_memory(client),
            "disk": collect_disk(client),
            "network": collect_network(client),
            "processes": collect_processes(client),
        }
        data["basic"]["load_1"] = load[0] if len(load) > 0 else "0"
        data["basic"]["load_5"] = load[1] if len(load) > 1 else "0"
        data["basic"]["load_15"] = load[2] if len(load) > 2 else "0"
        data["target"] = {"host": host, "port": port, "username": username}
        data["collected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return {"ok": True, "data": data}
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


# ----------------------------------------------------------------------------
# 参数解析
# ----------------------------------------------------------------------------
def split_host_port(text, default_port=DEFAULT_PORT):
    """解析 1.2.3.4、1.2.3.4:2222、[::1]:22 形式，返回 (host, port)"""
    s = (text or "").strip()
    if not s:
        return "", default_port
    if s.startswith("["):                      # IPv6: [fe80::1]:22
        m = re.match(r"^\[(.+)\]:(\d+)$", s)
        if m:
            return m.group(1), int(m.group(2))
        return s.strip("[]"), default_port
    if ":" in s:
        h, _, p = s.rpartition(":")
        if h and p.isdigit():
            return h, int(p)
    return s, default_port


def parse_one_token(token, default_user, default_password, default_port):
    """
    解析单个主机条目，返回 (host, port, user, password)。支持写法：
        host                      全部用默认值
        host:2222                 自定义端口
        user@host                 自定义用户
        user:password@host        自定义用户 + 密码
        user:password@host:2222   全自定义
        [2001:db8::1]:22          IPv6
    """
    s = (token or "").strip()
    user, password = default_user, default_password
    if "@" in s:
        cred, _, hostpart = s.rpartition("@")
        if cred:
            if ":" in cred:
                u, _, p = cred.partition(":")
                user, password = u, p
            else:
                user = cred
    else:
        hostpart = s
    host, port = split_host_port(hostpart, default_port)
    return host, port, user, password


def parse_target_list(hosts, default_user="", default_password="", default_port=DEFAULT_PORT):
    """
    把主机列表解析成 [(host, port, user, password), ...]，按 host:port:user 去重保序。
    hosts 元素可以是 "10.0.0.1"、"10.0.0.1:2222"、"root:pw@10.0.0.1:2222" 等。
    """
    if isinstance(hosts, str):
        hosts = re.split(r"[\s,;|]+", hosts.strip())
    out, seen = [], set()
    for token in hosts or []:
        h, p, u, pw = parse_one_token(str(token), default_user, default_password, default_port)
        if not h:
            continue
        key = (h, p, u)
        if key not in seen:
            seen.add(key)
            out.append((h, p, u, pw))
    return out


# ----------------------------------------------------------------------------
# 探测
# ----------------------------------------------------------------------------
def probe_one(host, port, username, password, precheck=True):
    """探测单台主机。永不抛异常，统一返回 {host, port, username, ok, data, error}"""
    item = {"host": host, "port": port, "username": username,
            "ok": False, "data": None, "error": ""}
    if precheck:
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
        except Exception as e:
            item["error"] = f"无法连接到 {host}:{port} — {type(e).__name__}: {e}"
            return item
    try:
        result = collect_all(host, port, username, password)
        if result.get("ok"):
            item["ok"] = True
            item["data"] = result["data"]
        else:
            item["error"] = result.get("error", "采集失败")
    except paramiko.AuthenticationException:
        item["error"] = "认证失败：用户名或密码错误"
    except paramiko.SSHException as e:
        item["error"] = f"SSH 协商失败：{e}（目标可能未开启 SSH 服务）"
    except socket.timeout:
        item["error"] = "连接超时"
    except Exception as e:
        item["error"] = f"{type(e).__name__}: {e}"
    return item


def probe_many(targets):
    """并发探测多台主机，返回 (results, elapsed_seconds)"""
    targets = list(targets)
    if not targets:
        return [], 0.0
    started = time.time()
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(targets))) as ex:
        results = list(ex.map(lambda t: probe_one(t[0], t[1], t[2], t[3]), targets))
    return results, round(time.time() - started, 1)
