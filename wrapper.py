#!/usr/bin/env python3
"""
cc-notify wrapper - 代理 VS Code 扩展与 Claude 之间的 JSON 通信。

拦截 can_use_tool 确认请求，弹出 zenity 对话框。
用户点击 Allow/Deny 后注入正确的 JSON 回复。

用法（由包装器脚本自动调用）:
    wrapper.py <claude-real-binary-path> [args...]
"""

import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".cc-notify.json"
DEFAULT_CONFIG = {
    "cooldown_seconds": 10,
}


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CONFIG


# ── Zenity ──────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\")


def show_notify(tool_name: str, description: str, details: str) -> str | None:
    display = tool_name
    if description:
        display = f"{tool_name}: {description}"
    if len(display) > 200:
        display = display[:197] + "..."

    body = details if details else "(no details)"
    if len(body) > 400:
        body = body[:397] + "..."

    text = f"{display}\n\n{body}"

    try:
        proc = subprocess.run(
            [
                "zenity",
                "--question",
                "--title=cc-notify — Permission Request",
                f"--text={_escape(text)}",
                "--ok-label=Allow",
                "--cancel-label=Deny",
                "--width=500",
                "--no-markup",
            ],
            timeout=300,
        )
        if proc.returncode == 0:
            return "allow"
        elif proc.returncode == 1:
            return "deny"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# ── Main proxy loop ────────────────────────────────────────────────

def main():

    if len(sys.argv) < 2:
        print("Usage: wrapper.py <real-binary> [args...]", file=sys.stderr)
        sys.exit(1)

    real_binary = sys.argv[1]
    args = sys.argv[2:]
    config = load_config()


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

    # Dedup
    _cooldown = config.get("cooldown_seconds", 10)
    _seen_hashes: set = set()
    _last_notify: float = 0.0

    # Notification state
    notify_lock = threading.Lock()
    pending_request: dict | None = None
    pending_result: str | None = None
    notify_done = threading.Event()
    notify_done.set()

    intercepted_ids: set = set()

    def _notify_worker(req_msg: dict):
        nonlocal pending_result
        tool_name = req_msg.get("request", {}).get("tool_name", "?")
        description = req_msg.get("request", {}).get("description", "")
        inp = req_msg.get("request", {}).get("input", {})
        details = json.dumps(inp, indent=2) if inp else ""
        outcome = show_notify(tool_name, description, details)
        with notify_lock:
            pending_result = outcome
            notify_done.set()

    out_buf = b""

    while child.poll() is None:
        try:
            readable, _, _ = select.select(monitor_fds, [], [], 0.2)
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

            elif fd == c_out:
                try:
                    data = os.read(c_out, 65536)
                except OSError:
                    continue
                if not data:
                    monitor_fds.remove(c_out)
                    continue

                out_buf += data

                while b"\n" in out_buf:
                    line_bytes, out_buf = out_buf.split(b"\n", 1)
                    line_bytes += b"\n"
                    line_str = line_bytes.decode("utf-8", errors="replace").strip()

                    forward = True

                    if line_str and notify_done.is_set():
                        try:
                            msg = json.loads(line_str)
                        except json.JSONDecodeError:
                            os.write(stdout_fd, line_bytes)
                            continue

                        msg_type = msg.get("type", "")

                        # ── Intercept can_use_tool ──
                        if msg_type == "control_request":
                            req = msg.get("request", {})
                            if req.get("subtype") == "can_use_tool":
                                request_id = msg.get("request_id", "")
                                tool_name = req.get("tool_name", "?")
                                inp = req.get("input", {})


                                # Dedup
                                h = hash(json.dumps(inp, sort_keys=True))
                                now = time.monotonic()
                                if h in _seen_hashes and (now - _last_notify) < _cooldown:
                                    os.write(stdout_fd, line_bytes)
                                    continue

                                _seen_hashes.add(h)
                                _last_notify = now

                                forward = False
                                intercepted_ids.add(request_id)
                                with notify_lock:
                                    pending_request = msg
                                    pending_result = None
                                notify_done.clear()
                                threading.Thread(
                                    target=_notify_worker,
                                    args=(msg,),
                                    daemon=True,
                                ).start()

                        # ── Filter echoed control_response ──
                        elif msg_type == "control_response":
                            resp = msg.get("response", {})
                            rid = resp.get("request_id", "")
                            if rid in intercepted_ids:
                                forward = False
                                intercepted_ids.discard(rid)

                    if forward:
                        try:
                            os.write(stdout_fd, line_bytes)
                        except OSError:
                            pass

            elif fd == c_err:
                try:
                    data = os.read(c_err, 65536)
                except OSError:
                    continue
                if not data:
                    monitor_fds.remove(c_err)
                    continue
                try:
                    os.write(stderr_fd, data)
                except OSError:
                    pass

        # ── Handle notification result ──
        if notify_done.is_set():
            with notify_lock:
                result = pending_result
                req = pending_request
                pending_result = None
                pending_request = None

            if req is not None:
                request_id = req.get("request_id", "")
                if result in ("allow", "deny"):
                    response = {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request_id,
                            "response": {
                                "behavior": result,
                                "updatedInput": {},
                                "updatedPermissions": [],
                            },
                        },
                    }
                    payload = json.dumps(response, separators=(",", ":")) + "\n"
                    try:
                        os.write(c_in, payload.encode())
                    except OSError:
                        pass
                else:
                    # User closed zenity → forward blocked msg to extension
                    try:
                        os.write(stdout_fd, json.dumps(req, separators=(",", "")).encode())
                        os.write(stdout_fd, b"\n")
                    except OSError:
                        pass

    # Drain
    for fd, out_fd in [(c_out, stdout_fd), (c_err, stderr_fd)]:
        while True:
            try:
                data = os.read(fd, 65536)
                if not data:
                    break
                os.write(out_fd, data)
            except OSError:
                break


    if child.poll() is None:
        try:
            child.terminate()
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()

    sys.exit(child.returncode)


if __name__ == "__main__":
    main()
