#!/usr/bin/env python3
"""
cc-notify - Desktop notification wrapper for CLI tools.

Pops up a dialog with Yes/No buttons when the wrapped command appears
to be waiting for user confirmation, so you don't miss prompts when
switched to another screen.

Usage: cc-notify <command> [args...]
Config: ~/.cc-notify.json
"""

import fcntl
import json
import os
import queue
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

# ── Default configuration ──────────────────────────────────────────

DEFAULT_CONFIG = {
    "patterns": [
        r"Y/n",
        r"y/N",
        r"yes/no",
        r"Yes/No",
        r"YES/NO",
        r"Confirm",
        r"Proceed",
        r"Allow",
        r"Apply changes",
        r"确认",
        r"是否继续",
    ],
    "ignore_patterns": [],
    "cooldown_seconds": 10,
}

CONFIG_PATH = Path.home() / ".cc-notify.json"


def load_config():
    """Load config from ~/.cc-notify.json, falling back to defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            merged.update(user)
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_CONFIG


# ── Pattern matcher ────────────────────────────────────────────────

class PatternMatcher:
    """Scans output lines for confirmation prompts, with dedup."""

    def __init__(self, config):
        self._patterns = [re.compile(p, re.IGNORECASE) for p in config["patterns"]]
        self._ignores = [re.compile(p, re.IGNORECASE) for p in config["ignore_patterns"]]
        self._cooldown = config["cooldown_seconds"]
        self._seen: dict[int, float] = {}
        self._buf = ""

    def feed(self, text: str) -> str | None:
        """
        Feed chunk of output text.
        Returns the matched line if a confirmation is detected, else None.
        """
        self._buf += text
        while "\n" in self._buf:
            raw, self._buf = self._buf.split("\n", 1)
            line = raw.strip()
            if not line:
                continue
            if self._is_match(line):
                return self._dedup(line)
        return None

    def check_timeout(self) -> str | None:
        """
        Called after a period of I/O inactivity.
        If the buffered partial line ends with '?', treat as a prompt.
        """
        line = self._buf.strip()
        if line and line.endswith("?"):
            if any(p.search(line) for p in self._ignores):
                return None
            return self._dedup(line)
        return None

    def _is_match(self, line: str) -> bool:
        if any(p.search(line) for p in self._ignores):
            return False
        return any(p.search(line) for p in self._patterns)

    def _dedup(self, line: str) -> str | None:
        key = hash(line[:80])
        now = time.monotonic()
        if key in self._seen and (now - self._seen[key]) < self._cooldown:
            return None
        self._seen[key] = now
        return line


# ── Notification (zenity) ──────────────────────────────────────────

def check_zenity() -> bool:
    """Return True if zenity is installed."""
    try:
        subprocess.run(["zenity", "--version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _escape_zenity(text: str) -> str:
    """Escape text for zenity --no-markup. Zenity with --no-markup
    still treats backslashes specially at line ends, so keep it simple."""
    return text.replace("\\", "\\\\")


def show_notify(prompt: str) -> str | None:
    """
    Pop up a zenity question dialog.
    Returns 'y', 'n', or None (if the user closed the window).
    """
    if len(prompt) > 500:
        prompt = prompt[:497] + "..."

    try:
        proc = subprocess.run(
            [
                "zenity",
                "--question",
                "--title=cc-notify",
                f"--text={_escape_zenity(prompt)}",
                "--ok-label=Yes",
                "--cancel-label=No",
                "--width=450",
                "--no-markup",
            ],
            timeout=300,
        )
        if proc.returncode == 0:
            return "y"
        elif proc.returncode == 1:
            return "n"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


# ── PTY helpers ────────────────────────────────────────────────────

def _term_size():
    try:
        cols, rows = os.get_terminal_size()
    except OSError:
        cols, rows = 80, 24
    return rows, cols


def _set_pty_size(fd, rows, cols):
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


# ── Main ───────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: cc-notify <command> [args...]")
        print()
        print("Desktop notification wrapper for CLI tools.")
        print("Pops up a dialog when the command appears to be waiting")
        print("for confirmation (Y/n prompts, permission requests, etc.)")
        print()
        print("Config: ~/.cc-notify.json")
        sys.exit(0)

    if not check_zenity():
        print("cc-notify: ERROR - zenity is required but not found.")
        print("Install it with:  sudo apt install zenity")
        sys.exit(1)

    cmd = sys.argv[1:]
    config = load_config()
    matcher = PatternMatcher(config)

    # ── Create PTY and spawn child ──
    master_fd, slave_fd = os.openpty()
    os.set_blocking(master_fd, False)
    rows, cols = _term_size()
    _set_pty_size(slave_fd, rows, cols)

    child = subprocess.Popen(
        cmd,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)

    # ── Notification coordination ──
    result_queue: queue.Queue = queue.Queue()
    notify_done = threading.Event()
    notify_done.set()  # starts as "ready to send"

    def _notify_worker(matched_text: str):
        outcome = show_notify(matched_text)
        result_queue.put(outcome)
        notify_done.set()

    # ── Raw terminal (pass through every keystroke) ──
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_is_tty = sys.stdin.isatty()
    saved_tc = None

    if stdin_is_tty:
        saved_tc = termios.tcgetattr(stdin_fd)
        raw_tc = termios.tcgetattr(stdin_fd)
        raw_tc[3] = raw_tc[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)
        raw_tc[6][termios.VMIN] = 1
        raw_tc[6][termios.VTIME] = 0
        termios.tcsetattr(stdin_fd, termios.TCSANOW, raw_tc)

    # Build the list of fds to monitor
    monitor_fds = [master_fd]
    if stdin_is_tty:
        monitor_fds.append(stdin_fd)

    last_io = time.monotonic()
    exit_code = 1

    try:
        while True:
            # ── Child still alive? ──
            if child.poll() is not None:
                # Drain any leftover output
                while True:
                    try:
                        chunk = os.read(master_fd, 4096)
                        if not chunk:
                            break
                        os.write(stdout_fd, chunk)
                    except (OSError, EOFError):
                        break
                exit_code = child.returncode
                break

            # ── Collect notification result ──
            try:
                outcome = result_queue.get_nowait()
                if outcome == "y":
                    os.write(master_fd, b"y\n")
                elif outcome == "n":
                    os.write(master_fd, b"n\n")
                # outcome is None → user closed window, do nothing
            except queue.Empty:
                pass

            # ── I/O multiplexing ──
            try:
                rlist, _, _ = select.select(monitor_fds, [], [], 0.5)
            except (OSError, ValueError):
                break

            now = time.monotonic()

            if rlist:
                for fd in rlist:
                    if fd == master_fd:
                        try:
                            data = os.read(master_fd, 4096)
                        except (OSError, EOFError):
                            data = b""
                        if not data:
                            break
                        os.write(stdout_fd, data)
                        matched = matcher.feed(data.decode("utf-8", errors="replace"))
                        if matched and notify_done.is_set():
                            notify_done.clear()
                            threading.Thread(
                                target=_notify_worker,
                                args=(matched,),
                                daemon=True,
                            ).start()
                        last_io = now

                    elif fd == stdin_fd:
                        try:
                            data = os.read(stdin_fd, 4096)
                        except (OSError, EOFError):
                            data = b""
                        if not data:
                            continue
                        os.write(master_fd, data)
                        last_io = now
            else:
                # select timed out → check ?-line heuristic
                if now - last_io >= 0.5 and notify_done.is_set():
                    matched = matcher.check_timeout()
                    if matched:
                        notify_done.clear()
                        threading.Thread(
                            target=_notify_worker,
                            args=(matched,),
                            daemon=True,
                        ).start()

            # ── Forward terminal resize ──
            new_rows, new_cols = _term_size()
            if (new_rows, new_cols) != (rows, cols):
                rows, cols = new_rows, new_cols
                try:
                    _set_pty_size(master_fd, rows, cols)
                except OSError:
                    pass

    finally:
        # Restore terminal settings
        if saved_tc is not None:
            termios.tcsetattr(stdin_fd, termios.TCSANOW, saved_tc)
        try:
            os.close(master_fd)
        except OSError:
            pass

        # Ensure child is cleaned up
        if child.poll() is None:
            try:
                os.killpg(os.getpgid(child.pid), signal.SIGTERM)
                child.wait(timeout=3)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                    child.wait()
                except ProcessLookupError:
                    pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
