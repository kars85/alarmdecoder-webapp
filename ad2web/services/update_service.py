import threading
import time
import datetime
import json
from flask import current_app
from ad2web.settings.models import Setting
from ad2web.extensions import db
from alarmdecoder.util import Firmware  # AlarmDecoder library's Firmware utility
from ad2web.updater.models import FirmwareUpdater  # Existing FirmwareUpdater class for device firmware updates

class VersionChecker(threading.Thread):
    """
    Background thread that periodically checks for available software updates
    for the webapp (and the AlarmDecoder device firmware).
    Updates global flags to reflect update availability.
    """
    TIMEOUT = 60  # Base sleep interval in seconds

    def __init__(self, decoder):
        super().__init__()
        self._decoder = decoder
        self._updater = decoder.updater
        self._running = False
        # Load last check time, interval, and disable flag from settings
        try:
            self.last_check_time = float(Setting.get_by_name('version_checker_last_check_time', default=0).value)
        except Exception:
            self.last_check_time = 0.0
        try:
            self.version_checker_timeout = int(Setting.get_by_name('version_checker_timeout', default=600).value)
        except Exception:
            self.version_checker_timeout = 600
        disable_val = Setting.get_by_name('version_checker_disable', default=False).value
        if isinstance(disable_val, str):
            disable_val = True if disable_val.lower() in ('true', '1', 'yes') else False
        self.disable_version_checker = bool(disable_val)

    def stop(self):
        """Signal the thread to stop its loop."""
        self._running = False

    def setTimeout(self, timeout):
        """Update the version check interval (seconds)."""
        try:
            timeout = int(timeout)
        except (ValueError, TypeError):
            return
        current_app.logger.info(f"Updating version check interval to {timeout} seconds")
        self.version_checker_timeout = timeout

    def setDisable(self, disable):
        """Enable or disable the periodic version checks."""
        current_app.logger.info("Version checks are now {}".format("disabled" if disable else "enabled"))
        self.disable_version_checker = bool(disable)

    def run(self):
        """Main loop: perform update checks at the configured interval if not disabled."""
        self._running = True
        while self._running:
            with current_app.app_context():
                if not self.disable_version_checker:
                    try:
                        now = time.time()
                        if now > self.last_check_time + self.version_checker_timeout:
                            last_check_str = datetime.datetime.fromtimestamp(self.last_check_time).strftime('%m-%d-%Y %H:%M:%S')
                            current_app.logger.info(f"Performing update check (last check was {last_check_str})")
                            # Check for webapp and library updates
                            self._decoder.updates = self._updater.check_updates()
                            update_available = any(val[0] for val in self._decoder.updates.values())
                            current_app.jinja_env.globals['update_available'] = update_available
                            # Check for device firmware update availability
                            firmware_needed = self._updater.check_firmware()
                            current_app.jinja_env.globals['firmware_update_available'] = firmware_needed
                            # Update last check time (persist to DB)
                            self.last_check_time = now
                            setting_obj = Setting.get_by_name('version_checker_last_check_time')
                            setting_obj.value = int(now)
                            db.session.add(setting_obj)
                            db.session.commit()
                    except Exception as err:
                        current_app.logger.error(f"Error during version check: {err}", exc_info=True)
                time.sleep(self.TIMEOUT)

class UpdaterService:
    """Service layer for application updates and firmware updates."""
    FIRMWARE_JSON_URL = "http://www.alarmdecoder.com/firmware.json"

    _version_thread = None
    _firmware_thread = None

    @classmethod
    def start_version_checker(cls):
        """Start the background VersionChecker thread if not already running."""
        if cls._version_thread is None or not cls._version_thread.is_alive():
            cls._version_thread = VersionChecker(current_app.decoder)
            cls._version_thread.daemon = True
            cls._version_thread.start()
            current_app.logger.info("VersionChecker thread started.")

    @classmethod
    def stop_version_checker(cls):
        """Stop the VersionChecker thread."""
        if cls._version_thread is not None:
            cls._version_thread.stop()
            try:
                cls._version_thread.join(timeout=5.0)
            except Exception as e:
                current_app.logger.warning(f"VersionChecker thread join timeout: {e}")
            cls._version_thread = None

    @classmethod
    def refresh_version_check_settings(cls):
        """Reload timeout/disable settings for the running VersionChecker thread."""
        if cls._version_thread and cls._version_thread.is_alive():
            try:
                new_timeout = int(Setting.get_by_name('version_checker_timeout', default=3600).value)
                disable_val = Setting.get_by_name('version_checker_disable', default=False).value
                if isinstance(disable_val, str):
                    new_disable = True if disable_val.lower() in ('true', '1', 'yes') else False
                else:
                    new_disable = bool(disable_val)
                cls._version_thread.setTimeout(new_timeout)
                cls._version_thread.setDisable(new_disable)
                current_app.logger.info("VersionChecker thread settings updated.")
            except Exception as e:
                current_app.logger.error(f"Error refreshing VersionChecker settings: {e}", exc_info=True)

    @staticmethod
    def check_updates():
        """
        Perform an on-demand check for software updates (AlarmDecoderWebapp and library).
        Returns a dict of components with their update status.
        """
        updates = current_app.decoder.updater.check_updates()
        update_available = any(val[0] for val in updates.values())
        current_app.jinja_env.globals['update_available'] = update_available
        return updates

    @staticmethod
    def perform_update(component=None):
        """
        Execute update for the specified component (or all if None).
        Returns a result dictionary per component updated.
        """
        return current_app.decoder.updater.update(component)

    @staticmethod
    def firmware_version_info():
        """
        Retrieve the current device firmware version and the latest stable firmware version.
        Returns a dict with 'current', 'latest', and 'status' fields.
        """
        device = current_app.decoder.device
        current_version = None
        if device and getattr(device, 'version_number', None):
            ver = device.version_number
            # Strip leading non-numeric char (e.g. 'L' indicating locked firmware)
            current_version = ver[1:] if ver and not ver[0].isdigit() else ver
        latest_version = None
        try:
            from urllib import request as urlrequest
            with urlrequest.urlopen(UpdaterService.FIRMWARE_JSON_URL, timeout=5) as resp:
                data = json.load(resp)
                for fw in data.get('firmware', []):
                    if fw.get('tag') == 'Stable':
                        latest_version = fw.get('version')
                        break
        except Exception as e:
            current_app.logger.warning(f"Could not fetch latest firmware info: {e}")
        # Determine status message
        status = "Unknown"
        if current_version and latest_version:
            status = "Up-to-date" if current_version == latest_version else "Update available"
        elif current_version and not latest_version:
            status = "Latest version unknown"
        return {
            "current": current_version or "N/A",
            "latest": latest_version or "N/A",
            "status": status
        }

    @classmethod
    def start_firmware_update(cls, file_path, length):
        """
        Launch a background thread to perform an AlarmDecoder device firmware update.
        Returns True if the update thread started, or False if an update is already in progress.
        """
        if cls._firmware_thread and cls._firmware_thread.is_alive():
            current_app.logger.warning("Firmware update already in progress; new request ignored.")
            return False
        app_ctx = current_app._get_current_object()

        def firmware_update_task(app_context, path, line_count):
            with app_context.app_context():
                reopen_with_reader = False
                try:
                    device = current_app.decoder.device
                    # Backup current device config (if supported) to restore after update
                    enable_reconfiguring = False
                    orig_config_string = ""
                    if device and callable(getattr(device, 'get_config_string', None)):
                        enable_reconfiguring = True
                        orig_config_string = device.get_config_string() or ""
                    current_app.logger.info(f"Beginning firmware update (file: {path})")
                    firmware_updater = FirmwareUpdater(filename=path, length=line_count)
                    firmware_updater.update()
                    if getattr(firmware_updater, 'completed', False):
                        if enable_reconfiguring:
                            current_app.decoder.broadcast('firmwareupload', {"stage": "STAGE_CONFIGURE"})
                            time.sleep(10)  # Wait for device to reboot into config mode
                            if orig_config_string:
                                try:
                                    device.send(f"C{orig_config_string}\r")
                                except Exception as err:
                                    current_app.logger.error(f"Error restoring device config: {err}")
                            time.sleep(5)
                        # Mark firmware update availability as handled
                        current_app.jinja_env.globals['firmware_update_available'] = False
                        current_app.decoder.broadcast('firmwareupload', {"stage": "STAGE_FINISHED"})
                        reopen_with_reader = True
                        # Clear firmware file info
                        current_app.decoder.firmware_file = None
                        current_app.decoder.firmware_length = -1
                except Exception as e:
                    current_app.logger.error(f"Error during firmware update: {e}", exc_info=True)
                    current_app.decoder.broadcast('firmwareupload', {"stage": "STAGE_ERROR", "error": "Error uploading firmware."})
                finally:
                    # Reinitialize device connection after update
                    try:
                        current_app.decoder.close()
                    except Exception as err:
                        current_app.logger.error(f"Error closing device after firmware update: {err}", exc_info=True)
                    try:
                        current_app.decoder.open(no_reader_thread=not reopen_with_reader)
                    except Exception as err:
                        current_app.logger.error(f"Error reopening device after firmware update: {err}", exc_info=True)

        cls._firmware_thread = threading.Thread(target=firmware_update_task, args=(app_ctx, file_path, length))
        cls._firmware_thread.daemon = True
        cls._firmware_thread.start()
        return True
