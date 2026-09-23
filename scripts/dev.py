"""Cross-platform start / stop / status for The Lantern: the Windows-friendly twin of scripts/*.sh.

    uv run python scripts/dev.py start               # background server; PID and log in .run/ (like start.sh)
    uv run python scripts/dev.py start --foreground  # stay attached; Ctrl+C stops the server
    uv run python scripts/dev.py start --reload      # restart uvicorn on code changes (either mode)
    uv run python scripts/dev.py status
    uv run python scripts/dev.py stop
    uv run python scripts/dev.py restart             # also resets the in-memory floor, sessions and caches

The preflight mirrors start.sh: .venv present, .env created from .env.example, frontend built with
`npm ci` + `npm run build` when frontend/dist is missing, and Ollama started (model pulled) when
LLM_PROVIDER=ollama. An unset LLM_PROVIDER means ollama, as in config.py and start.sh; the provider and
model are exported to the server, and provider readiness is read back from /ready. `stop`
frees port 3000 only when a node process (the Vite dev server) holds it. Standard library only; on
macOS the .sh scripts keep working as before.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run"
PID_FILE = RUN_DIR / "uvicorn.pid"
LOG_FILE = RUN_DIR / "uvicorn.log"
OLLAMA_PID_FILE = RUN_DIR / "ollama.pid"
VITE_PORT = 3000
WINDOWS = os.name == "nt"
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


def setting(name: str, default: str) -> str:
    """Process environment first, then .env, then the default. Values are never printed."""
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name and value.strip():
                return value.strip().strip("'\"")
    return default


def http_json(url: str, timeout: float = 1.0) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (OSError, ValueError):
        return None


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def process_name(pid: int) -> str:
    """Executable name of a live process, or "" when it is gone."""
    if WINDOWS:  # never os.kill(pid, 0) here: on Windows it terminates the process
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True
        ).stdout.strip()
        return out.split(",")[0].strip('"') if out.startswith('"') else ""
    return subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True).stdout.strip()


def is_process(pid: int | None, name: str) -> bool:
    """True if pid is alive and its executable contains name (guards against recycled PIDs)."""
    return bool(pid) and name in process_name(pid).lower()


def kill(pid: int, tree: bool) -> None:
    """Terminate pid; tree=True also takes what it started (uvicorn --reload workers, the venv launcher)."""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/F", *(["/T"] if tree else [])], capture_output=True)
        return
    send = os.killpg if tree else os.kill  # start_new_session=True made our server a group leader
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            send(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        for _ in range(10):
            if not process_name(pid):
                return
            time.sleep(0.1)


def listening_pids(port: int) -> set[int]:
    if WINDOWS:
        pids = set()
        for line in subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout.splitlines():
            parts = line.split()
            if len(parts) == 5 and parts[0] == "TCP" and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                pids.add(int(parts[4]))
        return pids - {0}
    if not shutil.which("lsof"):
        return set()
    out = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
    return {int(pid) for pid in out.split()}


def spawn_background(cmd: list[str], log_path: Path, env: dict[str, str] | None = None) -> subprocess.Popen:
    """Start cmd detached from this terminal, writing its output to log_path."""
    RUN_DIR.mkdir(exist_ok=True)
    options: dict[str, Any] = {"cwd": ROOT, "env": env, "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
    if WINDOWS:
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    with open(log_path, "wb") as log:
        return subprocess.Popen(cmd, stdout=log, **options)


def ensure_frontend() -> None:
    frontend = ROOT / "frontend"
    if (frontend / "dist" / "index.html").exists():
        return
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found: install Node.js 18+ (the UI at / is served from frontend/dist).")
    print("Frontend build not found; building it with Vite...")
    if not (frontend / "node_modules").is_dir():
        subprocess.run([npm, "ci" if (frontend / "package-lock.json").exists() else "install"], cwd=frontend, check=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)


def ensure_ollama() -> None:
    base = setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = setting("OLLAMA_MODEL", "qwen3:4b")
    exe = shutil.which("ollama")
    tags = http_json(f"{base}/api/tags", timeout=2)
    if tags is None:
        if not exe:
            print("LLM_PROVIDER=ollama, but 'ollama' is not on PATH: install it from https://ollama.com")
            return
        print(f"Starting Ollama ({base}) in the background...")
        OLLAMA_PID_FILE.write_text(str(spawn_background([exe, "serve"], RUN_DIR / "ollama.log").pid))
        for _ in range(20):
            tags = http_json(f"{base}/api/tags", timeout=1)
            if tags is not None:
                break
            time.sleep(0.5)
    if tags is not None and exe and not any(model in m.get("name", "") for m in tags.get("models", [])):
        print(f"Pulling Ollama model {model}...")
        subprocess.run([exe, "pull", model], check=False)


def start(args: argparse.Namespace) -> int:
    if not VENV_PYTHON.exists():
        print("Missing .venv. Set it up first:\n  uv venv --python 3.12\n  uv pip install -r requirements.txt")
        return 1
    if not (ROOT / ".env").exists() and (ROOT / ".env.example").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("Created .env from .env.example; add the server-side AssemblyAI key before using live speech.")
    ensure_frontend()

    url = f"http://127.0.0.1:{args.port}"
    pid = read_pid(PID_FILE)
    if is_process(pid, "python"):
        print(f"The Lantern is already running (PID {pid}): {url}/")
        return 0
    PID_FILE.unlink(missing_ok=True)
    if port_open(args.port):
        print(f"Port {args.port} is already in use. Free it with: uv run python scripts/dev.py stop")
        return 1

    provider = setting("LLM_PROVIDER", "ollama")
    if provider == "ollama":
        ensure_ollama()
    cmd = [str(VENV_PYTHON), "-m", "uvicorn", "backend.app.main:app", "--host", args.host, "--port", str(args.port)]
    if (ROOT / ".env").exists():
        cmd.extend(["--env-file", str(ROOT / ".env")])
    if args.reload:
        cmd.append("--reload")
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"}  # server logs contain emoji
    # Hand the server the same provider this preflight used, so the two can't disagree.
    env["LLM_PROVIDER"] = provider
    env["OLLAMA_MODEL"] = setting("OLLAMA_MODEL", "qwen3:4b")
    env["OLLAMA_BASE_URL"] = setting("OLLAMA_BASE_URL", "http://localhost:11434")

    if args.foreground:
        print(f"Serving {url}/  (Ctrl+C stops)")
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            return proc.wait()

    proc = spawn_background(cmd, LOG_FILE, env)
    PID_FILE.write_text(str(proc.pid))
    print("Starting The Lantern", end="", flush=True)
    deadline = time.monotonic() + 30  # the first start warms local Qwen and Kokoro providers
    while time.monotonic() < deadline and proc.poll() is None:
        health = http_json(f"{url}/health")
        if health is not None:
            ready = http_json(f"{url}/ready") or {}
            providers = ready.get("providers") or {}
            state = ready.get("provider_state") or {}
            model = providers.get("llm") or env["OLLAMA_MODEL"]
            llm_status = "warm" if state.get("llm_warm") else "not ready"
            tts_status = "warm" if state.get("tts_warm") else "not ready"
            print(
                f" OK\n  Web UI: {url}/\n  Health: {url}/health\n  Ready:  {url}/ready\n"
                f"  LLM:    {provider} ({model}, {llm_status})\n  TTS:    {providers.get('tts', '?')} ({tts_status})\n"
                f"  Logs:   {LOG_FILE.relative_to(ROOT)}\n  Stop:   uv run python scripts/dev.py stop"
            )
            return 0
        print(".", end="", flush=True)
        time.sleep(0.5)
    print(" FAILED. Last log lines:")
    print("\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]))
    kill(proc.pid, tree=True)
    PID_FILE.unlink(missing_ok=True)
    return 1


def stop(args: argparse.Namespace) -> int:
    stopped = False
    pid = read_pid(PID_FILE)
    if is_process(pid, "python"):
        kill(pid, tree=True)
        print(f"Stopped the server (PID {pid})")
        stopped = True
    PID_FILE.unlink(missing_ok=True)
    for port, only_node in ((args.port, False), (VITE_PORT, True)):
        holders = [p for p in listening_pids(port) if not only_node or is_process(p, "node")]
        for holder in holders:
            kill(holder, tree=False)
        if holders:
            print(f"Freed port {port}")
            stopped = True
    ollama = read_pid(OLLAMA_PID_FILE)
    if is_process(ollama, "ollama"):
        kill(ollama, tree=False)
        print(f"Stopped the project-managed Ollama (PID {ollama})")
        stopped = True
    OLLAMA_PID_FILE.unlink(missing_ok=True)
    if not stopped:
        print(f"No running server found on port {args.port}.")
    return 0


def status(args: argparse.Namespace) -> int:
    url = f"http://127.0.0.1:{args.port}"
    pid = read_pid(PID_FILE)
    if is_process(pid, "python"):
        print(f"RUNNING (PID {pid}): {url}/")
    elif port_open(args.port):
        print(f"Port {args.port} is in use by a process without a PID file (--foreground or started elsewhere).")
    else:
        print(f"NOT running on port {args.port}. Start it with: uv run python scripts/dev.py start")
    health = http_json(f"{url}/health")
    if health:
        ready = http_json(f"{url}/ready") or {}
        print(f"  service={health.get('service')}  ready={ready.get('ready', False)}")
        print(f"  providers={ready.get('providers', {})}  state={ready.get('provider_state', {})}")
    base = setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    tags = http_json(f"{base}/api/tags", timeout=2)
    if tags is None:
        print(f"Ollama: not running at {base}")
    else:
        names = ", ".join(m.get("name", "?") for m in tags.get("models", [])) or "none"
        print(f"Ollama: running at {base}; models: {names}")
    return 0


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with npm output when piped
    parser = argparse.ArgumentParser(description="Start, stop or check The Lantern on any OS.")
    parser.add_argument("command", choices=("start", "stop", "status", "restart"))
    parser.add_argument("--foreground", action="store_true", help="start: stay attached; Ctrl+C stops the server")
    parser.add_argument("--reload", action="store_true", help="start: restart uvicorn when code changes")
    parser.add_argument("--host", default=os.environ.get("APP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT", "8000")))
    args = parser.parse_args()
    try:
        if args.command == "restart":
            stop(args)
            return start(args)
        return {"start": start, "stop": stop, "status": status}[args.command](args)
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}: {' '.join(map(str, exc.cmd))}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
