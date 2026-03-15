"""Verify every @tool()-decorated function in _build_tools is returned in the tools list.

Catches the bug where a tool is defined but never added to the list that
_build_tools() returns, making it invisible to agents at runtime.
"""

from __future__ import annotations

import re
from pathlib import Path


TOOLS_PY = Path(__file__).resolve().parent.parent / "open_strix" / "tools.py"


def test_all_tools_registered():
    """Every @tool() function inside _build_tools must appear in the returned list."""
    source = TOOLS_PY.read_text()

    # Extract function names defined with @tool("...") decorator.
    # Pattern: @tool("name") followed by [async] def func_name(
    tool_funcs: set[str] = set()
    for m in re.finditer(
        r'@tool\([^)]+\)\s+(?:async\s+)?def\s+(\w+)\s*\(', source
    ):
        tool_funcs.add(m.group(1))

    assert tool_funcs, "Found no @tool() definitions — regex may need updating"

    # Extract variable names from the `tools: list[Any] = [...]` block.
    list_match = re.search(
        r'tools:\s*list\[Any\]\s*=\s*\[(.*?)\]', source, re.DOTALL
    )
    assert list_match, "Could not find `tools: list[Any] = [...]` in tools.py"
    listed = set(re.findall(r'\b(\w+)\b', list_match.group(1)))

    # Also pick up tools added conditionally via tools.insert(...) or tools.append(...)
    for m in re.finditer(r'tools\.(?:insert|append)\([^,]*,?\s*(\w+)', source):
        listed.add(m.group(1))

    missing = tool_funcs - listed
    assert not missing, (
        f"Tools defined with @tool() but missing from the returned list: {missing}. "
        f"Add them to the `tools` list in _build_tools()."
    )
