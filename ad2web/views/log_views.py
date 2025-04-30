from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask import current_app
from flask_login import login_required, current_user
from ad2web.decorators import admin_required
from ad2web.services.log_service import LogService
from ad2web.log.constants import (
    ARM, DISARM, POWER_CHANGED, ALARM, FIRE, BYPASS, BOOT,
    CONFIG_RECEIVED, ZONE_FAULT, ZONE_RESTORE, LOW_BATTERY,
    PANIC, LRR, READY, CHIME, RFX, EXP, AUI, EVENT_TYPES
)

log = Blueprint('log', __name__, url_prefix='/log')

@log.context_processor
def log_context_processor():
    """Expose event type constants to templates."""
    return {
        'ARM': ARM, 'DISARM': DISARM, 'POWER_CHANGED': POWER_CHANGED,
        'ALARM': ALARM, 'FIRE': FIRE, 'BYPASS': BYPASS, 'BOOT': BOOT,
        'CONFIG_RECEIVED': CONFIG_RECEIVED, 'ZONE_FAULT': ZONE_FAULT,
        'ZONE_RESTORE': ZONE_RESTORE, 'LOW_BATTERY': LOW_BATTERY,
        'PANIC': PANIC, 'LRR': LRR, 'READY': READY, 'CHIME': CHIME,
        'RFX': RFX, 'EXP': EXP, 'AUI': AUI, 'TYPES': EVENT_TYPES
    }

@log.route('/', methods=['GET'])
@login_required
def events():
    """Event history page."""
    # The 'tabs' list controls which log sub-pages are shown in the UI tab navigation
    tabs = [
        ("events", url_for('log.events'), False),
        ("live", url_for('log.live'), False),
        ("app",  url_for('log.alarmdecoder_logfile'), True)
    ]
    return render_template('log/events.html', active="events", tabs=tabs)

@log.route('/live', methods=['GET'])
@login_required
def live():
    """Live event streaming page."""
    tabs = [
        ("events", url_for('log.events'), False),
        ("live", url_for('log.live'), False),
        ("app",  url_for('log.alarmdecoder_logfile'), True)
    ]
    return render_template('log/live.html', active="live", tabs=tabs)

@log.route('/delete', methods=['POST'])
@login_required
@admin_required
def clear_events():
    """Clear all event log entries (admin only)."""
    success = LogService.clear_event_logs()
    if not success:
        current_app.logger.error("Event log clear failed.")
        # Optionally, flash an error message to the admin user here
    # Return JSON indicating success for the AJAX caller
    return jsonify({"success": success})

@log.route('/alarmdecoder', methods=['GET'])
@login_required
@admin_required
def alarmdecoder_logfile():
    """AlarmDecoder application log page (admin only)."""
    tabs = [
        ("events", url_for('log.events'), False),
        ("live", url_for('log.live'), False),
        ("app",  url_for('log.alarmdecoder_logfile'), True)
    ]
    return render_template('log/alarmdecoder.html', active="AlarmDecoder", tabs=tabs)

@log.route('/alarmdecoder/get_data/<int:lines>', methods=['GET'])
@login_required
@admin_required
def get_alarmdecoder_data(lines):
    """Provide last N lines of the AlarmDecoder log (admin only, AJAX)."""
    log_lines = LogService.tail_alarmdecoder_log(lines)
    return jsonify(log_lines)

@log.route('/retrieve_events_paging_data', methods=['GET'])
@login_required
def get_events_data():
    """Server-side data for event history DataTable (AJAX)."""
    try:
        data = LogService.get_events_data_response(request.values)
    except Exception as ex:
        current_app.logger.warning(f"Error processing DataTables request: {ex}")
        # Return an empty result on error to avoid breaking the table
        data = {'sEcho': "0", 'iTotalRecords': 0, 'iTotalDisplayRecords': 0, 'aaData': []}
    return jsonify(data)
