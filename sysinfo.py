#!/usr/bin/env python3
"""Single-file system info tool: collects hardware/OS info (locally or over SSH)
and renders a self-contained HTML report. Linux (sh) and Windows (PowerShell)
collectors are embedded below; the target OS is auto-detected.

  ./sysinfo.py                              # this machine -> sysinfo.html
  ./sysinfo.py -p 2200 vela@10.42.0.123 -o r9.html      # Linux board over SSH
  ./sysinfo.py user@win-pc -o win.html                  # Windows PC over SSH (OpenSSH Server)
  ./sysinfo.py --text [host]                # raw text instead of HTML
  ./sysinfo.py --os windows|linux host      # skip auto-detection

On Windows:  python sysinfo.py            (local)   or   python sysinfo.py user@host
"""
import argparse, base64, html, re, shutil, subprocess, sys, datetime

SH = r'''#!/bin/sh
# sysinfo.sh - dependency-free hardware/OS summary (read-only).
# Reads /proc, /sys and uses optional tools only when present, so it works on
# PCs and on boards without lsusb/lspci (e.g. RISC-V devices).
#
#   ./sysinfo.sh                       # local machine
#   ssh -p 2200 vela@host 'sh -s' < sysinfo.sh   # remote, nothing copied

export LC_ALL=C
have() { command -v "$1" >/dev/null 2>&1; }
sec()  { printf '\n=== %s ===\n' "$1"; }
rd()   { [ -r "$1" ] && tr -d '\0' < "$1" 2>/dev/null; }
kv()   { printf '%-14s %s\n' "$1" "$2"; }

sec "System"
kv Hostname   "$(hostname 2>/dev/null || rd /proc/sys/kernel/hostname)"
[ -r /etc/os-release ] && kv OS "$(. /etc/os-release; echo "$PRETTY_NAME")"
kv Kernel     "$(uname -sr)  ($(uname -v))"
kv Arch       "$(uname -m)"
kv Uptime     "$(uptime 2>/dev/null | sed 's/^ *//')"
kv Virt       "$(have systemd-detect-virt && systemd-detect-virt 2>/dev/null || true)"
kv Cmdline    "$(rd /proc/cmdline)"

sec "Board / Firmware"
if [ -r /proc/device-tree/model ]; then
    kv Model      "$(rd /proc/device-tree/model)"
    kv Compatible "$(tr '\0' ' ' < /proc/device-tree/compatible 2>/dev/null)"
    [ -r /proc/device-tree/serial-number ] && kv Serial "$(rd /proc/device-tree/serial-number)"
fi
for f in sys_vendor product_name product_version board_vendor board_name bios_vendor bios_version bios_date; do
    v=$(rd /sys/class/dmi/id/$f); [ -n "$v" ] && kv "$f" "$v"
done
[ -d /sys/firmware/efi ] && kv Boot "UEFI" || kv Boot "non-UEFI / device-tree"

sec "CPU"
if have lscpu; then
    lscpu 2>/dev/null | grep -Ev '^(Vulnerability|Flags)'
else
    grep -m1 -E 'model name|uarch|cpu model' /proc/cpuinfo
    kv CPUs "$(grep -c '^processor' /proc/cpuinfo)"
fi
echo "-- cpuinfo (first core) --"
awk 'BEGIN{RS="";FS="\n"} NR==1{print}' /proc/cpuinfo
echo "-- cpufreq --"
for c in /sys/devices/system/cpu/cpu[0-9]*; do
    [ -r "$c/cpufreq/scaling_cur_freq" ] || continue
    printf '%s: cur=%s kHz  max=%s kHz  gov=%s\n' "${c##*/}" \
        "$(rd $c/cpufreq/scaling_cur_freq)" "$(rd $c/cpufreq/cpuinfo_max_freq)" "$(rd $c/cpufreq/scaling_governor)"
done
echo "-- caches (cpu0) --"
for i in /sys/devices/system/cpu/cpu0/cache/index*; do
    [ -d "$i" ] && printf 'L%s %s %s\n' "$(rd $i/level)" "$(rd $i/type)" "$(rd $i/size)"
done

sec "Memory"
grep -E '^(MemTotal|MemAvailable|SwapTotal|Hugepagesize)' /proc/meminfo

sec "Storage"
echo "-- block devices [table] --"
if have lsblk; then
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null
else
    for b in /sys/block/*; do
        printf '%s %s sectors  %s\n' "${b##*/}" "$(rd $b/size)" "$(rd $b/device/model)"
    done
fi
echo "-- filesystems [table] --"
df -h -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null

sec "Network"
echo "-- addresses [table] --"
if have ip; then
    ip -br addr 2>/dev/null; ip route 2>/dev/null
else
    for n in /sys/class/net/*; do printf '%s %s %s\n' "${n##*/}" "$(rd $n/operstate)" "$(rd $n/address)"; done
fi
echo "-- link --"
for n in /sys/class/net/*; do
    printf '%-10s state=%-8s mac=%s speed=%s\n' "${n##*/}" "$(rd $n/operstate)" "$(rd $n/address)" "$(rd $n/speed 2>/dev/null)"
done

sec "USB"
if have lsusb; then lsusb; else
    for d in /sys/bus/usb/devices/*; do
        [ -r "$d/idVendor" ] || continue
        printf '%s %s:%s %s %s\n' "${d##*/}" "$(rd $d/idVendor)" "$(rd $d/idProduct)" "$(rd $d/manufacturer)" "$(rd $d/product)"
    done
fi
echo "-- serial ports --"
ls /dev/ttyUSB* /dev/ttyACM* /dev/ttyS[0-9]* /dev/ttyAMA* 2>/dev/null | tr '\n' ' '; echo

sec "PCI"
if have lspci; then lspci; else
    for d in /sys/bus/pci/devices/*; do
        [ -r "$d/vendor" ] || continue
        printf '%s vendor=%s device=%s class=%s driver=%s\n' "${d##*/}" "$(rd $d/vendor)" "$(rd $d/device)" "$(rd $d/class)" \
            "$( [ -L $d/driver ] && basename "$(readlink $d/driver)")"
    done
fi
[ -d /sys/bus/pci/devices ] && [ -z "$(ls /sys/bus/pci/devices 2>/dev/null)" ] && echo "(no PCI devices)"

sec "GPU / Display / Accelerators"
ls -l /dev/dri 2>/dev/null | tail -n +2
for c in /sys/class/drm/card[0-9]; do
    [ -d "$c" ] && printf '%s driver=%s\n' "${c##*/}" "$( [ -L $c/device/driver ] && basename "$(readlink $c/device/driver)")"
done
have nvidia-smi && nvidia-smi -L 2>/dev/null
ls /dev/video* /dev/accel* /dev/npu* /dev/mobilint* 2>/dev/null | tr '\n' ' '; echo

sec "Thermal / Power"
found=0
for z in /sys/class/thermal/thermal_zone*; do
    [ -r "$z/temp" ] || continue; found=1
    printf '%s %s %s mC\n' "${z##*/}" "$(rd $z/type)" "$(rd $z/temp)"
done
for h in /sys/class/hwmon/hwmon*; do
    [ -d "$h" ] && { found=1; printf '%s %s\n' "${h##*/}" "$(rd $h/name)"; }
done
[ $found = 0 ] && echo "(no thermal sensors exposed)"
for b in /sys/class/power_supply/*; do
    [ -d "$b" ] && printf '%s %s %s%%\n' "${b##*/}" "$(rd $b/status)" "$(rd $b/capacity)"
done

sec "Load / Top processes"
rd /proc/loadavg
echo "-- top processes [table] --"
ps -eo pid,pcpu,pmem,etime,comm --sort=-pcpu 2>/dev/null | head -8

sec "Software"
for t in gcc g++ clang cmake make python3 git docker; do
    have $t && printf '%-8s %s\n' "$t" "$($t --version 2>&1 | head -1)"
done
have colcon && printf '%-8s %s\n' colcon "$(pip3 show colcon-core 2>/dev/null | sed -n 's/^Version: //p')"
[ -d /opt/ros ] && kv ROS "$(ls /opt/ros | tr '\n' ' ')"
kv Modules "$(wc -l < /proc/modules 2>/dev/null) loaded"
'''


PS = r'''$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
function Sec($n) { "`n=== $n ===" }
function Sub($n) { "-- $n --" }
function KV($k, $v) { if ($null -ne $v -and "$v".Trim() -ne '') { '{0,-14} {1}' -f $k, "$v".Trim() } }
function Tbl($o) { $s = ($o | Format-Table -AutoSize | Out-String -Width 220).Trim(); if ($s) { $s } else { '(none)' } }

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$bb = Get-CimInstance Win32_BaseBoard
$bi = Get-CimInstance Win32_BIOS
$cpus = @(Get-CimInstance Win32_Processor)
$c = $cpus[0]

Sec 'System'
KV 'Hostname' $env:COMPUTERNAME
KV 'OS' $os.Caption
KV 'Kernel' ("Windows NT {0} build {1}" -f $os.Version, $os.BuildNumber)
$arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
KV 'Arch' $arch
if ($os.LastBootUpTime) { $up = (Get-Date) - $os.LastBootUpTime; KV 'Uptime' ("up {0} days, {1}h {2}m" -f $up.Days, $up.Hours, $up.Minutes) }
KV 'Virt' $(if ($cs.HypervisorPresent) { 'hypervisor present' } else { 'none' })
KV 'Domain' $cs.Domain
KV 'PowerShell' $PSVersionTable.PSVersion.ToString()

Sec 'Board / Firmware'
KV 'Manufacturer' $cs.Manufacturer
KV 'Product' $cs.Model
KV 'Board' ("{0} {1}" -f $bb.Manufacturer, $bb.Product)
KV 'BIOS' ("{0} {1}" -f $bi.Manufacturer, $bi.SMBIOSBIOSVersion)
if ($bi.ReleaseDate) { KV 'BIOSDate' $bi.ReleaseDate.ToString('yyyy-MM-dd') }

Sec 'CPU'
KV 'Processor' $c.Name
KV 'Vendor' $c.Manufacturer
KV 'Sockets' $cpus.Count
KV 'Cores' ($cpus | Measure-Object -Property NumberOfCores -Sum).Sum
KV 'Threads' ($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
KV 'MaxClock' ("{0} MHz" -f $c.MaxClockSpeed)
KV 'CurClock' ("{0} MHz" -f $c.CurrentClockSpeed)
KV 'L2Cache' ("{0} KB" -f $c.L2CacheSize)
KV 'L3Cache' ("{0} KB" -f $c.L3CacheSize)
KV 'VTx' $c.VirtualizationFirmwareEnabled
KV 'Load' ("{0}%" -f $c.LoadPercentage)

Sec 'Memory'
'MemTotal:       {0} kB' -f [int64]$os.TotalVisibleMemorySize
'MemAvailable:   {0} kB' -f [int64]$os.FreePhysicalMemory
$pf = (Get-CimInstance Win32_PageFileUsage | Measure-Object -Property AllocatedBaseSize -Sum).Sum
'SwapTotal:      {0} kB' -f ([int64]$pf * 1024)
Sub 'modules [table]'
Tbl (Get-CimInstance Win32_PhysicalMemory | Select-Object @{n='Slot';e={$_.DeviceLocator}}, Manufacturer, PartNumber, @{n='GB';e={[math]::Round($_.Capacity/1GB,1)}}, @{n='MHz';e={$_.Speed}})

Sec 'Storage'
Sub 'physical disks [table]'
Tbl (Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, HealthStatus)
Sub 'volumes [table]'
Tbl (Get-Volume | Where-Object { $_.DriveLetter } | Select-Object DriveLetter, FileSystemLabel, FileSystem, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.SizeRemaining/1GB,1)}})

Sec 'Network'
Sub 'adapters [table]'
Tbl (Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, MacAddress, LinkSpeed)
Sub 'addresses [table]'
Tbl (Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress, PrefixLength)
Sub 'default route [table]'
Tbl (Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object InterfaceAlias, NextHop, RouteMetric)

Sec 'USB'
Sub 'devices [table]'
Tbl (Get-PnpDevice -PresentOnly -Class USB | Select-Object Status, FriendlyName, @{n='VID:PID';e={ if ($_.InstanceId -match 'VID_([0-9A-F]{4}).PID_([0-9A-F]{4})') { $Matches[1] + ':' + $Matches[2] } }})
Sub 'serial ports'
([System.IO.Ports.SerialPort]::GetPortNames() -join ' ')

Sec 'PCI'
Sub 'devices [table]'
Tbl (Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'PCI\*' } | Select-Object Class, FriendlyName | Sort-Object Class, FriendlyName)

Sec 'GPU / Display / Accelerators'
Sub 'adapters [table]'
Tbl (Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, @{n='VRAM_GB';e={[math]::Round($_.AdapterRAM/1GB,1)}}, VideoModeDescription)

Sec 'Thermal / Power'
$tz = @(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature)
if ($tz.Count -gt 0) { foreach ($t in $tz) { '{0}  {1:N1} C' -f $t.InstanceName, (($t.CurrentTemperature / 10) - 273.15) } } else { '(no thermal sensors exposed, or admin rights needed)' }
foreach ($b in @(Get-CimInstance Win32_Battery)) { 'battery  {0}%  status={1}' -f $b.EstimatedChargeRemaining, $b.BatteryStatus }

Sec 'Load / Top processes'
Sub 'top processes [table]'
Tbl (Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Id, ProcessName, @{n='CPU_s';e={[math]::Round($_.CPU,0)}}, @{n='MemMB';e={[math]::Round($_.WorkingSet64/1MB,0)}})

Sec 'Software'
foreach ($t in 'gcc','g++','clang','cmake','make','python','py','git','docker','node','java','cargo','winget') {
    $cmd = Get-Command $t -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    if ($cmd.Source -like '*\WindowsApps\python*') { continue }
    KV $t (& $cmd.Source --version 2>&1 | Select-Object -First 1)
}
'''


def _ssh(host, port, remote):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
    if port:
        cmd += ["-p", str(port)]
    return cmd + [host, remote]


PS_ARGS = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"]


def ps_payload():
    # one line on stdin: avoids multi-line parsing quirks and cmd.exe argv limits
    b64 = base64.b64encode(PS.encode("utf-8")).decode()
    return ("iex ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')))\n" % b64).encode()


def detect_remote_os(host, port):
    p = subprocess.run(_ssh(host, port, "uname -s"), input=b"", capture_output=True, timeout=30)
    if p.returncode == 255:
        sys.exit("ssh failed: " + p.stderr.decode(errors="replace").strip())
    uname = p.stdout.decode(errors="replace").strip().lower()
    # no uname (cmd.exe / PowerShell default shell) or an MSYS/Cygwin shell on Windows
    if p.returncode != 0 or uname.startswith(("mingw", "msys", "cygwin")):
        return "windows"
    return "linux"


def collect(host, port, target="auto"):
    if host:
        if target == "auto":
            target = detect_remote_os(host, port)
        cmd = _ssh(host, port, "powershell " + " ".join(PS_ARGS) if target == "windows" else "sh -s")
    else:
        if target == "auto":
            target = "windows" if sys.platform == "win32" else "linux"
        if target == "windows":
            exe = shutil.which("powershell") or shutil.which("pwsh")
            if not exe:
                sys.exit("PowerShell not found (need powershell.exe or pwsh)")
            cmd = [exe] + PS_ARGS
        else:
            if not shutil.which("sh"):
                sys.exit("local mode needs a POSIX sh; pass a [user@]host to inspect a remote machine")
            cmd = ["sh", "-s"]
    data = ps_payload() if target == "windows" else SH.encode()
    p = subprocess.run(cmd, input=data, capture_output=True, timeout=120)
    out = p.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    if not out.strip():
        sys.exit("collect failed (%s): %s" % (target, p.stderr.decode(errors="replace").strip()))
    return out


class Raw(list):
    """lines of a sub-block whose title ended in "[table]": always shown verbatim"""


def parse(text):
    """-> [(section, [(subtitle|None, [lines])])]"""
    secs, cur, sub = [], None, None
    for ln in text.splitlines():
        m = re.match(r"^=== (.+) ===$", ln)
        if m:
            cur = (m.group(1), [])
            secs.append(cur)
            sub = None
            continue
        if cur is None:
            continue
        m = re.match(r"^-- (.+) --$", ln)
        if m:
            title = m.group(1)
            raw = title.endswith(" [table]")
            sub = (title[:-len(" [table]")] if raw else title, Raw() if raw else [])
            cur[1].append(sub)
            continue
        if sub is None:
            sub = (None, [])
            cur[1].append(sub)
        sub[1].append(ln)
    return secs


KV_COLON = re.compile(r"^\s*([^:\t]+?)\s*:\s*(.*)$")
KV_SPACE = re.compile(r"^(\S+)\s{2,}(.*)$")


def as_kv(lines):
    if isinstance(lines, Raw):
        return None
    lines = [l for l in lines if l.strip()]
    if len(lines) < 2:
        return None
    for rx in (KV_SPACE, KV_COLON):
        rows = [rx.match(l) for l in lines]
        if all(rows):
            return [(r.group(1), r.group(2)) for r in rows]
    return None


def esc(s):
    return html.escape(s, quote=True)


def render_block(lines):
    raw = isinstance(lines, Raw)
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return '<p class="empty">(none)</p>'
    kv = None if raw else as_kv(lines)
    if kv:
        rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in kv)
        return f'<table class="kv">{rows}</table>'
    return f"<pre>{esc(chr(10).join(lines))}</pre>"


def facts(text, secs):
    d = {}
    for name, subs in secs:
        for _, lines in subs:
            for kv in (as_kv(lines) or []):
                d.setdefault(kv[0], kv[1])
    out = []
    host = d.get("Hostname", "?")
    board = d.get("Model") or d.get("product_name") or d.get("Product") or "-"
    cpu = d.get("Model name") or d.get("Processor") or d.get("uarch") or "-"
    isa = d.get("isa")
    ncpu = d.get("CPU(s)") or d.get("Threads") or str(len(re.findall(r"^processor", text, re.M)) or "-")
    mem = "-"
    m = re.search(r"MemTotal:\s+(\d+) kB", text)
    if m:
        mem = f"{int(m.group(1)) / 1048576:.1f} GiB"
    out += [("Host", host), ("Board", board), ("Arch", d.get("Arch", "-")),
            ("CPU", cpu), ("Cores", ncpu), ("RAM", mem),
            ("OS", d.get("OS", "-")), ("Kernel", d.get("Kernel", "-").split("  (")[0])]
    if isa:
        out.append(("ISA", isa))
    m = re.search(r"load average: ([\d.]+), ([\d.]+), ([\d.]+)", text)
    if m:
        out.append(("Load 1/5/15", f"{m.group(1)} / {m.group(2)} / {m.group(3)}"))
    if "Load" in d:  # Windows: CPU load percentage
        out.append(("CPU load", d["Load"]))
    m = re.search(r"up (.+?),\s+\d+ users?", text)
    if m:
        out.append(("Uptime", m.group(1)))
    elif d.get("Uptime"):
        out.append(("Uptime", re.sub(r"^up ", "", d["Uptime"])))
    return out


CSS = """
:root{--bg:#f5f6f8;--card:#fff;--fg:#1b1f24;--mut:#667085;--line:#e4e7ec;--acc:#4f46e5;--code:#f2f4f7}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ec;--mut:#98a2b3;--line:#262b36;--acc:#8b93ff;--code:#0f1218}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{margin:0;font-size:26px}
.sub{color:var(--mut);margin:4px 0 20px;font-size:13px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));margin-bottom:22px}
.fact{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:0}
.fact b{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mut);font-weight:600}
.fact span{display:block;font-weight:600;overflow-wrap:anywhere}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}
nav a{font-size:13px;color:var(--acc);text-decoration:none;border:1px solid var(--line);background:var(--card);padding:3px 10px;border-radius:99px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:14px;padding:4px 18px 14px}
section h2{font-size:16px;margin:14px 0 8px;color:var(--acc)}
h3{font-size:12px;margin:14px 0 6px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
table.kv{border-collapse:collapse;width:100%}
table.kv th{text-align:left;font-weight:500;color:var(--mut);padding:4px 14px 4px 0;width:28%;vertical-align:top;white-space:nowrap}
table.kv td{padding:4px 0;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}
table.kv tr+tr{border-top:1px solid var(--line)}
pre{background:var(--code);border-radius:8px;padding:10px 12px;margin:0;overflow:auto;font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.empty{color:var(--mut);margin:4px 0;font-style:italic}
details{margin-top:6px}summary{cursor:pointer;color:var(--mut);font-size:13px}
@media (max-width:600px){table.kv th{white-space:normal;width:36%}}
"""


def build(text, title, source):
    secs = parse(text)
    fx = "".join(f'<div class="fact"><b>{esc(k)}</b><span>{esc(v)}</span></div>' for k, v in facts(text, secs))
    nav = "".join(f'<a href="#s{i}">{esc(n)}</a>' for i, (n, _) in enumerate(secs))
    body = []
    for i, (name, subs) in enumerate(secs):
        parts = []
        for st, lines in subs:
            blk = render_block(lines)
            if st:
                parts.append(f"<h3>{esc(st)}</h3>{blk}")
            else:
                parts.append(blk)
        body.append(f'<section id="s{i}"><h2>{esc(name)}</h2>{"".join(parts)}</section>')
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<h1>{esc(title)}</h1>
<p class="sub">source: {esc(source)} · generated {now}</p>
<div class="grid">{fx}</div>
<nav>{nav}</nav>
{"".join(body)}
<details><summary>raw output</summary><pre>{esc(text)}</pre></details>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", nargs="?", help="[user@]host for SSH; omit for local")
    ap.add_argument("-p", "--port", type=int)
    ap.add_argument("-o", "--out", default="sysinfo.html")
    ap.add_argument("--text", action="store_true", help="print raw text, no HTML")
    ap.add_argument("--os", dest="target", choices=["auto", "linux", "windows"], default="auto",
                    help="target OS (default: auto-detect)")
    a = ap.parse_args()
    text = collect(a.host, a.port, a.target)
    if a.text:
        print(text, end="")
        return
    secs = parse(text)
    name = next((l.split(None, 1)[1].strip() for l in text.splitlines() if l.startswith("Hostname")), "system")
    src = (f"ssh {a.host}" + (f":{a.port}" if a.port else "")) if a.host else "local"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(build(text, f"{name} — System Info", src))
    print(f"wrote {a.out} ({len(secs)} sections)")


if __name__ == "__main__":
    main()
