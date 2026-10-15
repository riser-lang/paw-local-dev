"""PAW Desktop System Tray Integration (Windows Native with Pystray).

Features:
- Native taskbar notification area icon with PAW emblem
- Interactive menu:
  - 打开工作台 (Open Web UI: http://127.0.0.1:{port}/app)
  - 暂停/恢复巡航 (Pause / Resume Scanner)
  - 系统健康自检 (Open Doctor: /app/doctor)
  - 安全退出 (Graceful Shutdown & State Flush)
- Headless, non-intrusive, zero console window needed
"""
from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Any, Callable, Optional

from PIL import Image, ImageDraw

logger = logging.getLogger("paw.tray")


def create_tray_image() -> Image.Image:
    """Generate a high-fidelity 64x64 PAW emblem icon."""
    width = 64
    height = 64
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Background rounded rectangle in PAW Primary Blue
    draw.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(24, 144, 255))
    # Inner emblem 'P'
    draw.rectangle([18, 16, 26, 48], fill=(255, 255, 255))
    draw.rounded_rectangle([26, 16, 44, 34], radius=6, fill=(255, 255, 255))
    draw.rounded_rectangle([26, 22, 38, 28], radius=3, fill=(24, 144, 255))
    return image


class PawSystemTray:
    def __init__(
        self,
        port: int,
        on_exit: Optional[Callable[[], None]] = None,
        scanner: Optional[Any] = None,
    ) -> None:
        self.port = port
        self.on_exit_cb = on_exit
        self.scanner = scanner
        self.icon = None
        self._thread: Optional[threading.Thread] = None

    def _open_app(self, icon=None, item=None):
        url = f"http://127.0.0.1:{self.port}/app"
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning(f"Failed to open browser: {e}")

    def _open_doctor(self, icon=None, item=None):
        url = f"http://127.0.0.1:{self.port}/app/doctor"
        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning(f"Failed to open doctor: {e}")

    def _toggle_cruise(self, icon=None, item=None):
        if not self.scanner:
            return
        status = getattr(self.scanner, "status", "")
        if status in ("phase1_ingesting", "phase2_analyzing", "scanning"):
            self.scanner.pause_scan()
            if self.icon:
                self.icon.notify("PAW 文件巡航已暂停", title="PAW 巡航控制")
        elif status == "paused":
            self.scanner.resume_scan()
            if self.icon:
                self.icon.notify("PAW 文件巡航已恢复继续", title="PAW 巡航控制")

    def _safe_exit(self, icon=None, item=None):
        logger.info("[Tray] Safe exit triggered by user from system tray menu.")
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        if self.on_exit_cb:
            self.on_exit_cb()

    def start(self) -> None:
        """Start system tray icon in a dedicated background thread."""
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("打开工作台 (&O)", self._open_app, default=True),
                pystray.MenuItem("暂停 / 恢复巡航 (&C)", self._toggle_cruise),
                pystray.MenuItem("系统健康自检 (&D)", self._open_doctor),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("安全退出 (&X)", self._safe_exit),
            )

            self.icon = pystray.Icon(
                "PAW_Personal_Work_OS",
                create_tray_image(),
                "PAW Personal Work OS (运行中)",
                menu=menu,
            )

            self._thread = threading.Thread(target=self.icon.run, daemon=True, name="PawTrayWorker")
            self._thread.start()
            logger.info("[Tray] Native Windows system tray started successfully.")
        except Exception as e:
            logger.warning(f"[Tray] System tray could not be initialized: {e}")

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
