"""
parse_traj.py — turn a SWE-agent .traj file into a reduced trajectory.

Output: an ordered list of events. Each event is a dict with a "kind":
  - "edit": a file write via str_replace_editor
        {kind, subcommand, path, old_str, new_str, insert_line, file_text, step}
  - "test": a shell command that runs tests
        {kind, command, step}
  - "revert": rm / git checkout of files the agent made or touched
        {kind, paths, command, step}
  - "shell_write": a bash command that *might* have written a file (flagged for review)
        {kind, command, step}

Everything else in the trajectory (views, greps, cats, thinking) is dropped.
"""

import json
import shlex
import sys
from pathlib import Path

# Subcommands of str_replace_editor that change a file. "view" is a read.
WRITE_SUBCOMMANDS = {"str_replace", "create", "insert", "undo_edit"}

# Shell commands we treat as "the agent ran tests". Tune this after looking at data.
TEST_PREFIXES = ("pytest", "python -m pytest", "python ", "python3 ", "tox", "make test")

# Shell commands that undo earlier work: deleting scratch files, restoring files from git.
REVERT_PREFIXES = ("rm ", "git checkout -- ", "git checkout ", "git restore ")

# Shell patterns that suggest a file was written outside str_replace_editor.
SHELL_WRITE_HINTS = ("sed -i", ">", ">>", "tee ", "mv ", "cp ", "touch ", "git apply", "patch ")

def last_command(action):
    """
    The agent almost always writes 'cd /testbed && <real command>'.
    Return just the <real command> part so prefix checks work.
    """
    return action.split("&&")[-1].strip()

def load_steps(traj_path):
    """Read the .traj JSON and return the list of agent steps."""
    with open(traj_path) as f:
        data = json.load(f)
    # SWE-agent 1.x stores the step-by-step log under "trajectory".
    return data["trajectory"]

def parse_editor_action(action):
    """
    Parse a str_replace_editor command string into its pieces.
    Example action:
        str_replace_editor str_replace /testbed/foo.py --old_str 'a' --new_str 'b'
    Returns a dict, or None if it's a read (view) or unparseable.
    """
    try:
        tokens = shlex.split(action)
    except ValueError:
        return None  # unbalanced quotes; flag it later if it matters

    if len(tokens) < 3 or tokens[0] != "str_replace_editor":
        return None

    subcommand = tokens[1]
    if subcommand not in WRITE_SUBCOMMANDS:
        return None  # "view" or something unexpected

    path = tokens[2]
    kwargs = {}
    i = 3
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--") and i + 1 < len(tokens):
            kwargs[tok[2:]] = tokens[i + 1]
            i += 2
        else:
            i += 1  # stray token; skip

    return {
        "kind": "edit",
        "subcommand": subcommand,
        "path": path,
        "old_str": kwargs.get("old_str"),
        "new_str": kwargs.get("new_str"),
        "insert_line": kwargs.get("insert_line"),
        "file_text": kwargs.get("file_text"),
    }

def is_test_command(action):
    """Heuristic: does this shell command look like a test run?"""
    a = last_command(action)
    return any(a.startswith(p) for p in TEST_PREFIXES)

def parse_revert(action):
    """
    Recognise 'rm a b c' and 'git checkout -- a b' as reverts.
    Returns {"kind": "revert", "paths": [...]} or None.
    """
    a = last_command(action)
    if not any(a.startswith(p) for p in REVERT_PREFIXES):
        return None
    try:
        tokens = shlex.split(a)
    except ValueError:
        return None
    # drop the command words and any flags; what's left are file paths
    paths = [t for t in tokens[1:] if not t.startswith("-") and t != "checkout" and t != "restore"]
    return {"kind": "revert", "paths": paths, "command": a}

def looks_like_shell_write(action):
    """Heuristic: could this shell command have modified a file?"""
    return any(h in last_command(action) for h in SHELL_WRITE_HINTS)

def reduce_trajectory(steps):
    """Walk every step and keep only edits and test runs, in order."""
    events = []
    for idx, step in enumerate(steps):
        action = (step.get("action") or "").strip()
        if not action:
            continue

        if action.startswith("str_replace_editor"):
            edit = parse_editor_action(action)
            if edit is not None:
                edit["step"] = idx
                events.append(edit)
            continue  # a "view" falls through to here and is dropped

        if is_test_command(action):
            events.append({"kind": "test", "command": last_command(action), "step": idx})
            continue

        revert = parse_revert(action)
        if revert is not None:
            revert["step"] = idx
            events.append(revert)
            continue

        if looks_like_shell_write(action):
            events.append({"kind": "shell_write", "command": action, "step": idx})
            continue

        # anything else: cat, grep, find, ls, cd, submit, ... -> dropped
    return events

def summarize(events):
    """Print a one-line-per-event view so you can eyeball the reduced trajectory."""
    for e in events:
        if e["kind"] == "edit":
            print(f"[{e['step']:>3}] edit  {e['subcommand']:<12} {e['path']}")
        elif e["kind"] == "test":
            print(f"[{e['step']:>3}] test  {e['command'][:80]}")
        elif e["kind"] == "revert":
            print(f"[{e['step']:>3}] undo  {' '.join(e['paths'])}")
        else:
            print(f"[{e['step']:>3}] ???   {e['command'][:80]}   <-- shell write? check me")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python parse_traj.py path/to/instance.traj")
        sys.exit(1)

    traj_path = Path(sys.argv[1])
    steps = load_steps(traj_path)
    events = reduce_trajectory(steps)
    summarize(events)
    print(f"\n{len(steps)} steps -> {len(events)} kept "
          f"({sum(e['kind']=='edit' for e in events)} edits, "
          f"{sum(e['kind']=='test' for e in events)} tests, "
          f"{sum(e['kind']=='revert' for e in events)} reverts, "
          f"{sum(e['kind']=='shell_write' for e in events)} flagged)")