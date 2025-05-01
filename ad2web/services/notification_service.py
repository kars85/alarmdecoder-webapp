import time
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import COMMASPACE, formatdate
from os.path import basename

from flask import current_app

# Import NotificationSystem and related models from the AlarmDecoder webapp
from ad2web.notifications.types import NotificationSystem

class NotificationService:
    """
    Service for handling notifications and alerts. This includes the AlarmDecoder NotificationSystem
    for event notifications and an integrated Mailer for sending emails (alerts, backups, etc.).
    """
    def __init__(self, app):
        """
        Initialize the notification service with the Flask app context. This will load notification
        configurations and prepare the notification system and email settings.
        """
        self.app = app
        # Initialize the AlarmDecoder notification system (manages various notifier channels)
        with app.app_context():
            self._notif_system = NotificationSystem()
            # Load SMTP email settings from the database (system email config)
            from ad2web.settings.models import Setting
            self.server = Setting.get_by_name('system_email_server',  default='localhost').value
            self.port = int(Setting.get_by_name('system_email_port', default=25).value)
            self.tls = Setting.get_by_name('system_email_tls', default=False).value
            self.auth_required = Setting.get_by_name('system_email_auth', default=False).value
            self.username = Setting.get_by_name('system_email_username', default='').value
            self.password = Setting.get_by_name('system_email_password', default='').value
            self.default_sender = Setting.get_by_name('system_email_from', default='root@alarmdecoder').value

        # Background thread management
        self._thread = None
        self._running = False

    def start(self):
        """
        Start the background notification monitoring thread. This thread checks for completed notification tasks
        and processes any delayed notifications in the wait list.
        """
        if not self._running:
            self._running = True
            # Launch the monitoring thread as a daemon
            self._thread = threading.Thread(target=self._notification_loop, daemon=True)
            self._thread.start()

    def stop(self):
        """
        Stop the background notification thread.
        """
        self._running = False
        if self._thread is not None:
            try:
                self._thread.join(timeout=5)
            except RuntimeError:
                pass
            self._thread = None

    def notify_event(self, event_type, **kwargs):
        """
        Dispatch a notification for the given event type using the NotificationSystem.
        This will queue notifications to all enabled notifiers that subscribe to the event.
        Any errors during notification dispatch are logged.
        """
        with self.app.app_context():
            errors = self._notif_system.send(event_type, **kwargs)
            for err in errors:
                current_app.logger.error(err)
        return errors

    def send_email(self, send_to, subject, body, files=None, send_from=None):
        """
        Send an email immediately. This uses the configured SMTP server and credentials.
        - send_to: list of recipient email addresses
        - subject: email subject line
        - body: plain text email body
        - files: list of file paths to attach (optional)
        - send_from: override the sender address (defaults to configured default sender)
        """
        if send_from is None:
            send_from = self.default_sender
        assert isinstance(send_to, list), "send_to must be a list of recipient addresses"
        msg = MIMEMultipart()
        msg['From'] = send_from
        msg['To'] = COMMASPACE.join(send_to)
        msg['Date'] = formatdate(localtime=True)
        msg['Subject'] = subject
        msg.attach(MIMEText(body))

        # Attach files if provided
        files = files or []
        for fpath in files:
            try:
                with open(fpath, "rb") as f:
                    part = MIMEApplication(f.read(), Name=basename(fpath))
                    part['Content-Disposition'] = f'attachment; filename="{basename(fpath)}"'
                    msg.attach(part)
            except Exception as err:
                current_app.logger.error(f"Failed to attach file {fpath}: {err}")

        try:
            smtp = smtplib.SMTP(self.server, self.port)
            if self.tls:
                smtp.starttls()
            if self.auth_required:
                smtp.login(str(self.username), str(self.password))
            smtp.sendmail(send_from, send_to, msg.as_string())
            smtp.quit()
            current_app.logger.info(f"Sent email to {send_to} via SMTP server {self.server}:{self.port}")
        except Exception as e:
            current_app.logger.error(f"Error sending email: {e}", exc_info=True)

    def update_server(self, server):
        """Update SMTP server address."""
        self.server = server

    def update_port(self, port):
        """Update SMTP server port."""
        self.port = int(port)

    def update_username(self, username):
        """Update SMTP username for authentication."""
        self.username = username

    def update_password(self, password):
        """Update SMTP password for authentication."""
        self.password = password

    def update_tls(self, use_tls):
        """Enable or disable TLS for SMTP."""
        self.tls = bool(use_tls)

    def update_auth(self, require_auth):
        """Enable or disable SMTP authentication requirement."""
        self.auth_required = bool(require_auth)

    def _notification_loop(self):
        """
        Internal loop that monitors asynchronous notification tasks and processes delayed notifications.
        This replaces the legacy NotificationThread run method.
        """
        # The NotificationSystem maintains an internal list of futures for tasks and a wait list for delayed notifications.
        notifier = self._notif_system
        while self._running:
            # Check for completed asynchronous notification tasks
            with notifier._lock:
                completed = []
                for future in list(notifier._futures):
                    if hasattr(future, 'done') and future.done():
                        try:
                            future.result()  # retrieve result to raise exceptions if any
                        except Exception as exc:
                            current_app.logger.error(f"Notification task error: {exc}", exc_info=True)
                        else:
                            current_app.logger.info("Background notification task completed with no exceptions.")
                        completed.append(future)
                # Remove completed futures from the list
                for fut in completed:
                    try:
                        notifier._futures.remove(fut)
                    except ValueError:
                        pass

            # Process any notifications that were delayed (e.g., zone restore delays)
            with self.app.app_context():
                errors = notifier.process_wait_list()
                for err in errors:
                    current_app.logger.error(err)

            time.sleep(5)  # sleep briefly before next check
