#!/usr/bin/env python3
"""
cc-notify wrapper - 代理 VS Code 扩展与 Claude 之间的 JSON 通信。

拦截 can_use_tool 确认请求，弹出原生 GTK3 三按钮对话框。
仅响应鼠标点击，键盘输入不会误触。

用法（由包装器脚本自动调用）:
    wrapper.py <claude-real-binary-path> [args...]
"""

import json
import os
import select
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

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


# ── Preview text ────────────────────────────────────────────────────

# (internal helper — see _build_preview above)

def _build_preview(tool_name: str, description: str, inp: dict) -> str:
    """Build human-readable preview text for the zenity dialog."""
    lines = []

    # Title line
    lines.append(f"{tool_name} - 需要确认权限")
    lines.append("")

    # Description
    if description:
        desc = description.strip()
        if len(desc) > 120:
            desc = desc[:117] + "..."
        lines.append(f"描述：{desc}")
        lines.append("")

    # Command / file preview
    preview = _extract_preview(tool_name, inp)
    if preview:
        lines.append("预览：")
        # Truncate and word-wrap
        wrapped = textwrap.fill(preview, width=65, max_lines=6,
                                placeholder="...", break_long_words=False)
        for wline in wrapped.splitlines():
            lines.append(f"  {wline}")

    lines.append("")
    lines.append("请选择操作：")

    return "\n".join(lines)


def _extract_preview(tool_name: str, inp: dict) -> str:
    """Extract a meaningful preview string from tool input."""
    if not inp:
        return ""

    tool_name_lower = tool_name.lower()

    # Bash: show the command
    if tool_name_lower == "bash":
        cmd = inp.get("command", "")
        return cmd.strip() if cmd else ""

    # Edit / Write / MultiEdit: show file path + first diff
    if tool_name_lower in ("edit", "write", "multiedit"):
        file_path = inp.get("file_path", inp.get("filePath", ""))
        if file_path:
            return f"文件: {file_path}"
        return ""

    # Read: show file path
    if tool_name_lower == "read":
        file_path = inp.get("file_path", inp.get("filePath", ""))
        if file_path:
            return f"文件: {file_path}"
        return ""

    # Generic: serialize the input
    return json.dumps(inp, indent=2, ensure_ascii=False)


# ── GTK3 notification dialog ────────────────────────────────────────

OPTION_ALLOW_ONCE = "允许本次"
OPTION_ALWAYS = "始终允许"
OPTION_DENY = "拒绝"


def show_notify(preview_text: str, has_suggestions: bool) -> str | None:
    """
    Native GTK3 dialog with three buttons. Only mouse clicks are accepted;
    keyboard input does NOT trigger any button.

    Returns one of the OPTION_* constants, or None if the window is closed.
    """

    class _Dialog(Gtk.Window):
        def __init__(self):
            super().__init__(title="cc-notify")
            self.set_position(Gtk.WindowPosition.CENTER)
            self.set_keep_above(True)
            self.result = None

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.add(vbox)

            # Preview text
            label = Gtk.Label(
                label=preview_text,
                xalign=0,
                yalign=0,
                margin=16,
                can_focus=False,
            )
            vbox.pack_start(label, True, True, 0)

            # Separator
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            vbox.pack_start(sep, False, False, 0)

            # Button row
            btn_box = Gtk.ButtonBox()
            btn_box.set_layout(Gtk.ButtonBoxStyle.EXPAND)
            btn_box.set_spacing(8)
            btn_box.set_margin_top(10)
            btn_box.set_margin_bottom(10)
            btn_box.set_margin_start(12)
            btn_box.set_margin_end(12)
            btn_box.set_homogeneous(True)

            # "允许本次" — suggested (blue)
            btn_allow = Gtk.Button(label="允许本次")
            btn_allow.get_style_context().add_class("suggested-action")
            btn_allow.connect("clicked", lambda w: self._choose(OPTION_ALLOW_ONCE))
            btn_box.pack_start(btn_allow, True, True, 0)

            # "始终允许" — normal
            if has_suggestions:
                btn_always = Gtk.Button(label="始终允许")
                btn_always.connect("clicked", lambda w: self._choose(OPTION_ALWAYS))
                btn_box.pack_start(btn_always, True, True, 0)

            # "拒绝" — subtle destructive
            btn_deny = Gtk.Button(label="拒绝")
            btn_deny.get_style_context().add_class("destructive-action")
            btn_deny.connect("clicked", lambda w: self._choose(OPTION_DENY))
            btn_box.pack_start(btn_deny, True, True, 0)

            vbox.pack_start(btn_box, False, False, 0)

            self.connect("destroy", self._on_destroy)
            self.show_all()

        def _choose(self, value):
            self.result = value
            self.destroy()

        def _on_destroy(self, _widget):
            if self.result is None:
                self.result = None  # window closed via X button
            Gtk.main_quit()

    dlg = _Dialog()
    Gtk.main()
    return dlg.result


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
    pending_choice: str | None = None
    notify_done = threading.Event()
    notify_done.set()

    intercepted_ids: set = set()

    def _notify_worker(req_msg: dict):
        nonlocal pending_choice
        req = req_msg.get("request", {})
        tool_name = req.get("tool_name", "?")
        description = req.get("description", "")
        inp = req.get("input", {})
        suggestions = req.get("permission_suggestions", [])

        preview = _build_preview(tool_name, description, inp)
        choice = show_notify(preview, bool(suggestions))

        with notify_lock:
            pending_choice = choice
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
                                    pending_choice = None
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
                choice = pending_choice
                req = pending_request
                pending_choice = None
                pending_request = None

            if req is not None:
                request_id = req.get("request_id", "")

                if choice in (OPTION_ALLOW_ONCE, OPTION_ALWAYS, OPTION_DENY):
                    req_body = req.get("request", {})
                    behavior = "deny" if choice == OPTION_DENY else "allow"
                    updated_permissions = []
                    tool_use_id = req_body.get("tool_use_id", "")

                    # 始终允许：附带 permission_suggestions 持久化规则
                    if choice == OPTION_ALWAYS:
                        updated_permissions = req_body.get(
                            "permission_suggestions", []
                        )

                    response = {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request_id,
                            "response": {
                                "behavior": behavior,
                                "updatedInput": req_body.get("input", {}),
                                "updatedPermissions": updated_permissions,
                                "toolUseID": tool_use_id,
                            },
                        },
                    }
                    payload = json.dumps(response, separators=(",", ":")) + "\n"
                    try:
                        os.write(c_in, payload.encode())
                    except OSError:
                        pass
                else:
                    # User closed window → forward blocked msg to extension
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
