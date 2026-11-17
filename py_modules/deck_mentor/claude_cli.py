"""Run the official Claude Code CLI in headless mode.

Going through the unmodified `claude` binary is what lets the player use their
own Claude subscription: the CLI owns the sign-in, and this plugin never reads,
stores or forwards the credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncIterator, Callable

from .util import clean_env, user_home, which

STREAM_LIMIT = 32 * 1024 * 1024  # one stdout line can carry a whole message


def find_claude(custom_path: str = "") -> str | None:
    home = user_home()
    candidates = [
        custom_path,
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(home, ".claude", "local", "claude"),
        os.path.join(home, ".npm-global", "bin", "claude"),
        which("claude"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def cli_env(api_key: str = "") -> dict[str, str]:
    env = clean_env({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"})
    # An API key in the environment silently wins over the subscription login,
    # so only pass one when the player chose the API-key mode.
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    return env


async def run_simple(args: list[str], env: dict[str, str], timeout: float = 20.0, cwd: str | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", "timeout"
    return proc.returncode or 0, out.decode(errors="ignore"), err.decode(errors="ignore")


async def status(claude: str | None, api_key: str = "") -> dict:
    if not claude:
        return {"found": False}
    env = cli_env(api_key)
    info: dict = {"found": True, "path": claude}
    rc, out, _err = await run_simple([claude, "--version"], env, timeout=20)
    info["version"] = out.strip().split(" ")[0] if rc == 0 else ""
    rc, out, err = await run_simple([claude, "auth", "status", "--json"], env, timeout=20)
    try:
        data = json.loads(out)
        info.update(
            loggedIn=bool(data.get("loggedIn")) or bool(api_key),
            authMethod=data.get("authMethod") or ("api_key" if api_key else ""),
            projectsDirectory=data.get("projectsDirectory") or "",
        )
    except (ValueError, TypeError):
        info.update(loggedIn=bool(api_key), authMethod="api_key" if api_key else "", error=(err or out).strip()[-300:])
    return info


def user_message(text: str, png_b64: str | None = None) -> str:
    content: list[dict] = []
    if png_b64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}})
    content.append({"type": "text", "text": text})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}) + "\n"


def describe_tool(name: str, data: dict) -> str:
    if name == "WebSearch":
        return f"Ищу в интернете: {data.get('query', '')}"
    if name == "WebFetch":
        return f"Читаю страницу: {data.get('url', '')}"
    if name == "Read":
        return f"Читаю файл: {os.path.basename(str(data.get('file_path', '')))}"
    if name in ("Glob", "Grep"):
        return f"Ищу файлы: {data.get('pattern', '')}"
    if name == "Bash":
        return f"Запускаю: {data.get('description') or str(data.get('command', ''))[:60]}"
    if name in ("Write", "Edit"):
        return f"Пишу: {os.path.basename(str(data.get('file_path', '')))}"
    return name


async def stream_turn(
    claude: str,
    args: list[str],
    message: str,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    on_process: Callable[[asyncio.subprocess.Process], None] | None = None,
) -> AsyncIterator[dict]:
    """Run one headless turn and yield simplified events.

    Event types: init, text (delta), tool (label), limit, result.
    """
    cmd = [
        claude, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        *args,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        limit=STREAM_LIMIT,
    )
    if on_process:
        on_process(proc)

    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(message.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    stderr_chunks: list[bytes] = []

    async def drain_stderr() -> None:
        assert proc.stderr
        while chunk := await proc.stderr.read(4096):
            stderr_chunks.append(chunk)
            del stderr_chunks[:-8]

    err_task = asyncio.create_task(drain_stderr())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    got_result = False
    messages_seen = 0

    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            if not line:
                break
            try:
                data = json.loads(line)
            except ValueError:
                continue
            kind = data.get("type")

            if kind == "system" and data.get("subtype") == "init":
                yield {"type": "init", "session_id": data.get("session_id"), "model": data.get("model"),
                       "auth": data.get("apiKeySource")}
            elif kind == "stream_event":
                event = data.get("event") or {}
                etype = event.get("type")
                if etype == "message_start":
                    messages_seen += 1
                elif etype == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield {"type": "text", "delta": delta["text"], "message": messages_seen}
            elif kind == "assistant":
                for block in (data.get("message") or {}).get("content") or []:
                    if block.get("type") in ("tool_use", "server_tool_use"):
                        yield {"type": "tool", "label": describe_tool(block.get("name", ""), block.get("input") or {})}
            elif kind == "rate_limit_event":
                info = data.get("rate_limit_info") or {}
                yield {"type": "limits", "info": info}
                if info.get("status") not in (None, "allowed", "allowed_warning"):
                    yield {"type": "limit", "resetsAt": info.get("resetsAt"), "kind": info.get("rateLimitType")}
            elif kind == "result":
                got_result = True
                yield {
                    "type": "result",
                    "ok": not data.get("is_error") and data.get("subtype") == "success",
                    "subtype": data.get("subtype"),
                    "text": data.get("result") or "\n".join(str(e) for e in data.get("errors") or []),
                    "session_id": data.get("session_id"),
                    "cost": data.get("total_cost_usd"),
                    "turns": data.get("num_turns"),
                }
    finally:
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        err_task.cancel()

    if not got_result:
        tail = b"".join(stderr_chunks).decode(errors="ignore").strip()
        yield {"type": "result", "ok": False, "subtype": "crash", "text": tail[-600:] or f"claude завершился с кодом {proc.returncode}"}
