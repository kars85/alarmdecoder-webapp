import os
import io
import time
import tarfile
import threading
from datetime import datetime

from flask import current_app
from ad2web.extensions import db
from ad2web.settings.models import Setting
from ad2web.settings import constants as settings_constants
from ad2web.utils.path_utils import tar_add_directory, tar_add_textfile

class BackupService:
    """
    Service for managing backups of the AlarmDecoder webapp settings/data.
    Handles generating export archives and scheduling periodic backups.
    """
    DEFAULT_PREFIX = 'alarmdecoder-export'
    CHECK_INTERVAL = 600  # 10 minutes between schedule checks

    def __init__(self, app, notification_service=None):
        """
        Initialize the backup service. Loads configuration from settings and prepares for scheduled backups.
        If a NotificationService is provided, it will be used to send backup emails.
        """
        self.app = app
        self.notification_service = notification_service
        # Load backup-related settings from the database
        with app.app_context():
            self.export_frequency = int(Setting.get_by_name('export_frequency', default=0).value)         # seconds between backups (0 = disabled)
            self.local_storage = Setting.get_by_name('enable_local_file_storage', default=False).value   # whether to keep local copies
            self.export_path = Setting.get_by_name('export_local_path', default=os.path.join(app.instance_path, 'exports')).value
            self.email_enable = Setting.get_by_name('export_email_enable', default=False).value          # whether to email backups
            self.recipients = []  # list of email recipients for backups
            to_addr = Setting.get_by_name('export_mailer_to', default=None).value
            if to_addr:
                self.recipients.append(to_addr)
            self.days_to_keep = int(Setting.get_by_name('days_to_keep', default=7).value)                # retention period for old files (days)
            last_check = Setting.get_by_name('export_last_check_time', default=0).value
            self.last_check_time = int(last_check) if last_check else 0

            # Ensure the export directory exists if local storage is enabled or for temp use
            if self.export_path and not os.path.isdir(self.export_path):
                os.makedirs(self.export_path, exist_ok=True)

        self._thread = None
        self._running = False
        self._first_run = True  # skip actual backup on the very first run to avoid immediate backup at startup

    def start(self):
        """Start the background backup scheduler thread if scheduling is enabled."""
        if not self._running and self.export_frequency > 0:
            self._running = True
            self._thread = threading.Thread(target=self._schedule_loop, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop the background backup thread."""
        self._running = False
        if self._thread is not None:
            try:
                self._thread.join(timeout=5)
            except RuntimeError:
                pass
            self._thread = None

    def backup_now(self):
        """
        Perform an on-demand backup immediately. Returns the path to the generated backup file.
        This method can be used to trigger a manual backup (e.g., via an API or admin action).
        """
        with self.app.app_context():
            backup_file = self._create_backup_file()
            if backup_file and self.email_enable and self.notification_service:
                # Send the backup via email
                subject = "AlarmDecoder Settings Backup"
                body = "Attached is the latest AlarmDecoder settings backup."
                try:
                    self.notification_service.send_email(self.recipients, subject, body, files=[backup_file])
                    current_app.logger.info(f"Backup emailed to {self.recipients}: {os.path.basename(backup_file)}")
                except Exception as err:
                    current_app.logger.error(f"Failed to email backup: {err}", exc_info=True)
            # If not retaining locally, remove the file after emailing
            if backup_file and not self.local_storage:
                try:
                    os.remove(backup_file)
                except Exception as e:
                    current_app.logger.warning(f"Could not remove backup file {backup_file}: {e}")
            return backup_file

    def _schedule_loop(self):
        """Background loop to periodically trigger backups based on the configured frequency."""
        while self._running:
            with self.app.app_context():
                if self.export_frequency > 0:
                    try:
                        current_time = time.time()
                        if current_time > self.last_check_time + self.export_frequency:
                            current_app.logger.info("Checking if scheduled backup is due...")
                            if not self._first_run:
                                # Time to perform a scheduled backup
                                backup_file = self._create_backup_file()
                                if backup_file:
                                    # If email alerts are enabled for backups, send the file
                                    if self.email_enable and self.notification_service:
                                        subject = "AlarmDecoder Settings Database Backup"
                                        body = "Automated backup of AlarmDecoder settings database."
                                        try:
                                            self.notification_service.send_email(self.recipients, subject, body, files=[backup_file])
                                            current_app.logger.info(f"Emailed backup {os.path.basename(backup_file)} to {self.recipients}")
                                        except Exception as err:
                                            current_app.logger.error(f"Error sending backup email: {err}", exc_info=True)
                                    # Delete the file if local storage is disabled
                                    if not self.local_storage:
                                        try:
                                            os.remove(backup_file)
                                            current_app.logger.info(f"Removed local backup file {backup_file} after email.")
                                        except Exception as e:
                                            current_app.logger.warning(f"Failed to remove backup file: {e}")
                                # Update last backup timestamp
                                self.last_check_time = current_time
                                last_check_setting = Setting.get_by_name('export_last_check_time')
                                if last_check_setting:
                                    last_check_setting.value = int(self.last_check_time)
                                    db.session.add(last_check_setting)
                                    db.session.commit()
                            else:
                                # Skip backup on first loop iteration to avoid immediate backup at service start
                                self._first_run = False

                            # Clean up old backup files beyond retention period
                            try:
                                self._remove_old_files(self.days_to_keep)
                            except Exception as cleanup_err:
                                current_app.logger.error(f"Backup retention cleanup error: {cleanup_err}")
                    except Exception as err:
                        current_app.logger.error(f"Error in backup scheduler: {err}", exc_info=True)
            time.sleep(self.CHECK_INTERVAL)

    def _create_backup_file(self):
        """
        Generate a backup tarball of the application data (settings, etc.) and write it to the export directory.
        Returns the full path to the created backup file, or None on failure.
        """
        try:
            # Prepare in-memory tar file
            buffer = io.BytesIO()
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{self.DEFAULT_PREFIX}-{timestamp}.tar.gz"
            full_path = os.path.join(self.export_path, filename)
            # Open a tarfile for writing (gzip compressed) to the buffer
            with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
                # Create a base directory in the tar (prefix) to contain all exported files
                tar_add_directory(tar, self.DEFAULT_PREFIX)
                # Iterate over all data models defined in EXPORT_MAP and add their data
                export_map = settings_constants.EXPORT_MAP
                for export_name, model_class in export_map.items():
                    try:
                        data_bytes = self._export_model_data(model_class)
                        tar_add_textfile(tar, export_name, data_bytes, self.DEFAULT_PREFIX)
                    except Exception as e:
                        current_app.logger.error(f"Failed to export {model_class.__name__}: {e}")
            # Write buffer to disk
            with open(full_path, 'wb') as outfile:
                outfile.write(buffer.getvalue())
            current_app.logger.info(f"Created backup file: {full_path}")
            return full_path
        except Exception as err:
            current_app.logger.error(f"Backup creation error: {err}", exc_info=True)
            return None

    def _export_model_data(self, model_class):
        """
        Query all records of the given SQLAlchemy model class and return a JSON representation (as bytes).
        Similar to Exporter._export_model, this serializes datetime objects and skips set columns.
        """
        import json
        from sqlalchemy.orm import class_mapper
        data = []
        for record in model_class.query.all():
            record_dict = {}
            for column in class_mapper(record.__class__).columns:
                value = getattr(record, column.key)
                # Convert datetime to string, skip sets
                if hasattr(value, 'isoformat'):
                    # datetime or date
                    try:
                        value = value.strftime('%Y-%m-%d %H:%M:%S.%f')
                    except Exception:
                        value = value.isoformat()
                elif isinstance(value, set):
                    continue  # skip set-type fields (not serializable to JSON directly)
                record_dict[column.key] = value
            data.append(record_dict)
        # Return JSON string as bytes
        return json.dumps(data, sort_keys=True, indent=4, separators=(',', ': '), default=str).encode('utf-8')

    def _remove_old_files(self, keep_days):
        """
        Remove backup files older than the given number of days from the export directory.
        """
        if not self.export_path or not os.path.isdir(self.export_path):
            return
        cutoff_time = time.time() - (keep_days * 24 * 3600)
        for fname in os.listdir(self.export_path):
            if fname.startswith(self.DEFAULT_PREFIX):
                fpath = os.path.join(self.export_path, fname)
                try:
                    if os.path.isfile(fpath):
                        # Use file creation time (or last modified if creation not available)
                        file_time = os.path.getctime(fpath)
                        if file_time < cutoff_time:
                            os.remove(fpath)
                            current_app.logger.info(f"Removed old backup file: {fname}")
                except Exception as e:
                    current_app.logger.error(f"Error removing old file {fname}: {e}")
