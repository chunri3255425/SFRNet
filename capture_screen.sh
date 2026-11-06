#!/usr/bin/env bash
# 定时截屏脚本（WSL2 → 抓取 Windows 桌面）
#
# WSLg 的 DISPLAY=:0 只是 Linux 虚拟显示，拍不到 Windows 桌面；
# 因此这里借 Windows 侧的 PowerShell + System.Drawing.CopyFromScreen 抓全屏，
# 存到 Windows 临时目录后搬进 WSL 输出目录（文件名带时间戳）。
#
# 用法:
#   ./capture_screen.sh                      # 每 10s 一张，存到 ./captures，Ctrl-C 停止
#   ./capture_screen.sh -i 5                 # 每 5s
#   ./capture_screen.sh -n 60                # 只拍 60 张后退出
#   ./capture_screen.sh -o ./shots           # 指定输出目录
#   ./capture_screen.sh --detect             # 每张拍完顺带跑一次 serve.py --detect
set -euo pipefail

INTERVAL=10
COUNT=0                 # 0 = 无限
OUTDIR="./captures"
DETECT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--interval) INTERVAL="$2"; shift 2 ;;
    -n|--count)    COUNT="$2";     shift 2 ;;
    -o|--out)      OUTDIR="$2";    shift 2 ;;
    --detect)      DETECT=1;       shift ;;
    -h|--help)     grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1 (用 -h 看帮助)" >&2; exit 2 ;;
  esac
done

# 定位 Windows PowerShell
PS=""
for c in \
  "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" \
  "$(command -v powershell.exe 2>/dev/null || true)"; do
  [[ -n "$c" && -x "$c" ]] && { PS="$c"; break; }
done
[[ -z "$PS" ]] && { echo "错误: 找不到 powershell.exe，本脚本仅适用于 WSL 抓 Windows 桌面。" >&2; exit 1; }

mkdir -p "$OUTDIR"
WIN_TEMP='C:\Users\Public\wsl_capture_tmp.png'   # 所有用户可写的固定中转路径
WIN_TEMP_UNIX="/mnt/c/Users/Public/wsl_capture_tmp.png"

# PowerShell 抓屏脚本（虚拟桌面全屏，覆盖多显示器外接矩形）
PS_SNAPSHOT='
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b   = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap($b.Width,$b.Height)
$g   = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.X,$b.Y,0,0,$bmp.Size)
$bmp.Save([string]"'"$WIN_TEMP"'", [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
'

echo "开始截屏: 每 ${INTERVAL}s 一张 -> ${OUTDIR}/  (Ctrl-C 停止; ${COUNT:+}${COUNT:-无限})"
[[ "$DETECT" == 1 ]] && echo "已开启 --detect: 每张将调用 python serve.py --detect"

n=0
while :; do
  ts=$(date +%Y%m%d_%H%M%S)
  dst="${OUTDIR}/shot_${ts}.png"
  if "$PS" -NoProfile -ExecutionPolicy Bypass -Command "$PS_SNAPSHOT" >/dev/null 2>&1 \
     && [[ -f "$WIN_TEMP_UNIX" ]]; then
    mv -f "$WIN_TEMP_UNIX" "$dst"
    n=$((n+1))
    echo "[$(date +%H:%M:%S)] #$n 已保存 $dst"
    if [[ "$DETECT" == 1 ]]; then
      python serve.py --detect "$dst" 2>&1 | grep -v Warning | tail -1 || true
    fi
  else
    echo "[$(date +%H:%M:%S)] 截屏失败 (PowerShell 返回非零或无输出文件)" >&2
  fi
  [[ "$COUNT" -gt 0 && "$n" -ge "$COUNT" ]] && { echo "已达 ${n} 张，退出。"; break; }
  sleep "$INTERVAL"
done
