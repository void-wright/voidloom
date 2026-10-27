# -*- coding: utf-8 -*-
"""2026-10-26 diagnostics: the Voidloom hook did not fire in the owner's Codex.

Codex's SessionStart matcher is a regex over `source` (startup, resume, clear,
compact, fork; an omitted matcher matches all -- learn.chatgpt.com/docs/hooks).
We registered `matcher: "startup"`, so a RESUMED thread never received the
brain: the owner's working thread started 2026-09-29, fired once, and was
resumed daily for four weeks without a fire. And `hooks install` treated any
element of ours as "already installed" whatever its shape, so a corrected shape
could never reach an existing install.

Works only in a scratch directory: a temp hooks.json and a temp brain home.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import voidloom_heartbeat as hb  # noqa: E402
import voidloom_hooks_status as hs  # noqa: E402

FOREIGN = {"hooks": [{"type": "command", "command": "echo someone-elses-hook"}]}


def _home(tmp_path) -> str:
    home = tmp_path / "home"
    (home / ".claude" / "memory" / "hooks").mkdir(parents=True)
    return str(home)


def test_the_codex_element_matches_every_session_source():
    el = hs.hook_element("codex", "python3 /x/session_start_inject.py")
    assert "matcher" not in el            # omitted = startup, resume, clear, compact, fork
    assert el["hooks"][0]["additionalContextLimit"] == hs.CODEX_ADDITIONAL_CONTEXT_LIMIT


def test_an_installed_startup_only_element_is_upgraded_in_place(tmp_path):
    home = _home(tmp_path)
    cmd = hs.hook_command(home)
    old = {"matcher": "startup", "hooks": [{"type": "command", "command": cmd,
                                            "additionalContextLimit": hs.CODEX_ADDITIONAL_CONTEXT_LIMIT}]}
    target = tmp_path / "codex" / "hooks.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"hooks": {"SessionStart": [FOREIGN, old]}}, indent=2) + "\n")
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert not plan.get("blocked"), plan.get("blockers")
    assert plan["action"] == "merge_element", plan.get("action")
    assert plan["superseded_shape"]["was"] == [old]
    assert "/hooks" in plan["superseded_shape"]["retrust"]
    res = hs.install_execute(plan)
    assert res["result"] not in ("REFUSED", "ALREADY_INSTALLED"), res
    groups = json.loads(target.read_text())["hooks"]["SessionStart"]
    ours = [g for g in groups if hs.is_our_hook_element(g)]
    assert ours == [hs.hook_element("codex", cmd)]        # exactly one, the new shape
    assert FOREIGN in groups                               # a foreign hook is never touched


def test_the_current_shape_still_reads_already_installed(tmp_path):
    home = _home(tmp_path)
    cmd = hs.hook_command(home)
    target = tmp_path / "codex" / "hooks.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"hooks": {"SessionStart": [hs.hook_element("codex", cmd)]}},
                                 indent=2) + "\n")
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert plan["action"] == "already_installed"
    assert "superseded_shape" not in plan


def _rollout(path: Path, source) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"type": "session_meta", "payload": {"id": path.stem[-36:], "timestamp": "2026-10-20T10:00:00Z",
                                                "originator": "codex-tui", "source": source,
                                                "base_instructions": "x" * 20000}}
    path.write_text(json.dumps(meta) + "\n")


def test_codex_exec_runs_and_subagent_threads_are_headless(tmp_path):
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "20"
    u = "0199aaaa-bbbb-7ccc-8ddd-{:012d}"
    _rollout(day / f"rollout-2026-10-20T10-00-00-{u.format(1)}.jsonl", "vscode")
    _rollout(day / f"rollout-2026-10-20T10-00-01-{u.format(2)}.jsonl", "cli")
    _rollout(day / f"rollout-2026-10-20T10-00-02-{u.format(3)}.jsonl", "exec")
    _rollout(day / f"rollout-2026-10-20T10-00-03-{u.format(4)}.jsonl",
             {"subagent": {"thread_spawn": {"parent_thread_id": u.format(1), "depth": 1}}})
    _rollout(day / f"rollout-2026-10-20T10-00-04-{u.format(5)}.jsonl", "some-future-surface")
    out = hb.list_platform_sessions("codex", env={"CODEX_HOME": str(tmp_path / "codex")})
    by = {s["id"][-1]: s["headless"] for s in out["sessions"]}
    assert by == {"1": False, "2": False, "3": True, "4": True, "5": False}


def test_a_key_the_user_added_is_not_drift(tmp_path):
    """Review F14: drift is judged on the keys Voidloom owns. A hand-added timeout on our
    element must not trigger a supersede -- on Codex every supersede forces a re-trust."""
    home = _home(tmp_path)
    cmd = hs.hook_command(home)
    el = hs.hook_element("codex", cmd)
    el["hooks"][0]["timeout"] = 30
    target = tmp_path / "codex" / "hooks.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"hooks": {"SessionStart": [el]}}, indent=2) + "\n")
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert plan["action"] == "already_installed" and "superseded_shape" not in plan


def _write(target: Path, groups) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"hooks": {"SessionStart": groups}}, indent=2) + "\n")


def _old(cmd: str) -> dict:
    return {"matcher": "startup", "hooks": [{"type": "command", "command": cmd,
                                             "additionalContextLimit": hs.CODEX_ADDITIONAL_CONTEXT_LIMIT}]}


def test_a_foreign_hook_sharing_our_element_is_never_dropped(tmp_path):
    """Review 2, N4: an element {hooks: [ours, foreign]} in the old shape must not be dropped
    to supersede ours -- the foreign hook would go with it."""
    home = _home(tmp_path)
    cmd = hs.hook_command(home)
    shared = _old(cmd)
    shared["hooks"].append({"type": "command", "command": "/usr/bin/other-tool --session"})
    target = tmp_path / "codex" / "hooks.json"
    _write(target, [shared])
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert "superseded_shape" not in plan and plan["superseded_shape_refused"]["indices"] == [0]
    hs.install_execute(plan)
    cmds = [h["command"] for g in json.loads(target.read_text())["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "/usr/bin/other-tool --session" in cmds
    assert "NOT REPLACED" in hs.render_install(plan, wet=False)


def test_the_install_output_says_what_it_replaced_and_asks_for_the_re_trust(tmp_path):
    """Review 2, N8: the human output names the supersede and the re-trust; the JSON does
    not call a current hook 'retired'."""
    home = _home(tmp_path)
    target = tmp_path / "codex" / "hooks.json"
    _write(target, [_old(hs.hook_command(home))])
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    text = hs.render_install(plan, wet=False)
    assert "SUPERSEDES" in text and "RE-TRUST" in text
    assert "superseded_legacy" not in plan


def test_a_different_brain_home_is_not_a_new_shape(tmp_path):
    """Review 2, N9: an element in the CURRENT shape pointing at another brain is a different
    install; it is never re-pointed (on Codex that would force a re-trust)."""
    home = _home(tmp_path)
    other = hs.hook_element("codex", "python3 /somewhere/else/.claude/memory/hooks/session_start_inject.py")
    target = tmp_path / "codex" / "hooks.json"
    _write(target, [other])
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert plan["action"] == "already_installed" and "superseded_shape" not in plan


def test_a_current_and_a_drifted_copy_become_exactly_one(tmp_path):
    """Review 2, N18: one current and one old-shape element of ours would both fire."""
    home = _home(tmp_path)
    cmd = hs.hook_command(home)
    target = tmp_path / "codex" / "hooks.json"
    _write(target, [_old(cmd), hs.hook_element("codex", cmd), FOREIGN])
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    hs.install_execute(plan)
    groups = json.loads(target.read_text())["hooks"]["SessionStart"]
    assert [g for g in groups if hs.is_our_hook_element(g)] == [hs.hook_element("codex", cmd)]
    assert FOREIGN in groups


def test_an_oversized_codex_head_keeps_its_machine_source(tmp_path):
    """Review 2, N17: a session_meta line past the 64 KB read keeps its source near the front."""
    day = tmp_path / "codex" / "sessions" / "2026" / "10" / "20"
    p = day / "rollout-2026-10-20T10-00-00-0199aaaa-bbbb-7ccc-8ddd-000000000009.jsonl"
    p.parent.mkdir(parents=True)
    meta = {"type": "session_meta", "payload": {"id": "x", "timestamp": "2026-10-20T10:00:00Z", "source": "exec",
                                                "base_instructions": "y" * 90000}}
    p.write_text(json.dumps(meta) + "\n")
    out = hb.list_platform_sessions("codex", env={"CODEX_HOME": str(tmp_path / "codex")})
    assert out["sessions"][0]["headless"] is True


def test_a_foreign_hook_sharing_a_legacy_element_is_never_dropped(tmp_path):
    """recheck R3-4 (N4, the legacy member)."""
    home = _home(tmp_path)
    shared = {"hooks": [{"type": "command", "command": "node %s/hooks/session-start.mjs" % home},
                        {"type": "command", "command": "/usr/bin/other-tool --session"}]}
    target = tmp_path / "codex" / "hooks.json"
    _write(target, [shared])
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert plan["superseded_shape_refused"]["indices"] == [0]
    hs.install_execute(plan)
    cmds = [h["command"] for g in json.loads(target.read_text())["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "/usr/bin/other-tool --session" in cmds


def test_a_current_element_sharing_space_with_a_foreign_hook_is_left_without_alarm(tmp_path):
    """recheck R3-3: a correct element of ours beside someone's hook is not 'NOT REPLACED'."""
    home = _home(tmp_path)
    el = hs.hook_element("codex", hs.hook_command(home))
    el["hooks"].append({"type": "command", "command": "/usr/bin/mine"})
    target = tmp_path / "codex" / "hooks.json"
    _write(target, [el])
    plan = hs.install_plan("codex", str(tmp_path), home=home, target=str(target))
    assert plan["action"] == "already_installed"
    assert "superseded_shape_refused" not in plan and "superseded_shape" not in plan
