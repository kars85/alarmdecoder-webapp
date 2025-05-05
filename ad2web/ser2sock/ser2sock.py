# ad2web/ser2sock/ser2sock.py
"""
Cross-platform control utilities for the ser2sock daemon.
Provides functions to detect, reload, stop, and update configuration of the ser2sock process.
"""
import platform
import subprocess
import logging

logger = logging.getLogger(__name__)

# Attempt to import `sh` for Unix-like convenience; fallback to None on unsupported platforms
try:
    import sh
except ImportError:
    sh = None


def exists() -> bool:
    """
    Return True if the ser2sock process is currently running.
    Uses `sh.pgrep` on Unix if available, or falls back to subprocess-based checks.
    On Windows, uses `tasklist` to detect the running executable.
    """
    if sh:
        try:
            sh.pgrep("ser2sock")
            return True
        except sh.ErrorReturnCode:
            return False

    system = platform.system()
    if system == "Windows":
        try:
            output = subprocess.check_output(["tasklist"], text=True)
            return "ser2sock.exe" in output
        except Exception:
            return False
    else:
        # Unix fallback: pgrep
        try:
            subprocess.run(
                ["pgrep", "ser2sock"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False


def hup() -> None:
    """
    Send SIGHUP to the ser2sock daemon to reload its configuration without stopping it.
    Uses `sh.kill` if available; otherwise, invokes `pkill -HUP ser2sock` on Unix.
    """
    if sh:
        try:
            sh.kill("ser2sock", "-HUP")
            return
        except Exception as e:
            logger.error(f"Failed to send HUP via sh: {e}")

    # Fallback implementation for non-Windows
    if platform.system() != "Windows":
        try:
            subprocess.run(["pkill", "-HUP", "ser2sock"], check=True)
        except Exception as e:
            logger.error(f"Failed to send HUP via subprocess: {e}")


def stop() -> None:
    """
    Terminate the ser2sock daemon process.
    Uses `sh.kill` if available; on Windows, uses `taskkill`, otherwise `pkill`.
    """
    if sh:
        try:
            sh.kill("ser2sock", "-TERM")
            return
        except Exception as e:
            logger.error(f"Failed to stop ser2sock via sh: {e}")

    system = platform.system()
    if system == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "ser2sock.exe"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to stop ser2sock via taskkill: {e}")
    else:
        try:
            subprocess.run(["pkill", "ser2sock"], check=True)
        except Exception as e:
            logger.error(f"Failed to stop ser2sock via subprocess: {e}")


def update_config(config_path: str) -> None:
    """
    Update the ser2sock configuration file at `config_path`.
    After updating the file, signal the running daemon to reload by sending a SIGHUP.

    Note:
        - This function does not stop and restart the daemon, only reloads.
        - For a full restart, call `stop()` then `start()` in your orchestration logic.
    """
    config_file = f"{config_path.rstrip('/')}/ser2sock.cfg"
    try:
        # Example: rewrite configuration file in place
        # (Implement actual config serialization logic here)
        with open(config_file, 'w', encoding='utf-8') as fp:
            # Placeholder: write default or templated configuration
            fp.write(f"# ser2sock configuration updated at {__import__('time').ctime()}\n")
        # Signal the daemon to reload
        hup()
    except Exception as e:
        logger.error(f"Failed to update ser2sock config at {config_file}: {e}")
        raise
