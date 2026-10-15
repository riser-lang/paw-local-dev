"""PAW Desktop Local Edition - Core Configuration.

Centralizes paths, database locations, provider settings, runtime configs,
and enforces Requirements 4.2 (DPAPI key encryption) and 6.2.3 (Data/Program separation).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.security import decrypt_secret, encrypt_secret

logger = logging.getLogger("paw.config")

# Base directory: where the application bundle or repo resides
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# ── Data and Program Separation (Requirement 6.2.3) ──────────────────────────
# If PAW_DATA_DIR is explicitly configured via environment variable, use it.
# If running inside a PyInstaller frozen application, use %LOCALAPPDATA%\PAW.
# If running in portable dev mode, use BASE_DIR / "data".
if os.getenv("PAW_DATA_DIR"):
    DATA_DIR = Path(os.environ["PAW_DATA_DIR"]).resolve()
elif getattr(sys, "frozen", False) and sys.platform == "win32":
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    DATA_DIR = Path(local_app_data) / "PAW"
    # Custom data directory override: pointer file kept at the default
    # bootstrap location so it is always found regardless of the active dir.
    _pointer_file = DATA_DIR / "data_dir_pointer.txt"
    if _pointer_file.is_file():
        try:
            _custom_dir = _pointer_file.read_text(encoding="utf-8").strip()
        except Exception:
            _custom_dir = ""
        if _custom_dir and Path(_custom_dir).is_dir():
            DATA_DIR = Path(_custom_dir)
else:
    DATA_DIR = BASE_DIR / "data"

SOURCES_DIR = DATA_DIR / "sources"
EVIDENCE_DIR = DATA_DIR / "evidence"
BACKUPS_DIR = DATA_DIR / "backups"
BACKUP_DIR = BACKUPS_DIR
LOGS_DIR = DATA_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"

# Default SQLite database path
DEFAULT_DB_PATH = DATA_DIR / "workspace.db"
DB_PATH = Path(os.getenv("PAW_WORKSPACE_DB", str(DEFAULT_DB_PATH)))

# Export paths so all sub-modules inherit them consistently
os.environ["PAW_WORKSPACE_DB"] = str(DB_PATH)
os.environ["PAW_SOURCES_DIR"] = str(SOURCES_DIR)
os.environ["PAW_EVIDENCE_DIR"] = str(EVIDENCE_DIR)
os.environ["PAW_DATA_DIR"] = str(DATA_DIR)

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
SOURCES_DIR.mkdir(parents=True, exist_ok=True)
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Copy clean template database on first run in data separation mode (Requirement F1)
candidate_templates = [
    BASE_DIR / "template" / "workspace_template.db",
    Path(sys.executable).parent / "_internal" / "template" / "workspace_template.db",
    Path(sys.executable).parent / "template" / "workspace_template.db",
    BASE_DIR / "data" / "workspace_template.db",
]
template_db = None
for p in candidate_templates:
    if p.is_file():
        template_db = p
        break

if template_db and not DEFAULT_DB_PATH.exists():
    try:
        shutil.copy2(template_db, DEFAULT_DB_PATH)
        logger.info(f"Initialized new workspace database from clean template: {template_db}")
    except Exception as e:
        logger.warning(f"Failed to copy template workspace.db: {e}")


class Settings:
    def __init__(self) -> None:
        self.host: str = os.getenv("PAW_HOST", "127.0.0.1")
        self.port: int = int(os.getenv("PAW_PORT", "8800"))
        
        # Provider selection: "deepseek" | "qwen" | "openai" | "custom"
        self.provider: str = "deepseek"
        
        # Plaintext keys in memory (encrypted with DPAPI on disk)
        self.deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
        self.deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        
        self.qwen_api_key: str = os.getenv("DASHSCOPE_API_KEY", "")
        self.qwen_base_url: str = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.qwen_model: str = os.getenv("DASHSCOPE_MODEL", "qwen-plus")

        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")

        self.custom_api_key: str = os.getenv("CUSTOM_API_KEY", "")
        self.custom_base_url: str = os.getenv("CUSTOM_BASE_URL", "http://127.0.0.1:11434/v1")
        self.custom_model: str = os.getenv("CUSTOM_MODEL", "llama3")

        self.claude_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
        self.claude_base_url: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.claude_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")

        self.watch_enabled: bool = True
        self.auto_propose: bool = True  # Automatically generate candidates on file arrival
        
        # Scanning & resource control (Requirement 3.2.4 & 3.2.10)
        self.work_dir: str = str(SOURCES_DIR)
        self.onboarding_completed: bool = False
        self.cruise_mode: str = "realtime"  # "realtime" | "hourly" | "daily" | "manual"
        self.cruise_interval: int = 3600
        self.speed_mode: str = "standard"  # "quiet" (low priority) | "standard" | "fast"
        self.max_file_size_mb: int = 100
        self.excluded_dirs: List[str] = [".git", "node_modules", "$RECYCLE.BIN", "System Volume Information", "__pycache__"]

        # Usage telemetry & R1 Daily Call Limit
        self.cumulative_calls: int = 0
        self.cumulative_tokens: int = 0
        self.data_dir: str = str(DATA_DIR)
        self.daily_call_limit: int = 0  # 0 = unlimited (default)
        self.today_calls_made: int = 0
        self.last_call_date: str = ""

        # Web Search Capabilities (§3.4.7: default enabled, depends on model support)
        self.web_search_enabled: bool = True
        self.deepseek_web_search: bool = True
        self.qwen_web_search: bool = True
        self.openai_web_search: bool = False
        self.custom_web_search: bool = True

        # R2 Built-in Web Search Capabilities (Specification §3.1)
        # web_search_mode: "auto" | "native_only" | "software_only" | "disabled"
        self.web_search_mode: str = "auto"
        self.daily_search_limit: int = 50
        self.today_searches_made: int = 0
        self.last_search_date: str = ""
        self.search_proxy: str = ""

        self.load()

    def is_web_search_supported(self, provider: Optional[str] = None) -> bool:
        """Check if target provider natively supports online search parameters."""
        p = (provider or self.provider).lower()
        if p in ("qwen", "deepseek", "custom"):
            return True
        return False

    def is_active_web_search_enabled(self) -> bool:
        """Check if web search is enabled for the currently selected provider."""
        if not self.web_search_enabled:
            return False
        p = self.provider.lower()
        # Requirement F5: Only Qwen DashScope supports `enable_search: true` in OpenAI compatibility mode
        if p == "qwen":
            return bool(self.qwen_web_search)
        # DeepSeek official, OpenAI, Claude, Custom: Default unsupported
        return False

    def is_active_web_search_supported(self) -> bool:
        p = self.provider.lower()
        return p == "qwen" or "dashscope" in (self.get_active_credentials().get("base_url") or "")

    def check_and_increment_daily_call(self) -> bool:
        """Check if today's call limit has been reached, resetting count if on a new calendar day.
        
        0 or negative indicates no limit (unlimited calls).
        """
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.last_call_date != today:
            self.last_call_date = today
            self.today_calls_made = 0
        if self.daily_call_limit and self.daily_call_limit > 0:
            if self.today_calls_made >= self.daily_call_limit:
                return False
        self.today_calls_made += 1
        self.save()
        return True

    def check_and_increment_daily_search(self) -> bool:
        """Check if today's software search limit has been reached, resetting on new calendar day."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.last_search_date != today:
            self.last_search_date = today
            self.today_searches_made = 0
        if self.daily_search_limit and self.daily_search_limit > 0:
            if self.today_searches_made >= self.daily_search_limit:
                return False
        self.today_searches_made += 1
        self.save()
        return True


    def get_active_credentials(self) -> Dict[str, str]:
        """Return decrypted active credentials for the selected provider."""
        p = self.provider.lower()
        if p == "deepseek":
            return {"base_url": self.deepseek_base_url, "api_key": self.deepseek_api_key, "model": self.deepseek_model}
        elif p == "qwen":
            return {"base_url": self.qwen_base_url, "api_key": self.qwen_api_key, "model": self.qwen_model}
        elif p == "openai":
            return {"base_url": self.openai_base_url, "api_key": self.openai_api_key, "model": self.openai_model}
        elif p == "custom":
            return {"base_url": self.custom_base_url, "api_key": self.custom_api_key, "model": self.custom_model}
        elif p == "claude":
            # Requirement 4.1.3: Claude via OpenAI-compatible gateway
            return {"base_url": self.openai_base_url, "api_key": self.openai_api_key, "model": self.claude_model}
        return {"base_url": self.deepseek_base_url, "api_key": self.deepseek_api_key, "model": self.deepseek_model}

    def load(self) -> None:
        """Load settings from disk and decrypt encrypted sensitive fields."""
        # Also check legacy BASE_DIR / config.json if DATA_DIR / config.json does not exist yet
        target_cfg = CONFIG_FILE if CONFIG_FILE.is_file() else (BASE_DIR / "config.json")
        if target_cfg.is_file():
            try:
                data = json.loads(target_cfg.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(self, k):
                        if k.endswith("_api_key") and isinstance(v, str):
                            setattr(self, k, decrypt_secret(v))
                        else:
                            setattr(self, k, v)
            except Exception as e:
                logger.warning(f"Failed to load config from {target_cfg}: {e}")

    def save(self) -> None:
        """Save settings to disk, encrypting API keys with Windows DPAPI (Requirement 4.2.1)."""
        data = {
            "host": self.host,
            "port": self.port,
            "provider": self.provider,
            "deepseek_api_key": encrypt_secret(self.deepseek_api_key) if self.deepseek_api_key else "",
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "qwen_api_key": encrypt_secret(self.qwen_api_key) if self.qwen_api_key else "",
            "qwen_base_url": self.qwen_base_url,
            "qwen_model": self.qwen_model,
            "openai_api_key": encrypt_secret(self.openai_api_key) if self.openai_api_key else "",
            "openai_base_url": self.openai_base_url,
            "openai_model": self.openai_model,
            "custom_api_key": encrypt_secret(self.custom_api_key) if self.custom_api_key else "",
            "custom_base_url": self.custom_base_url,
            "custom_model": self.custom_model,
            "claude_api_key": encrypt_secret(self.claude_api_key) if self.claude_api_key else "",
            "claude_base_url": self.claude_base_url,
            "claude_model": self.claude_model,
            "watch_enabled": self.watch_enabled,
            "auto_propose": self.auto_propose,
            "work_dir": self.work_dir,
            "onboarding_completed": self.onboarding_completed,
            "cruise_mode": self.cruise_mode,
            "cruise_interval": self.cruise_interval,
            "speed_mode": self.speed_mode,
            "max_file_size_mb": self.max_file_size_mb,
            "excluded_dirs": self.excluded_dirs,
            "cumulative_calls": self.cumulative_calls,
            "cumulative_tokens": self.cumulative_tokens,
            "daily_call_limit": self.daily_call_limit,
            "today_calls_made": self.today_calls_made,
            "last_call_date": self.last_call_date,
            "web_search_enabled": self.web_search_enabled,
            "deepseek_web_search": self.deepseek_web_search,
            "qwen_web_search": self.qwen_web_search,
            "openai_web_search": self.openai_web_search,
            "custom_web_search": self.custom_web_search,
            "web_search_mode": self.web_search_mode,
            "daily_search_limit": self.daily_search_limit,
            "today_searches_made": self.today_searches_made,
            "last_search_date": self.last_search_date,
            "search_proxy": self.search_proxy,
        }
        CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")



def get_work_dirs() -> list:
    """Parse settings.work_dir into a list of directories.

    Supports semicolon-separated multiple directories (semicolon separated).
    Single-directory values remain fully compatible. Invalid/empty entries are dropped.
    """
    raw = settings.work_dir or ""
    dirs = []
    for part in raw.split(";"):
        p = part.strip()
        if p and p not in dirs:
            dirs.append(p)
    return dirs

settings = Settings()
