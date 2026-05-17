#!/usr/bin/env python3
"""Unit tests for cc-notify core logic."""

import sys
import json
import time
import tempfile
from pathlib import Path

# Add the script dir to path so we can import from it
sys.path.insert(0, str(Path(__file__).parent))

# Import from cc-notify module
import importlib.util
spec = importlib.util.spec_from_file_location("cc_notify", "cc-notify.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def test_config_defaults():
    """Default config is returned when no file exists."""
    config = cc.load_config()
    assert "patterns" in config
    assert "ignore_patterns" in config
    assert "cooldown_seconds" in config
    assert config["cooldown_seconds"] == 10
    print("  PASS test_config_defaults")


def test_config_merge():
    """User config merges with defaults."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"cooldown_seconds": 5, "patterns": ["custom"]}, f)
    try:
        # Override CONFIG_PATH
        saved = cc.CONFIG_PATH
        cc.CONFIG_PATH = Path(f.name)
        config = cc.load_config()
        assert config["cooldown_seconds"] == 5
        assert config["patterns"] == ["custom"]
        # Default keys still present
        assert "ignore_patterns" in config
        cc.CONFIG_PATH = saved
        print("  PASS test_config_merge")
    finally:
        Path(f.name).unlink(missing_ok=True)


def test_pattern_matching():
    """PatternMatcher detects confirmation patterns."""
    matcher = cc.PatternMatcher(cc.DEFAULT_CONFIG)

    # Direct keyword match
    assert matcher.feed("Do you want to continue? (Y/n) \n") is not None
    print("  PASS Y/n match")

    # Confirm match
    assert matcher.feed("Confirm changes? \n") is not None
    print("  PASS Confirm match")

    # Proceed match
    assert matcher.feed("Proceed with installation? \n") is not None
    print("  PASS Proceed match")

    # Chinese match
    assert matcher.feed("确认执行吗？\n") is not None
    print("  PASS Chinese match")

    # No match - regular output
    assert matcher.feed("Compiling...\n") is None
    print("  PASS non-match regular output")

    # No match - empty line
    assert matcher.feed("\n") is None
    print("  PASS non-match empty line")


def test_question_timeout():
    """?-line timeout detection works."""
    matcher = cc.PatternMatcher(cc.DEFAULT_CONFIG)

    # Feed partial line ending with ?
    matcher.feed("Are you sure you want to delete this file")
    result = matcher.check_timeout()
    assert result is None  # No ? at end

    # Clear buffer and feed line with ?
    matcher._buf = "Are you sure you want to delete this file?"
    result = matcher.check_timeout()
    assert result is not None
    print("  PASS ?-line timeout detection")


def test_dedup():
    """Duplicate prompts within cooldown are suppressed."""
    config = cc.DEFAULT_CONFIG.copy()
    config["cooldown_seconds"] = 10
    matcher = cc.PatternMatcher(config)

    line = "Do you want to proceed? (Y/n) "
    # First time should match
    result1 = matcher.feed(line + "\n")
    assert result1 is not None
    print("  PASS first occurrence detected")

    # Second time within cooldown should be suppressed
    result2 = matcher.feed(line + "\n")
    assert result2 is None
    print("  PASS duplicate suppressed")


def test_ignore_patterns():
    """Ignore patterns exclude certain lines."""
    config = cc.DEFAULT_CONFIG.copy()
    config["ignore_patterns"] = [r"DEBUG.*Y/n"]
    matcher = cc.PatternMatcher(config)

    # Should be ignored
    matcher._buf = ""
    result = matcher.feed("DEBUG: asking Y/n for testing\n")
    assert result is None
    print("  PASS ignored pattern")

    # Should still match
    matcher._buf = ""
    result = matcher.feed("Proceed? (Y/n)\n")
    assert result is not None
    print("  PASS non-ignored still matches")


def test_escape_zenity():
    """Zenity text escaping."""
    result = cc._escape_zenity(r"test\path")
    assert result == r"test\\path"
    print("  PASS zenity escape")


def test_partial_line_buffering():
    """Chunked output across multiple feed calls still matches."""
    matcher = cc.PatternMatcher(cc.DEFAULT_CONFIG)

    # Feed in chunks
    assert matcher.feed("Some ou") is None
    assert matcher.feed("tput\nDo you want to ") is None
    result = matcher.feed("proceed? (Y/n) \n")
    assert result is not None
    print("  PASS chunked input matching")


if __name__ == "__main__":
    print("Running cc-notify unit tests...\n")
    tests = [
        test_config_defaults,
        test_config_merge,
        test_pattern_matching,
        test_question_timeout,
        test_dedup,
        test_ignore_patterns,
        test_escape_zenity,
        test_partial_line_buffering,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
            failed += 1

    print(f"\n{failed} failed, {len(tests) - failed} passed out of {len(tests)}")
    sys.exit(1 if failed else 0)
