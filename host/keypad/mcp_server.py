"""`keypad mcp`: the Claude Code MCP server (stdio) with ask_user."""

from __future__ import annotations

import os

from . import ipc

UNAVAILABLE = "KEYPAD UNAVAILABLE"


def run_mcp() -> None:
    from mcp.server.mcpserver import MCPServer

    from .version import version

    server = MCPServer("keypad", version=version(), instructions=(
        "ask_user asks the human on their hardware keypad (small color screen, 8 keys). Use it for decisions "
        f"with enumerable answers: yes/no, or a few short options. If the result starts with '{UNAVAILABLE}', "
        "ask in the conversation instead and do not retry."))
    cwd = os.getcwd()

    @server.tool()
    def ask_user(question: str, options: list[str] | None = None, multi: bool = False, header: str = "") -> str:
        """Ask the user a question on the hardware keypad and wait for the answer.

        Args:
            question: the question, short enough for a small screen
            options: up to 32 short choices (the first 3 have their own keys); omit for a yes/no question
            multi: let the user pick several options
            header: very short title, e.g. DATABASE
        """
        yes_no = not options
        q = {"text": question, "header": header, "options": options or [], "multi": multi, "yes_no": yes_no}
        try:
            ans = ipc.request("POST", "/ask", {"pid": os.getppid(), "cwd": cwd, "questions": [q]}, timeout=3700)
        except ipc.AgentNotRunning:
            return f"{UNAVAILABLE}: the keypad agent is not running."
        except ipc.RequestError as e:
            return f"{UNAVAILABLE}: {e}. Ask the user in the conversation instead."
        if not ans or ans[0].get("error"):
            why = ans[0]["error"] if ans else "no answer"
            return f"{UNAVAILABLE}: {why}. Ask the user in the conversation instead."
        if yes_no:
            return "The user answered: " + ("yes" if ans[0].get("yes") else "no")
        return "The user answered: " + ", ".join(ans[0].get("values") or [])

    server.run("stdio")
