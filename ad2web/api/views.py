# ad2web/api/views.py
"""
Defines the Flask routes for the AD2Web RESTful API.

This module sets up two Blueprints:
- `api_settings`: Handles API-related settings pages (like key management)
                  and serving the Swagger/OpenAPI documentation.
- `api`: Handles the core v1 API endpoints for interacting with the
         AlarmDecoder device, zones, notifications, cameras, users, and system.

Most API endpoints require authentication via an API key passed either in the
'Authorization' header or as an 'apikey' query parameter. Access control
(e.g., admin checks) is applied where necessary.
"""
import json


import os
import platform
import socket
import subprocess

from functools import wraps
from datetime import timedelta
from http.client import OK, CREATED, ACCEPTED, NO_CONTENT, UNAUTHORIZED, NOT_FOUND, CONFLICT, UNPROCESSABLE_ENTITY, SERVICE_UNAVAILABLE

from flask import Blueprint, current_app, request, jsonify, Response, render_template, redirect, url_for
from flask_login import login_required

from alarmdecoder import AlarmDecoder
from alarmdecoder.panels import ADEMCO, DSC
from alarmdecoder.zonetracking import Zone as ADZone

from ..extensions import db
from ad2web.decorators import admin_required, crossdomain

from ..user import User, USER_ROLE, USER_STATUS, ADMIN
from ..zones import Zone
from ..notifications import Notification, NotificationSetting
from ..notifications.constants import EVENT_TYPES
from ..cameras import Camera

from .constants import ERROR_NOT_AUTHORIZED, ERROR_DEVICE_NOT_INITIALIZED, ERROR_MISSING_BODY, ERROR_MISSING_FIELD, ERROR_INVALID_VALUE, \
                        ERROR_RECORD_ALREADY_EXISTS, ERROR_RECORD_DOES_NOT_EXIST

from .models import APIKey
#from .utils import generate_api_key
import six

api_settings = Blueprint('api_settings', __name__, url_prefix='/api')
api = Blueprint('api', __name__, url_prefix='/api/v1')

request_user = None

##### Settings routes
@api_settings.route('/')
@login_required
def index():
    """Renders the main API settings page."""
    return render_template('api/index.html')

@api_settings.route('/api_doc', methods=['GET', 'OPTIONS'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
def api_doc():
    """Serves the OpenAPI (Swagger) JSON specification.

    Dynamically updates the 'host' field based on the request's FQDN.
    """
    project_root = current_app.config['PROJECT_ROOT']
    json_url = os.path.join( project_root, "ad2web/static", "alarmdecoder.json")
    data = json.load(open(json_url))

    #modify the host in the json to get their local fqdn if not alarmdecoder (using gunicorn port)
    host = socket.getfqdn()
    if host != 'alarmdecoder':
        data['host'] = host + ':' + os.getenv('AD_LISTENER_PORT', '5000')

    data = jsonify(data)
    return data, OK

@api_settings.route('/swagger', methods=['GET', 'OPTIONS'])
@login_required
def swagger():
    """Redirects to the Swagger UI interface."""
    return redirect(url_for('static', filename='swagger/index.html'))

@api_settings.route('/keys')
@login_required
@admin_required
def keys():
    """Renders the API key management page (Admin only)."""
    users = User.query.all()

    return render_template('api/keys.html', users=users)

@api_settings.route('/keys/generate/<int:user_id>')
@login_required
@admin_required
def generate_key(user_id):
    apikey = APIKey.query.filter_by(user_id=user_id).first()
    if not apikey:
        apikey = APIKey(user_id=user_id)

    # Generate new key using the model's utility method
    apikey.key = APIKey.generate_api_key()
    db.session.add(apikey)
    db.session.commit()
    return redirect(url_for('api_settings.keys'))

@api_settings.route('/keys/disable/<int:user_id>')
@login_required
@admin_required
def disable_key(user_id):
    """Disables (removes) the API key for a specific user (Admin only)."""
    apikey = APIKey.query.filter_by(user_id=user_id).first()
    if not apikey:
        apikey = APIKey(user_id=user_id)

    apikey.key = None

    db.session.add(apikey)
    db.session.commit()

    return redirect(url_for('api_settings.keys'))

##### Utility
def api_authorized(f):
    """Decorator to check for valid API key authorization.

    Verifies the presence and validity of an API key provided either in the
    'Authorization' header or as an 'apikey' query parameter. It also checks
    if the AlarmDecoder device is initialized and sets the `request_user` global
    variable for use within the decorated route.

    Returns:
        Flask Response: An error response (401, 422, 503) if authorization fails,
                      otherwise calls the decorated function.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        global request_user
        request_user = None

        if request.method in ['POST', 'PUT']:
            req = request.get_json(silent=True)
            if req is None:
                return jsonify(build_error(ERROR_MISSING_BODY, "Missing request body or using incorrect content type.")), UNPROCESSABLE_ENTITY

        #support getting API Key over Authorization Header
        request_apikey = request.headers.get('Authorization', None)

        if request_apikey is None:
            request_apikey = request.args.get('apikey', None)

        apikey = APIKey.query.filter_by(key=request_apikey).first()

        if request_apikey is None or apikey is None:
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, 'Not authorized.')), UNAUTHORIZED

        if current_app.decoder.device is None:
            return jsonify(build_error(ERROR_DEVICE_NOT_INITIALIZED, 'Device has not finished initializing.')), SERVICE_UNAVAILABLE

        request_user = User.query.filter_by(id=apikey.user_id).first()
        if request_user is None:
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "No user matches the supplied API key.")), UNAUTHORIZED

        return f(*args, **kwargs)

    return wrapped

def build_error(code, message):
    """Constructs a standardized JSON error response body.

    Args:
        code (int): An error code constant (e.g., ERROR_NOT_AUTHORIZED).
        message (str): A human-readable error message.

    Returns:
        dict: A dictionary representing the JSON error structure.
    """
    return {
        'error': {
            'code': code,
            'message': message,
        }
    }

def check_admin(user):
    """Checks if a given user has administrative privileges.

    Args:
        user (User): The user object to check.

    Returns:
        bool: True if the user is an admin, False otherwise.
    """
    if not user:
        return False

    return user.role_code == ADMIN

##### AlarmDecoder device routes
@api.route('/alarmdecoder', methods=['GET', 'OPTIONS'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder():
    """Gets the current status of the AlarmDecoder device.

    Returns:
        JSON: A dictionary containing various status flags and information
              about the connected alarm panel (power, ready, armed, zones, etc.).
    """
    mode = current_app.decoder.device.mode
    if mode == ADEMCO:
        mode = 'ADEMCO'
    elif mode == DSC:
        mode = 'DSC'
    else:
        mode = 'UNKNOWN'

    relay_status = []
    for (address, channel), value in current_app.decoder.device._relay_status.items():  # TODO: test this.
        relay_status.append({
            'address': address,
            'channel': channel,
            'value': value
        })

    faulted_zones = []
    for zid, z in current_app.decoder.device._zonetracker.zones.items():
        if z.status != ADZone.CLEAR:
            faulted_zones.append(z.zone)

    ret = {
        'panel_type': mode,
        'panel_powered': current_app.decoder.device._power_status,
        'panel_ready': getattr(current_app.decoder.device, "_ready_status", True),
        'panel_alarming': current_app.decoder.device._alarm_status,
        'panel_bypassed': current_app.decoder.device._bypass_status,
        'panel_armed': current_app.decoder.device._armed_status,
        'panel_armed_stay': getattr(current_app.decoder.device, "_armed_stay", False),
        'panel_fire_detected': current_app.decoder.device._fire_status,
        'panel_battery_trouble': current_app.decoder.device._battery_status[0],
        'panel_panicked': current_app.decoder.device._panic_status,
        'panel_chime': getattr(current_app.decoder.device, "_chime_status", False),
        'panel_perimeter_only': getattr(current_app.decoder.device, "_perimeter_only_status", False),
        'panel_entry_delay_off': getattr(current_app.decoder.device, "_entry_delay_off_status", False),
        'panel_exit': getattr(current_app.decoder.device,"_exit", False),
        'panel_relay_status': relay_status,
        'panel_zones_faulted': faulted_zones,
        'last_message_received': current_app.decoder.last_message_received
    }

    return jsonify(ret), OK

@api.route('/alarmdecoder/send', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder_send():
    """Sends keypresses to the alarm panel via the AlarmDecoder.

    Expects a JSON body with a 'keys' field containing the string of keys
    to send. Special keys like <F1>, <PANIC> are supported.

    Returns:
        Response: 204 No Content on success.
                  422 Unprocessable Entity if 'keys' field is missing.
    """
    req = request.get_json()
    keys = req.get('keys', None)
    if keys is None:
        return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'keys' in request.")), UNPROCESSABLE_ENTITY

    #replace special keys with 3 press representation
    keys = keys.replace("<F1>", AlarmDecoder.KEY_F1 )
    keys = keys.replace("<F2>", AlarmDecoder.KEY_F2 )
    keys = keys.replace("<F3>", AlarmDecoder.KEY_F3 )
    keys = keys.replace("<F4>", AlarmDecoder.KEY_F4 )
    keys = keys.replace("<PANIC>", AlarmDecoder.KEY_PANIC )
    keys = keys.replace("<S1>", AlarmDecoder.KEY_S1 )
    keys = keys.replace("<S2>", AlarmDecoder.KEY_S2 )
    keys = keys.replace("<S3>", AlarmDecoder.KEY_S3 )
    keys = keys.replace("<S4>", AlarmDecoder.KEY_S4 )
    keys = keys.replace("<S5>", AlarmDecoder.KEY_S5 )
    keys = keys.replace("<S6>", AlarmDecoder.KEY_S6 )
    keys = keys.replace("<S7>", AlarmDecoder.KEY_S7 )
    keys = keys.replace("<S8>", AlarmDecoder.KEY_S8 )

    current_app.decoder.device.send(keys)

    return "", NO_CONTENT

@api.route('/alarmdecoder/event', methods=['SUBSCRIBE','UNSUBSCRIBE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder_events():
    """Handles UPnP-style event subscriptions for AlarmDecoder events.

    SUBSCRIBE: Registers a callback URL to receive notifications for events.
               Requires HOST, CALLBACK, and TIMEOUT headers.
    UNSUBSCRIBE: Removes an existing subscription.
                 Requires HOST and SID headers.

    Returns:
        Response: A UPnP-compliant response with appropriate headers (SID, TIMEOUT).
    """
    device = current_app.decoder.device

    if request.method == 'SUBSCRIBE':
        ret = _build_alarmdecoder_configuration_data(device)

        # Get data we need
        host = request.headers.get("HOST")
        callback = request.headers.get("CALLBACK")
        timeout = request.headers.get("TIMEOUT")

        current_app.logger.info('SUBSCRIBE host:{} callback: {} timeout: {}'.format(host, callback, timeout))

        # Call notifier method to add to subscriber map/list
        subid = current_app.decoder._notifier_system.add_subscriber(host, callback, timeout)

        # If all went well adding then respond OK
        # NOTE: Currently no way to Fail with 500.
        resp = Response("", status=200)
        resp.headers['SERVER'] = "Linux UPnP/1.0 AlarmDecoder"
        resp.headers['X-User-Agent'] = "AD2WEB"
        resp.headers['TIMEOUT'] = timeout
        resp.headers['SID'] = "uuid:" + subid

        return resp

    if request.method == 'UNSUBSCRIBE':
        ret = _build_alarmdecoder_configuration_data(device)

        # Get data we need
        host = request.headers.get("HOST")
        sid = request.headers.get("SID")

        current_app.logger.info('UNSUBSCRIBE host: {} sid: {}'.format(host, sid))

        # Call notifier method to remove from subscriber map/list
        current_app.decoder._notifier_system.remove_subscriber(host, sid)

        # If all went well adding then respond OK
        # NOTE: Currently no way to Fail with 500.
        resp = Response("", status=200)
        resp.headers['SERVER'] = "Linux UPnP/1.0 AlarmDecoder"
        resp.headers['X-User-Agent'] = "AD2WEB"
        resp.headers['SID'] = sid

        return resp

@api.route('/alarmdecoder/reboot', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder_reboot():
    """Reboots the AlarmDecoder device (Admin only).

    Returns:
        Response: 204 No Content on success.
                  401 Unauthorized if the user is not an admin.
    """
    if not check_admin(request_user):
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    current_app.decoder.device.reboot()

    return "", NO_CONTENT

def _build_alarmdecoder_configuration_data(device, short=False):
    """Helper function to build a dictionary of AlarmDecoder configuration.

    Args:
        device (AlarmDecoder): The AlarmDecoder device instance.
        short (bool): If True, returns a minimal representation (unused here).

    Returns:
        dict or None: A dictionary containing configuration parameters, or None
                      if the device is not provided.
    """
    if not device:
        return None

    if device.mode == ADEMCO:
        mode = 'ADEMCO'
    elif device.mode == DSC:
        mode = 'DSC'
    else:
        mode = 'UNKNOWN'

    ret = {
        'address': device.address,
        'config_bits': device.configbits,
        'address_mask': device.address_mask,
        'emulate_zone': device.emulate_zone,
        'emulate_relay': device.emulate_relay,
        'emulate_lrr': device.emulate_lrr,
        'deduplicate': device.deduplicate,
        'mode': mode
    }

    return ret

@api.route('/alarmdecoder/configuration', methods=['GET', 'PUT'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder_configuration():
    """Gets or updates the AlarmDecoder device configuration (PUT requires Admin).

    GET: Returns the current configuration settings.
    PUT: Updates configuration settings based on the provided JSON body.
         Requires admin privileges.

    Returns:
        JSON: The current (or updated) configuration data on success (200 OK).
              401 Unauthorized if PUT is attempted without admin rights.
              422 Unprocessable Entity for invalid values (e.g., invalid 'mode').
    """
    device = current_app.decoder.device

    if request.method == 'GET':
        ret = _build_alarmdecoder_configuration_data(device)

        return jsonify(ret), OK

    elif request.method == 'PUT':
        if not check_admin(request_user):
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

        req = request.get_json()

        if req.get('address', None) is not None:
            device.address = req['address']
        if req.get('config_bits', None) is not None:
            device.configbits = req['config_bits']
        if req.get('address_mask', None) is not None:
            device.address_mask = req['address_mask']
        if req.get('emulate_zone', None) is not None:
            device.emulate_zone = req['emulate_zone']
        if req.get('emulate_relay', None) is not None:
            device.emulate_relay = req['emulate_relay']
        if req.get('emulate_lrr', None) is not None:
            device.emulate_lrr = req['emulate_lrr']
        if req.get('deduplicate', None) is not None:
            device.deduplicate = req['deduplicate']
        if req.get('mode', None) is not None:
            mode = req['mode']
            if mode == 'ADEMCO':
                mode = ADEMCO
            elif mode == 'DSC':
                mode = DSC
            else:
                return jsonify(build_error(ERROR_INVALID_VALUE, "Invalid value for 'mode'.")), UNPROCESSABLE_ENTITY

            device.mode = mode

        device.save_config()

        ret = _build_alarmdecoder_configuration_data(device)

        return jsonify(ret), OK

##### Zone routes
def _build_zone_data(zone, short=False):
    """Helper function to build a dictionary representation of a Zone.

    Args:
        zone (Zone): The Zone database model instance.
        short (bool): If True, returns a minimal representation (unused here).

    Returns:
        dict or None: A dictionary containing zone details (id, name, description),
                      or None if the zone object is not provided.
    """
    if not Zone:
        return None

    ret = {
        'zone_id': zone.zone_id,
        'name': zone.name,
        'description': zone.description
    }

    return ret

@api.route('/zones', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def zones():
    """Gets a list of all zones or creates a new zone (POST requires Admin).

    GET: Returns a list of all configured zones.
    POST: Creates a new zone definition. Requires admin privileges and a JSON
          body with 'zone_id', 'name', and optionally 'description'.

    Returns:
        JSON: List of zones (GET) or the newly created zone data (POST).
              200 OK (GET), 201 Created (POST).
              401 Unauthorized if POST is attempted without admin rights.
              409 Conflict if a zone with the given ID already exists (POST).
              422 Unprocessable Entity if required fields are missing (POST).
    """
    if request.method == 'GET':
        ret = {}
        zones = Zone.query.all()

        ret['zones'] = []
        for z in zones:
            ret['zones'].append(_build_zone_data(z, short=True))

        return jsonify(ret), OK

    elif request.method == 'POST':
        if not check_admin(request_user):
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

        req = request.get_json()

        zone_id = req.get('zone_id', None)
        name = req.get('name', None)
        description = req.get('description', None)

        if zone_id is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'zone_id' entry.")), UNPROCESSABLE_ENTITY

        if name is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'name' entry.")), UNPROCESSABLE_ENTITY

        existing_zone = Zone.query.filter_by(zone_id=zone_id).first()
        if existing_zone is not None:
            return jsonify(build_error(ERROR_RECORD_ALREADY_EXISTS, 'Zone already exists.')), CONFLICT

        zone = Zone(zone_id=zone_id, name=name, description=description)

        db.session.add(zone)
        db.session.commit()

        ret = _build_zone_data(zone)

        return jsonify(ret), CREATED

@api.route('/zones/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def zones_by_id(id):
    """Gets, updates, or deletes a specific zone (PUT/DELETE require Admin).

    Args:
        id (int): The zone ID.

    GET: Returns details for the specified zone.
    PUT: Updates the specified zone. Requires admin privileges and a JSON body
         with fields to update ('zone_id', 'name', 'description').
    DELETE: Deletes the specified zone. Requires admin privileges.

    Returns:
        JSON: Zone details (GET, PUT). 200 OK.
              Response: 204 No Content (DELETE).
              401 Unauthorized if PUT/DELETE attempted without admin rights.
              404 Not Found if the zone ID does not exist.
              409 Conflict if PUT attempts to change zone_id to an existing one.
              422 Unprocessable Entity if required fields are missing (PUT).
    """
    zone = Zone.query.filter_by(zone_id=id).first()
    if zone is None:
        return jsonify(build_error(ERROR_RECORD_DOES_NOT_EXIST, 'Zone does not exist.')), NOT_FOUND

    if request.method == 'GET':
        ret = _build_zone_data(zone)

        return jsonify(ret), OK

    elif request.method == 'PUT':
        if not check_admin(request_user):
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

        req = request.get_json()

        zone_id = req.get('zone_id', None)

        if zone_id is not None and zone_id != zone.zone_id:
            check_zone = Zone.query.filter_by(zone_id=zone_id).first()
            if check_zone is not None:
                return jsonify(build_error(ERROR_RECORD_ALREADY_EXISTS, 'Zone already exists with the associated zone_id.')), CONFLICT
            else:
                zone.zone_id = zone_id

        name = req.get('name', None)
        if name is not None:
            zone.name = name

        description = req.get('description', None)
        if description is not None:
            zone.description = description

        db.session.add(zone)
        db.session.commit()

        ret = _build_zone_data(zone)

        return jsonify(ret), OK

    elif request.method == 'DELETE':
        if not check_admin(request_user):
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

        db.session.delete(zone)
        db.session.commit()

        return "", NO_CONTENT

@api.route('/zones/<int:id>/fault', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def zones_fault(id):
    """Simulates a fault on a specific zone (Admin only).

    Note: This typically only works for zones emulated by the AlarmDecoder.

    Args:
        id (int): The zone ID to fault.

    Returns:
        Response: 204 No Content on success.
                  401 Unauthorized if the user is not an admin.
                  404 Not Found if the zone ID does not exist.
    """
    if not check_admin(request_user):
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    z = Zone.query.filter_by(zone_id=id).first()
    if z is None:
        return jsonify(build_error(ERROR_RECORD_DOES_NOT_EXIST, 'Zone does not exist.')), NOT_FOUND

    # TODO: Make a note in docs.. only supported for emulated zones.
    current_app.decoder.device.send("L{}1\r".format(id))

    return "", NO_CONTENT

@api.route('/zones/<int:id>/restore', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def zones_restore(id):
    """Simulates restoring a specific zone from a fault condition (Admin only).

    Note: This typically only works for zones emulated by the AlarmDecoder.

    Args:
        id (int): The zone ID to restore.

    Returns:
        Response: 204 No Content on success.
                  401 Unauthorized if the user is not an admin.
                  404 Not Found if the zone ID does not exist.
    """
    if not check_admin(request_user):
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    z = Zone.query.filter_by(zone_id=id).first()
    if z is None:
        return jsonify(build_error(ERROR_RECORD_DOES_NOT_EXIST, 'Zone does not exist.')), NOT_FOUND

    # TODO: Make a note in docs.. only supported for emulated zones.
    current_app.decoder.device.send("L{}0\r".format(id))

    return "", NO_CONTENT

##### Notification routes
def _build_notification_data(notification, short=False):
    """Helper function to build a dictionary representation of a Notification.

    Args:
        notification (Notification): The Notification database model instance.
        short (bool): If True, returns a minimal representation (omits settings).

    Returns:
        dict or None: A dictionary containing notification details. Sensitive
                      settings like username/password are omitted. Subscription
                      settings are transformed for readability. Returns None if
                      the notification object is not provided.
    """
    if notification is None:
        return None

    ret = {
        'id': notification.id,
        'type': notification.type,
        'description': notification.description,
        'user_id': notification.user_id
    }

    if not short:
        settings = { }

        for setting_name, setting in notification.settings.items():
            # NOTE: Leaving authentication information out on purpose.  May need to expand this or do it a different way.
            if setting_name == 'username' or setting_name == 'password':
                continue

            value = setting.value

            # Special case for subscriptions.
            if setting_name == "subscriptions":
                value = json.loads(setting.value)

                output = { }
                for event_type in EVENT_TYPES:
                    output[EVENT_TYPES[event_type]] = False

                for k in value.keys():
                    output[EVENT_TYPES[int(k)]] = value[k]
                    del value[k]

                value = output

            settings[setting_name] = value

        ret['settings'] = settings

    return ret

@api.route('/notifications', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def notifications():
    """Gets a list of notifications or creates a new one.

    GET: Returns a list of notifications accessible by the authenticated user.
         Admins see all notifications; regular users see only their own.
    POST: Creates a new notification. Requires a JSON body with 'type',
          'description', 'user_id', and 'settings'. Non-admins can only
          create notifications for themselves.

    Returns:
        JSON: List of notifications (GET) or the newly created notification (POST).
              200 OK (GET), 201 Created (POST).
              401 Unauthorized if user attempts to create for another user (non-admin).
              422 Unprocessable Entity if required fields are missing or invalid (POST).
    """
    if request.method == 'GET':
        if request_user.role_code == ADMIN:
            notifications = Notification.query.all()
        else:
            notifications = Notification.query.filter_by(user_id=request_user.id).all()

        ret = { 'notifications': [] }
        for n in notifications:
            ret['notifications'].append(_build_notification_data(n, short=True))

        return jsonify(ret), OK

    elif request.method == 'POST':
        req = request.get_json()

        notification_type = req.get('type', None)
        if notification_type is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'type' entry.")), UNPROCESSABLE_ENTITY

        description = req.get('description', None)
        if description is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'description' entry.")), UNPROCESSABLE_ENTITY

        user_id = req.get('user_id', None)
        if user_id is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'user_id' entry.")), UNPROCESSABLE_ENTITY

        if request_user and request_user.role_code != ADMIN and user_id != request_user.id:
            return jsonify(build_error(ERROR_INVALID_VALUE, "Cannot create notifications for other users.")), UNPROCESSABLE_ENTITY

        notification = Notification(type=notification_type, description=description, user_id=user_id)

        settings = req.get('settings', None)
        for name, value in settings.items():
            if name == 'subscriptions':
                event_types = {v: k for k, v in EVENT_TYPES.items()}

                subscriptions_out = {}
                for k, v in value.items():
                    subscriptions_out[str(event_types[k])] = v

                value = json.dumps(subscriptions_out)

            notification.settings[name] = NotificationSetting(name=name, value=value)

        db.session.add(notification)
        db.session.commit()

        ret = _build_notification_data(notification)

        return jsonify(ret), CREATED

@api.route('/notifications/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def notifications_by_id(id):
    """Gets, updates, or deletes a specific notification.

    Requires the authenticated user to be an admin or the owner of the notification.

    Args:
        id (int): The notification ID.

    GET: Returns details for the specified notification.
    PUT: Updates the specified notification. Requires a JSON body with fields
         to update ('description', 'user_id', 'settings'). The 'type' cannot be changed.
    DELETE: Deletes the specified notification.

    Returns:
        JSON: Notification details (GET, PUT). 200 OK.
              Response: 204 No Content (DELETE).
              401 Unauthorized if user lacks permission.
              404 Not Found if the notification ID does not exist.
              422 Unprocessable Entity for invalid updates (e.g., changing type).
    """
    ret = { }

    notification = Notification.query.filter_by(id=id).first()
    if notification is None:
        return jsonify(build_error(ERROR_RECORD_DOES_NOT_EXIST, 'Notification does not exist.')), NOT_FOUND

    if request_user and request_user.role_code != ADMIN and request_user.id != notification.user_id:
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    if request.method == 'GET':
        ret = _build_notification_data(notification)

        return jsonify(ret), OK

    elif request.method == 'PUT':
        req = request.get_json()

        notification_type = req.get('type', None)
        if notification_type is not None and notification_type != notification.type:
            return jsonify(build_error(ERROR_INVALID_VALUE, 'Cannot change the type of an existing notification.')), UNPROCESSABLE_ENTITY

        description = req.get('description', None)
        user_id = req.get('user_id', None)
        settings = req.get('settings', None)

        if description is not None:
            notification.description = description
        if user_id is not None:
            notification.user_id = user_id

        if settings is not None:
            for name, value in settings.items():
                setting = notification.settings.get(name, None)
                if setting is None:
                    setting = NotificationSetting(name=name)

                if name == 'subscriptions':
                    event_types = {v: k for k, v in EVENT_TYPES.items()}

                    subscriptions_out = {}
                    for k, v in value.items():
                        subscriptions_out[str(event_types[k])] = v

                    value = json.dumps(subscriptions_out)

                setting.value = value

        db.session.add(notification)
        db.session.commit()

        ret = _build_notification_data(notification)

        return jsonify(ret), OK

    elif request.method == 'DELETE':
        db.session.delete(notification)
        db.session.commit()

        return "", NO_CONTENT

##### Camera routes
def _build_camera_data(camera, short=False):
    """Helper function to build a dictionary representation of a Camera.

    Args:
        camera (Camera): The Camera database model instance.
        short (bool): If True, returns a minimal representation (omits URL).

    Returns:
        dict or None: A dictionary containing camera details. Sensitive info
                      like username/password is omitted. Returns None if the
                      camera object is not provided.
    """
    if camera is None:
        return None

    ret = {
        'id': camera.id,
        'name': camera.name,
        'user_id': camera.user_id
    }

    if not short:
        # NOTE: Leaving authentication information out on purpose.
        ret['url'] = camera.get_jpg_url

    return ret

@api.route('/cameras', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def cameras():
    """Gets a list of cameras or creates a new one.

    GET: Returns a list of cameras accessible by the authenticated user.
         Admins see all cameras; regular users see only their own.
    POST: Creates a new camera. Requires a JSON body with 'name', 'url',
          'user_id', and optionally 'username', 'password'. Non-admins can only
          create cameras for themselves.

    Returns:
        JSON: List of cameras (GET) or the newly created camera (POST).
              200 OK (GET), 201 Created (POST).
              401 Unauthorized if user attempts to create for another user (non-admin).
              422 Unprocessable Entity if required fields are missing (POST).
    """
    ret = { }

    if request.method == 'GET':
        if request_user.role_code == ADMIN:
            cameras = Camera.query.all()
        else:
            cameras = Camera.query.filter_by(user_id=request_user.id).all()

        ret['cameras'] = []
        for camera in cameras:
            ret['cameras'].append(_build_camera_data(camera, short=True))

        return jsonify(ret), OK

    elif request.method == 'POST':
        req = request.get_json()

        name = req.get('name', None)
        url = req.get('url', None)
        user_id = req.get('user_id', None)
        username = req.get('username', '')  # TODO: Fix camera code so that it deals with nulls correctly
        password = req.get('password', '')  # TODO: Fix camera code so that it deals with nulls correctly

        if name is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'name' entry.")), UNPROCESSABLE_ENTITY

        if url is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'url' entry.")), UNPROCESSABLE_ENTITY

        if user_id is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'user_id' entry.")), UNPROCESSABLE_ENTITY

        if request_user and request_user.role_code != ADMIN and user_id != request_user.id:
            return jsonify(build_error(ERROR_INVALID_VALUE, "Cannot create cameras for other users.")), UNPROCESSABLE_ENTITY

        camera = Camera(name=name, get_jpg_url=url, user_id=user_id, username=username, password=password)
        db.session.add(camera)
        db.session.commit()

        ret = _build_camera_data(camera)

        return jsonify(ret), CREATED

@api.route('/cameras/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def cameras_by_id(id):
    """Gets, updates, or deletes a specific camera.

    Requires the authenticated user to be an admin or the owner of the camera.

    Args:
        id (int): The camera ID.

    GET: Returns details for the specified camera.
    PUT: Updates the specified camera. Requires a JSON body with fields
         to update ('name', 'url', 'user_id', 'username', 'password').
    DELETE: Deletes the specified camera.

    Returns:
        JSON: Camera details (GET, PUT). 200 OK.
              Response: 204 No Content (DELETE).
              401 Unauthorized if user lacks permission.
              404 Not Found if the camera ID does not exist.
    """
    ret = { }

    camera = Camera.query.filter_by(id=id).first()

    if request_user and request_user.role_code != ADMIN and camera and request_user.id != camera.user_id:
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    if camera is None:
        return jsonify(build_error(ERROR_RECORD_DOES_NOT_EXIST, 'Camera does not exist.')), NOT_FOUND

    if request.method == 'GET':
        ret = _build_camera_data(camera)

        return jsonify(ret), OK

    elif request.method == 'PUT':
        req = request.get_json()

        name = req.get('name', None)
        url = req.get('url', None)
        user_id = req.get('user_id', None)
        username = req.get('username', None)
        password = req.get('password', None)

        if name is not None:
            camera.name = name
        if url is not None:
            camera.get_jpg_url = url
        if user_id is not None:
            camera.user_id = user_id
        if username is not None:
            camera.username = username
        if password is not None:
            camera.password = password

        db.session.add(camera)
        db.session.commit()

        ret = _build_camera_data(camera)

        return jsonify(ret), OK

    elif request.method == 'DELETE':
        db.session.delete(camera)
        db.session.commit()

        return "", NO_CONTENT

##### User routes
def _build_user_data(user, short=False):
    """Helper function to build a dictionary representation of a User.

    Args:
        user (User): The User database model instance.
        short (bool): If True, returns a minimal representation (unused here).

    Returns:
        dict or None: A dictionary containing user details (excluding password).
                      Returns None if the user object is not provided.
    """
    if not user:
        return None

    ret = {
        'id': user.id,
        'name': user.name,
        'email': user.email,
        'created_time': user.created_time,
        'role': user.role,
        'status': user.status
    }

    return ret

@api.route('/users', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def users():
    """Gets a list of all users or creates a new user (Admin only).

    GET: Returns a list of all users.
    POST: Creates a new user. Requires a JSON body with 'name', 'email',
          'password', 'role', and 'status'.

    Returns:
        JSON: List of users (GET) or the newly created user data (POST).
              200 OK (GET), 201 Created (POST).
              401 Unauthorized if the requesting user is not an admin.
              409 Conflict if a user with the given email or name already exists (POST).
              422 Unprocessable Entity if required fields are missing or invalid (POST).
    """
    if not check_admin(request_user):
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    ret = { }

    if request.method == 'GET':
        ret['users'] = []

        users = User.query.all()
        for user in users:
            ret['users'].append(_build_user_data(user, short=True))

        return jsonify(ret), OK

    elif request.method == 'POST':
        req = request.get_json()

        name = req.get('name', None)
        email = req.get('email', None)
        password = req.get('password', None)
        role = req.get('role', None)
        status = req.get('status', None)

        if name is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'name' entry.")), UNPROCESSABLE_ENTITY

        if email is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'email' entry.")), UNPROCESSABLE_ENTITY

        if password is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'password' entry.")), UNPROCESSABLE_ENTITY

        if role is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'role' entry.")), UNPROCESSABLE_ENTITY

        if status is None:
            return jsonify(build_error(ERROR_MISSING_FIELD, "Missing 'status' entry.")), UNPROCESSABLE_ENTITY

        existing_user = User.query.filter_by(email=email).first()
        if existing_user is not None:
            return jsonify(build_error(ERROR_RECORD_ALREADY_EXISTS, 'User already exists with specified the email address.')), CONFLICT

        existing_user = User.query.filter_by(name=name).first()
        if existing_user is not None:
            return jsonify(build_error(ERROR_RECORD_ALREADY_EXISTS, 'User already exists with the specified username.')), CONFLICT

        # Convert role/status fields into what they should be.
        role_types = {v: k for k, v in USER_ROLE.items()}
        status_types = {v: k for k, v in USER_STATUS.items()}

        role = role_types[role]
        status = status_types[status]

        user = User(name=name, email=email, password=password, role_code=role, status_code=status)
        db.session.add(user)
        db.session.commit()

        ret = _build_user_data(user)

        return jsonify(ret), CREATED

@api.route('/users/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def users_by_id(id):
    """Gets, updates, or deletes a specific user.

    Requires the authenticated user to be an admin, or the user being acted upon
    if only viewing/updating their own details (DELETE requires Admin).

    Args:
        id (int): The user ID.

    GET: Returns details for the specified user.
    PUT: Updates the specified user. Requires a JSON body with fields to update
         ('name', 'email', 'role', 'status'). Admins can update any user;
         regular users can only update their own name and email.
    DELETE: Deletes the specified user (Admin only). Cannot delete user ID 1
            or the currently authenticated user.

    Returns:
        JSON: User details (GET, PUT). 200 OK.
              Response: 204 No Content (DELETE).
              401 Unauthorized if user lacks permission.
              404 Not Found if the user ID does not exist.
              422 Unprocessable Entity for invalid operations (e.g., deleting self,
                  deleting user 1, invalid role/status).
    """
    ret = { }

    user = User.query.filter_by(id=id).first()

    if request_user and request_user.role_code != ADMIN and user and request_user.id != user.id:
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    if user is None:
        return jsonify(build_error(ERROR_RECORD_DOES_NOT_EXIST, 'User does not exist.')), NOT_FOUND

    if request.method == 'GET':
        ret = _build_user_data(user)

        return jsonify(ret), OK

    elif request.method == 'PUT':
        req = request.get_json()

        name = req.get('name', None)
        email = req.get('email', None)
        role = req.get('role', None)
        status = req.get('status', None)

        # Convert role/status fields into what they should be.
        role_types = {v: k for k, v in USER_ROLE.items()}
        status_types = {v: k for k, v in USER_STATUS.items()}

        if name is not None:
            user.name = name
        if email is not None:
            user.email = email
        if role is not None:
            user.role_code = role_types[role]
        if status is not None:
            user.status_code = status_types[status]

        db.session.add(user)
        db.session.commit()

        ret = _build_user_data(user)

        return jsonify(ret), OK

    elif request.method == 'DELETE':
        if not check_admin(request_user):
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

        # Don't allow deletion of primary admin user.
        if id == 1:
            return jsonify(build_error(ERROR_INVALID_VALUE, 'Cannot delete primary admin user.')), UNPROCESSABLE_ENTITY

        if user.id == request_user.id:
            return jsonify(build_error(ERROR_INVALID_VALUE, 'Cannot delete own user account.')), UNPROCESSABLE_ENTITY

        db.session.delete(user)
        db.session.commit()

        return "", NO_CONTENT

##### System routes
@api.route('/system', methods=['GET'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def system():
    """Gets system information like uptime and webapp update status.

    Returns:
        JSON: A dictionary containing system uptime and webapp update details.
              200 OK.
    """
    uptime = ''
    with open('/proc/uptime') as f:
        seconds = float(f.readline().split()[0])
        uptime = timedelta(seconds=int(seconds))

    (update_available, branch, local_revision, remote_revision, status, project_url) = current_app.decoder.updater.check_updates()['AlarmDecoderWebapp']

    ret = {
        'uptime': str(uptime),
        'webapp': {
            'update_available': update_available,
            'update_status': status,
            'branch': branch,
            'revision': local_revision
        }
    }

    return jsonify(ret), OK

@api.route('/system/reboot', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def system_reboot():
    """Reboots the host system (Admin only).

    Executes the `reboot` command.

    Returns:
        JSON: Empty body. 202 Accepted on success.
              401 Unauthorized if the user is not an admin or if the command fails
                  due to permissions.
    """
    if not check_admin(request_user):
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    if platform.system().lower() == 'windows':
        # On Windows, use the shutdown command to reboot (/r = reboot, /t 0 = no delay, /f = force)
        try:
            subprocess.run(['shutdown', '/r', '/t', '0', '/f'], check=True)
        except subprocess.CalledProcessError as e:
            # If the command fails (non-zero exit), treat error as we would on Linux
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, f"Reboot command failed: {e}")), UNAUTHORIZED
    else:
        try:
            subprocess.run(['reboot'], check=True)
        except subprocess.CalledProcessError as e:
            if e.returncode == 1:
                # Exit code 1: not authorized (permission denied)
                return jsonify(build_error(ERROR_NOT_AUTHORIZED, "System did not respond correctly.")), UNAUTHORIZED
            elif e.returncode == 143:
                # Exit code 143: process terminated (system is rebooting)
                pass
            else:
                # Other error codes: treat as unexpected failure
                raise
    return jsonify(), ACCEPTED

@api.route('/system/shutdown', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def system_shutdown():
    """Shuts down the host system (Admin only).

    Executes the `shutdown` command.

    Returns:
        JSON: Empty body. 202 Accepted on success.
              401 Unauthorized if the user is not an admin.
    """
    if not check_admin(request_user):
        return jsonify(build_error(ERROR_NOT_AUTHORIZED, "Insufficient privileges for request.")), UNAUTHORIZED

    if platform.system().lower() == 'windows':
        try:
            subprocess.run(['shutdown', '/s', '/t', '0', '/f'], check=True)
        except subprocess.CalledProcessError as e:
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, f"Shutdown failed: {e}")), UNAUTHORIZED
    else:
        try:
            # Use 'poweroff' for immediate shutdown on Unix
            subprocess.run(['poweroff'], check=True)
        except subprocess.CalledProcessError as e:
            # If the shutdown command fails (e.g., permission denied)
            return jsonify(build_error(ERROR_NOT_AUTHORIZED, "System did not respond correctly.")), UNAUTHORIZED
    return jsonify(), ACCEPTED
