#!/bin/bash
# 验证厂商 README 那条硬伤：「每隔一小时重启【因为没激活系统】」。
# 做法：每 30 秒读一次板内 /proc/uptime，记录 主机时刻 + 板内秒数。
# 判读：uptime 单调递增 = 没重启；出现回零/骤降 = 重启，且回零处的秒数就是周期。
# 顺带在断连时重试，避免把 hdc 抖动误判成重启。
H='/mnt/d/environment/command-line-tools/sdk/default/openharmony/toolchains/hdc.exe'
LOG=${1:-/home/plus/learn/SFRNet/build/tmp_md5/oh_uptime.log}
DEADLINE=$(( $(date +%s) + 5400 ))   # 90 分钟，覆盖 1.5 个「一小时」周期

up() { # 最多重试 4 次取 uptime，返回空串表示彻底够不着
    for i in 1 2 3 4; do
        v=$("$H" shell cat /proc/uptime 2>/dev/null | tr -d '\r' | awk '{print $1; exit}')
        [ -n "$v" ] && { echo "$v"; return; }
        sleep 5
    done
    echo ""
}

prev=""; miss=0
echo "# 起 $LOG  $(date '+%F %T')" >> "$LOG"
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    cur=$(up)
    ts=$(date '+%H:%M:%S')
    if [ -z "$cur" ]; then
        miss=$((miss+1))
        echo "$ts  无响应 (连续 $miss 次)" >> "$LOG"
        # 连续多次够不着 = 设备真的下来了（重启期间 HDC 未起），记为候选重启点
        [ "$miss" -eq 3 ] && echo "$ts  *** ALERT: 连续 3 次取不到 uptime，疑似设备离线/重启 ***" >> "$LOG"
    else
        if [ -n "$prev" ]; then
            d=$(awk -v a="$cur" -v b="$prev" 'BEGIN{printf "%.0f", a-b}')
            if [ "$d" -lt -5 ]; then
                echo "$ts  uptime=$cur  *** 重启发生：上一读=$prev，本次回退 ${d}s（周期≈${prev}s）***" >> "$LOG"
            else
                echo "$ts  uptime=$cur  (+${d}s)" >> "$LOG"
            fi
        else
            echo "$ts  uptime=$cur  (基线)" >> "$LOG"
        fi
        prev="$cur"; miss=0
    fi
    sleep 30
done
echo "# 止 $(date '+%F %T')  窗口结束" >> "$LOG"
