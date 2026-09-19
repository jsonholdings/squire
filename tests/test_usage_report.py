"""Tests for hooks/usage_report.py's per-agent attribution (2026-09-18, TODO "squire usage
report can't attribute per agent"). Pure unit tests against the importable functions -- no
subprocess needed since this module has no side effects on import."""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "hooks" / "usage_report.py"

spec = importlib.util.spec_from_file_location("usage_report", MOD_PATH)
usage_report = importlib.util.module_from_spec(spec)
sys.modules["usage_report"] = usage_report
spec.loader.exec_module(usage_report)


def test_per_agent_table_groups_by_session_and_agent_id():
    rows = [
        {"session": "s1", "agent_id": None, "agent_type": None, "cmd": "run", "backend_ok": True},
        {"session": "s1", "agent_id": "a1", "agent_type": "worker", "cmd": "sum", "backend_ok": True},
        {"session": "s1", "agent_id": "a1", "agent_type": "worker", "cmd": "sum", "backend_ok": True},
        {"session": "s1", "agent_id": "a2", "agent_type": "Explore", "cmd": "grep", "backend_ok": True},
    ]
    agents = usage_report.per_agent_table(rows, [])
    main_key = "s1/main"
    a1_key = "s1/a1"
    a2_key = "s1/a2"
    assert agents[main_key]["by_cmd"] == {"run": 1}
    assert agents[main_key]["agent_id"] == "main"
    assert agents[a1_key]["by_cmd"] == {"sum": 2}
    assert agents[a1_key]["agent_type"] == "worker"
    assert agents[a2_key]["by_cmd"] == {"grep": 1}
    assert agents[a2_key]["agent_type"] == "Explore"


def test_per_agent_table_counts_raw_overrides_per_agent():
    rows = []
    overrides = [
        {"session": "s1", "agent_id": "a1", "agent_type": "worker", "reason": "x"},
        {"session": "s1", "agent_id": "a1", "agent_type": "worker", "reason": "y"},
        {"session": "s1", "agent_id": None, "agent_type": None, "reason": "z"},
    ]
    agents = usage_report.per_agent_table(rows, overrides)
    assert agents["s1/a1"]["raw_overrides"] == 2
    assert agents["s1/main"]["raw_overrides"] == 1


def test_per_agent_table_respects_session_filter():
    rows = [
        {"session": "s1", "agent_id": "a1", "cmd": "sum", "backend_ok": True},
        {"session": "s2", "agent_id": "a1", "cmd": "sum", "backend_ok": True},
    ]
    agents = usage_report.per_agent_table(rows, [], session_filter="s1")
    assert list(agents.keys()) == ["s1/a1"]


def test_per_agent_table_excludes_fixture_rows():
    rows = [
        {"session": "s1", "agent_id": "a1", "cmd": "sum", "backend_ok": False,
         "gen_s": 0.001, "chars_in": 10, "chars_out": 0},
    ]
    assert usage_report.is_fixture_row(rows[0])
    agents = usage_report.per_agent_table(rows, [])
    assert agents == {}


def test_format_agents_text_labels_main_thread():
    agents = {
        "s1/main": {"session": "s1", "agent_id": "main", "agent_type": None,
                     "by_cmd": {"run": 1}, "raw_overrides": 0},
    }
    text = usage_report.format_agents_text(agents)
    assert "main thread" in text
