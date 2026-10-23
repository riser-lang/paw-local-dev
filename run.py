"""PAW Desktop Local Edition - Application Entrypoint.

Starts FastAPI gateway on 127.0.0.1:8800 (or next available port) and automatically opens the browser.
Supports:
- Windows Native Single-Instance Mutex (CreateMutexW)
- Port collision auto-detection & auto-shift (8800 -> 8801 -> ...)
"""
from __future__ import annotations

import os as _paw_os
if _paw_os.environ.get("PAW_NO_BROWSER") == "1":
    import webbrowser as _paw_browser
    _paw_browser.open = lambda *args, **kwargs: False

import ctypes
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Add project root, backend, and runtime to sys.path
if getattr(sys, "frozen", False):
    base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    base_dir = Path(__file__).resolve().parent

root_dir = base_dir
backend_dir = base_dir / "backend"
runtime_dir = backend_dir / "runtime"
runtime_core_dir = runtime_dir / "core"

for d in [base_dir, backend_dir, runtime_dir, runtime_core_dir]:
    s = str(d)
    if s not in sys.path:
        sys.path.insert(0, s)

# Safe stream redirection for windowless/headless desktop mode (console=False)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")

# Reader children do not initialize configuration, a database, the desktop
# tray, or a gateway. This also prevents late workers recreating cleared data.
if __name__ == '__main__':
    _worker_args = None
    if len(sys.argv) > 1 and sys.argv[1] == '--reader-worker':
        _worker_args = sys.argv[2:]
    elif len(sys.argv) > 2 and sys.argv[1:3] == ['-m', 'backend.reader.subprocess_worker']:
        _worker_args = sys.argv[3:]
    if _worker_args is not None:
        from backend.reader.subprocess_worker import main as _reader_worker_main
        sys.exit(_reader_worker_main(_worker_args))

from backend.core.startup_trace import event as trace_startup
trace_startup('runtime_import')
import uvicorn
from backend.core.config import settings
# The desktop build has no console, so scan/cruise diagnostics are also written
# to %LOCALAPPDATA%\PAW\logs with the secret-redaction filter installed.
try:
    from backend.core.log_setup import ensure_file_logging
    ensure_file_logging()
except Exception:
    pass
from backend.tray.tray_app import PawSystemTray
trace_startup('runtime_loaded')

MUTEX_NAME = "PAW_Personal_Work_OS_SingleInstance_Mutex_v2"
ERROR_ALREADY_EXISTS = 183


def find_available_port(host: str, start_port: int, max_attempts: int = 30) -> int:
    """Finds an open port starting from start_port."""
    for p in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return start_port


def open_browser(port: int):
    time.sleep(1.2)
    url = f"http://127.0.0.1:{port}/app"
    print(f"\n[PAW Desktop] Opening web browser at: {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def acquire_single_instance_mutex():
    """Acquires a Windows mutex to ensure only one PAW instance is running."""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_error = kernel32.GetLastError()
        if last_error == ERROR_ALREADY_EXISTS:
            trace_startup('existing_instance')
            print("\n" + "=" * 65)
            print(" [PAW Desktop 提示] PAW Personal Work OS 已在后台运行中。")
            print(" 正在为您自动唤起现有工作台界面...")
            print("=" * 65 + "\n")
            # Try to open existing browser port
            for port in range(settings.port, settings.port + 10):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    if s.connect_ex(("127.0.0.1", port)) == 0:
                        try:
                            webbrowser.open(f"http://127.0.0.1:{port}/app")
                        except Exception:
                            pass
                        break
            sys.exit(0)
        return mutex
    return None


def main():
    # 0. Subprocess Worker Dispatch (Requirement F2: Subprocess isolation in frozen runtime)
    if len(sys.argv) > 1 and sys.argv[1] == "--reader-worker":
        from backend.reader.subprocess_worker import main as worker_main
        sys.exit(worker_main(sys.argv[2:]))
    if len(sys.argv) > 2 and sys.argv[1:3] == ["-m", "backend.reader.subprocess_worker"]:
        from backend.reader.subprocess_worker import main as worker_main
        sys.exit(worker_main(sys.argv[3:]))

    trace_startup('mutex_acquire')
    mutex_handle = acquire_single_instance_mutex()
    trace_startup('mutex_acquired')

    # Detect port availability and auto-shift if occupied
    actual_port = find_available_port(settings.host, settings.port)
    trace_startup('port_selected',port=actual_port,requested_port=settings.port)
    if actual_port != settings.port:
        print(f"\n[PAW Desktop 提示] 端口 {settings.port} 已被占用，已自动顺延切换至可用端口: {actual_port}\n")
        settings.port = actual_port

    print("=" * 65)
    print("       PAW Personal Work OS v2.5.0 (Windows 本地化版)")
    print("       Evidence-First + Human-Governed Personal AI Workspace")
    print("=" * 65)
    print(f"Server URL:   http://{settings.host}:{settings.port}/app")
    print(f"Data Dir:     {settings.data_dir}")
    print(f"Sources Dir:  {settings.work_dir}")
    print("Press Ctrl+C to terminate.")
    print("=" * 65)

    threading.Thread(target=open_browser, args=(settings.port,), daemon=True).start()

    config = uvicorn.Config(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)

    def _on_tray_exit():
        server.should_exit = True

    try:
        trace_startup('backend_load')
        from backend.main import scanner
        trace_startup('backend_loaded')
        tray = PawSystemTray(port=settings.port, on_exit=_on_tray_exit, scanner=scanner)
        trace_startup('tray_start')
        tray.start()
        trace_startup('tray_started')
    except Exception as exc:
        trace_startup('tray_error',error_type=type(exc).__name__)
        tray = None

    try:
        trace_startup('server_run')
        server.run()
    finally:
        trace_startup('server_stopped')
        if tray:
            tray.stop()
        if mutex_handle and sys.platform == "win32":
            ctypes.windll.kernel32.CloseHandle(mutex_handle)



if __name__ == "__main__":
    main()
