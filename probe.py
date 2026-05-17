#!/usr/bin/env python3
"""
PROBE - 记录 VS Code 扩展与 Claude 之间的 JSON 通信。

用途：分析确认提示（permission prompt）的消息格式和回复格式。
运行后会在 ~/.cc-notify-logs/ 生成日志文件。

用法（由包装器脚本自动调用）:
    probe.py <claude-real-binary-path> [args...]
"""

import os
import sys
import select
import subprocess
from pathlib import Path
from datetime import datetime

LOG_DIR = Path.home() / ".cc-notify-logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"


def main():
    if len(sys.argv) < 2:
        print("Usage: probe.py <real-binary> [args...]", file=sys.stderr)
        sys.exit(1)

    real_binary = sys.argv[1]
    args = sys.argv[2:]

    print(f"[cc-notify probe] 日志: {LOG_FILE}", file=sys.stderr)

    child = subprocess.Popen(
        [real_binary] + args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stderr_fd = sys.stderr.fileno()

    c_out = child.stdout.fileno()
    c_err = child.stderr.fileno()
    c_in = child.stdin.fileno()

    for fd in [c_out, c_err]:
        os.set_blocking(fd, False)

    monitor_fds = [stdin_fd, c_out, c_err]

    with open(LOG_FILE, "w") as log:
        log.write(f"# cc-notify PROBE log\n")
        log.write(f"# Time: {datetime.now().isoformat()}\n")
        log.write(f"# Binary: {real_binary}\n")
        log.write(f"# Args: {args}\n\n")

        while child.poll() is None:
            try:
                readable, _, _ = select.select(monitor_fds, [], [], 0.5)
            except (OSError, ValueError):
                break

            for fd in readable:
                if fd == stdin_fd:
                    data = os.read(stdin_fd, 4096)
                    if not data:
                        monitor_fds.remove(stdin_fd)
                        try:
                            child.stdin.close()
                        except OSError:
                            pass
                        continue
                    try:
                        os.write(c_in, data)
                    except OSError:
                        pass
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            log.write(f"IN  | {line}\n")

                elif fd == c_out:
                    try:
                        data = os.read(c_out, 4096)
                    except OSError:
                        continue
                    if not data:
                        monitor_fds.remove(c_out)
                        continue
                    try:
                        os.write(stdout_fd, data)
                    except OSError:
                        pass
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            log.write(f"OUT | {line}\n")

                elif fd == c_err:
                    try:
                        data = os.read(c_err, 4096)
                    except OSError:
                        continue
                    if not data:
                        monitor_fds.remove(c_err)
                        continue
                    try:
                        os.write(stderr_fd, data)
                    except OSError:
                        pass
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            log.write(f"ERR | {line}\n")

            log.flush()

        # Drain remaining
        for label, fd, out_fd in [
            ("OUT*", c_out, stdout_fd),
            ("ERR*", c_err, stderr_fd),
        ]:
            while True:
                try:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    os.write(out_fd, data)
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            log.write(f"{label}| {line}\n")
                except OSError:
                    break

        log.write(f"\n# Exit code: {child.returncode}\n")
        log.write(f"# End: {datetime.now().isoformat()}\n")

    print(f"[cc-notify probe] 会话结束, 日志已保存", file=sys.stderr)
    sys.exit(child.returncode)


if __name__ == "__main__":
    main()
