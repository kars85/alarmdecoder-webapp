import os
# import cgi # Removed
import html # Added for html.escape
import json
import collections

from flask import Blueprint, render_template, request, url_for, redirect
from flask import current_app as APP
from flask_login import login_required

from ..extensions import db
# Removed: from ..decorators import admin_required # Removed top-level import
from .constants import ARM, DISARM, POWER_CHANGED, ALARM, FIRE, BYPASS, BOOT, \
                        CONFIG_RECEIVED, ZONE_FAULT, ZONE_RESTORE, LOW_BATTERY, \
                        PANIC, EVENT_TYPES, LRR, READY, RFX, EXP, AUI
from .models import EventLogEntry
from ..logwatch import LogWatcher
from ..utils import INSTANCE_FOLDER_PATH

log = Blueprint('log', __name__, url_prefix='/log')


@log.context_processor
def log_context_processor():
    # This function doesn't have external dependencies causing circular imports usually
    return {
        'ARM': ARM,
        'DISARM': DISARM,
        'POWER_CHANGED': POWER_CHANGED,
        'ALARM': ALARM,
        'FIRE': FIRE,
        'BYPASS': BYPASS,
        'BOOT': BOOT,
        'CONFIG_RECEIVED': CONFIG_RECEIVED,
        'ZONE_FAULT': ZONE_FAULT,
        'ZONE_RESTORE': ZONE_RESTORE,
        'LOW_BATTERY': LOW_BATTERY,
        'PANIC': PANIC,
        'LRR': LRR,
        'READY': READY,
        'EXP': EXP,
        'RFX': RFX,
        'AUI': AUI,
        'TYPES': EVENT_TYPES
    }

@log.route('/')
@login_required
def events():
    # No admin_required decorator here in the original code
    return render_template('log/events.html', active="events")

@log.route('/live')
@login_required
# @admin_required # Decorator moved inside
def live():
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # Original function code indented here
        return render_template('log/live.html', active='live')
    return inner() # Execute the inner, decorated function

@log.route('/delete', methods=['POST']) # Use POST for destructive actions
@login_required
# @admin_required # Decorator moved inside
def delete():
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # Original function code indented here
        try:
            num_deleted = EventLogEntry.query.delete()
            db.session.commit()
            APP.logger.info(f"Deleted {num_deleted} event log entries.")
            # Add a success message if desired
            # flash(f"Successfully deleted {num_deleted} event log entries.", "success")
        except Exception as e:
            db.session.rollback()
            APP.logger.error(f"Error deleting event log entries: {e}", exc_info=True)
            # Add an error message if desired
            # flash("Error deleting event log entries.", "error")

        return redirect(url_for('log.events'))
    return inner() # Execute the inner, decorated function

@log.route('/alarmdecoder')
@login_required
# @admin_required # Decorator moved inside
def alarmdecoder_logfile():
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # Original function code indented here
        return render_template('log/alarmdecoder.html', active='AlarmDecoder')
    return inner() # Execute the inner, decorated function

@log.route('/alarmdecoder/get_data/<int:lines>', methods=['GET'])
@login_required
# @admin_required # Decorator moved inside
def get_log_data(lines):
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner(num_lines): # Pass argument to inner function
        # Original function code indented here
        log_file = os.path.join(INSTANCE_FOLDER_PATH, 'logs', 'info.log')
        log_data = [] # Default to empty list

        try:
            log_data = LogWatcher.tail(log_file, num_lines)
        except FileNotFoundError:
            log_data = [f"Error: Log file not found at {log_file}"]
            APP.logger.warning(f"Log file not found: {log_file}")
        except OSError as err:
            log_data = [f"Error reading log file: {err}"]
            APP.logger.error(f"OSError reading log file {log_file}: {err}", exc_info=True)
        except Exception as err:
            log_data = [f"An unexpected error occurred: {err}"]
            APP.logger.error(f"Unexpected error reading log file {log_file}: {err}", exc_info=True)


        # Ensure response is always JSON parsable
        return json.dumps(log_data)
    # Pass the original argument to the inner function call
    return inner(lines)

#XHR for retrieving event log data server side
@log.route('/retrieve_events_paging_data')
@login_required # No admin_required needed here as it's just reading data
def get_events_paging_data():
    results = {}
    try:
        # Pass the request object to the DataTablesServer class
        results = DataTablesServer(request).output_result()
    except Exception as ex: # Catch broader exceptions during processing
        APP.logger.error("Error processing datatables request: {}".format(ex), exc_info=True)
        # Return an error structure that DataTables can understand
        sEcho = request.values.get('sEcho', '1') # Try to get sEcho
        results = {
            'sEcho': html.escape(str(int(sEcho)) if sEcho.isdigit() else '1'), # Escape and ensure int
            'iTotalRecords': 0,
            'iTotalDisplayRecords': 0,
            'aaData': [],
            'error': f"Server error processing request: {ex}" # Include error message
        }

    # Ensure the content type is set correctly
    return APP.response_class(
        response=json.dumps(results),
        status=200,
        mimetype='application/json'
    )

class DataTablesServer:
    def __init__( self, request ):
        self.request = request # Store the request object
        self.request_values = request.values
        self.result_data = None

        # Total records in the table without filtering
        self.cardinality_unfiltered = 0
        # Total records after filtering (but before pagination)
        self.cardinality_filtered = 0

        self.run_queries()

    def output_result(self):
        output = {}
        # Safely get and escape sEcho - essential for DataTables
        sEcho_val = self.request_values.get('sEcho', '0')
        try:
            # Ensure it's an integer before escaping its string representation
            output['sEcho'] = html.escape(str(int(sEcho_val)))
        except ValueError:
            output['sEcho'] = '0' # Default if conversion fails

        # Set counts based on the corrected query logic
        output['iTotalRecords'] = self.cardinality_unfiltered
        output['iTotalDisplayRecords'] = self.cardinality_filtered

        aaData_rows = []
        # Check if result_data is not None before iterating
        if self.result_data:
            for row in self.result_data:
                # Escape output data going back to the browser
                aaData_row = []
                aaData_row.append(html.escape(str(row.timestamp)))
                # Look up type safely, provide default if not found
                event_type_str = EVENT_TYPES.get(row.type, f"Unknown ({row.type})")
                aaData_row.append(html.escape(event_type_str))
                aaData_row.append(html.escape(row.message)) # Escape the message content

                aaData_rows.append(aaData_row)
        else:
            APP.logger.warning("output_result called but self.result_data is None.")


        output['aaData'] = aaData_rows
        # Include error key if processing failed previously (optional, handled in route)
        # if hasattr(self,'error') and self.error:
        #     output['error'] = self.error
        return output

    def run_queries(self):
        pages = self.paging()
        filter_term = self.filtering() # Corrected variable name

        # Default paging values
        start = 0
        limit = 10 # Default limit per page

        # Use validated paging values
        if pages and pages.start is not None:
            start = pages.start
        if pages and pages.length is not None:
            limit = pages.length

        # --- Corrected Query Logic ---
        try:
            # 1. Base Query
            base_query = EventLogEntry.query

            # 2. Calculate total unfiltered records (for iTotalRecords)
            self.cardinality_unfiltered = base_query.count() # Count before filtering

            # 3. Apply filtering (if a filter term exists)
            if filter_term:
                # Apply the filter to the base query
                filtered_query = base_query.filter(EventLogEntry.message.like(f'%{filter_term}%'))
            else:
                # No filter applied
                filtered_query = base_query

            # 4. Calculate total filtered records (for iTotalDisplayRecords)
            self.cardinality_filtered = filtered_query.count() # Count after filtering

            # 5. Apply sorting (currently hardcoded, consider adding dynamic sorting based on request_values)
            # Example: sort_column_index = request_values.get('iSortCol_0') etc.
            # For now, keep the original descending timestamp sort:
            sorted_query = filtered_query.order_by(EventLogEntry.timestamp.desc())

            # 6. Apply pagination
            self.result_data = sorted_query.limit(limit).offset(start).all() # Use .all() to execute

        except Exception as e:
            APP.logger.error(f"Error running DataTables queries: {e}", exc_info=True)
            self.result_data = [] # Ensure result_data is an empty list on error
            self.cardinality_unfiltered = 0
            self.cardinality_filtered = 0
            # Optionally store the error message
            # self.error = f"Database query failed: {e}"


    # Determine the filter value for the search box
    def filtering(self):
        filter_term = None
        search_val = self.request_values.get('sSearch') # Use .get for safety
        if search_val:
            # Escape the user input before using it in the query construction
            filter_term = html.escape(str(search_val))
            # Basic validation: prevent excessively long search terms if needed
            # MAX_SEARCH_LEN = 100
            # if len(filter_term) > MAX_SEARCH_LEN:
            #     filter_term = filter_term[:MAX_SEARCH_LEN]
            #     APP.logger.warning(f"Search term truncated to {MAX_SEARCH_LEN} characters.")

        return filter_term

    # Determine pagination parameters
    def paging(self):
        pages = collections.namedtuple('pages', ['start', 'length'])
        start_str = self.request_values.get('iDisplayStart')
        length_str = self.request_values.get('iDisplayLength')

        # Initialize with None
        start_val = None
        length_val = None

        # Validate and convert to integer - NO escaping needed here after int()
        if start_str and start_str.isdigit():
            start_val = int(start_str)
        else:
            APP.logger.debug(f"Invalid or missing iDisplayStart value: {start_str}")


        if length_str and length_str.isdigit():
            # DataTables uses -1 to mean "show all"
            length_int = int(length_str)
            if length_int == -1:
                # Handle "show all". Querying without limit can be dangerous on large tables.
                # Consider setting a maximum limit instead of truly unlimited.
                MAX_LIMIT = 1000 # Example max limit
                length_val = MAX_LIMIT
                APP.logger.debug(f"DataTables requested 'show all' (-1), applying max limit: {MAX_LIMIT}")
            else:
                length_val = length_int
        else:
            APP.logger.debug(f"Invalid or missing iDisplayLength value: {length_str}")


        # Return the named tuple with potentially None values if validation failed
        # The run_queries function will use defaults if these are None
        return pages(start=start_val, length=length_val)