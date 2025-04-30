import os
from datetime import datetime

from flask import current_app
from markupsafe import escape

from ad2web.extensions import db
from ad2web.log.models import EventLogEntry
from ad2web.log.constants import EVENT_TYPES
from ad2web.logwatch import LogWatcher
from ad2web.utils import INSTANCE_FOLDER_PATH

class LogService:
    @staticmethod
    def get_event_log_page(start=0, length=50, search=None):
        """
        Retrieves a page of event log entries from the database.
        Returns (results_list, total_count, filtered_count).
        Each result is a tuple (timestamp_str, type_str, message).
        """
        # Validate pagination parameters
        if start < 0:
            start = 0
        if length is None or length <= 0:
            length = 50
        # Query total count of events
        try:
            total_count = EventLogEntry.query.count()
        except Exception as e:
            current_app.logger.error(f"Error counting all event logs: {e}")
            return [], 0, 0
        query = EventLogEntry.query
        # Apply search filter on message, if provided
        if search:
            pattern = f"%{search}%"
            try:
                query = query.filter(EventLogEntry.message.ilike(pattern))
            except Exception as e:
                current_app.logger.warning(f"Error filtering event logs: {e}")
                return [], total_count, 0  # treat filter failure as no results
        # Count filtered results
        try:
            filtered_count = query.count()
        except Exception as e:
            current_app.logger.error(f"Error counting filtered event logs: {e}")
            filtered_count = 0
        # Fetch the requested page of log entries
        try:
            entries = (query.order_by(EventLogEntry.timestamp.desc())
                           .offset(start).limit(length).all())
        except Exception as e:
            current_app.logger.error(f"Error querying event log entries: {e}")
            return [], total_count, 0
        # Format results: timestamp to string (no microseconds), map type code to name, escape text
        results = []
        for entry in entries:
            ts_str = entry.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            type_str = EVENT_TYPES.get(entry.type, str(entry.type))
            results.append((ts_str, escape(type_str), escape(entry.message)))
        return results, total_count, filtered_count

    @staticmethod
    def get_events_data_response(request_values):
        """
        Builds the JSON response for DataTables event log AJAX requests.
        """
        # Parse DataTables parameters from request
        try:
            s_echo = int(request_values.get('sEcho', 0))
        except (TypeError, ValueError):
            s_echo = 0
        try:
            start = int(request_values.get('iDisplayStart', 0))
        except (TypeError, ValueError):
            start = 0
        try:
            length = int(request_values.get('iDisplayLength', 50))
        except (TypeError, ValueError):
            length = 50
        search = request_values.get('sSearch', None)
        # Get filtered log entries and counts
        entries, total_count, filtered_count = LogService.get_event_log_page(start, length, search)
        # Prepare response structure
        data = {
            'sEcho': str(s_echo),
            'iTotalRecords': total_count,
            'iTotalDisplayRecords': filtered_count,
            'aaData': []
        }
        for ts, type_str, msg in entries:
            data['aaData'].append([ts, str(type_str), str(msg)])
        return data

    @staticmethod
    def clear_event_logs():
        """
        Deletes all event log entries. Returns True on success.
        """
        try:
            num_deleted = EventLogEntry.query.delete()
            db.session.commit()
            current_app.logger.info(f"Cleared {num_deleted} event log entries.")
            return True
        except Exception as e:
            current_app.logger.error(f"Failed to clear event logs: {e}")
            db.session.rollback()
            return False

    @staticmethod
    def tail_alarmdecoder_log(lines=50):
        """
        Returns the last N lines from the AlarmDecoder application log file.
        """
        log_file = os.path.join(INSTANCE_FOLDER_PATH, 'logs', 'info.log')
        if lines <= 0:
            return []
        try:
            log_lines = LogWatcher.tail(log_file, lines)
            # Decode bytes to str if necessary
            return [line.decode('utf-8') if isinstance(line, bytes) else line for line in log_lines]
        except OSError as err:
            current_app.logger.warning(f"Error reading log file: {err}")
            return [str(err)]
