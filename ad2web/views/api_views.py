from flask import Blueprint, current_app, request, jsonify, Response, render_template, redirect, url_for
from flask_login import login_required
from http.client import OK, CREATED, ACCEPTED, NO_CONTENT, UNAUTHORIZED, NOT_FOUND, CONFLICT, UNPROCESSABLE_ENTITY, SERVICE_UNAVAILABLE

from ..decorators import admin_required, crossdomain
from ..services import api_service  # our new service layer
from ..settings.models import Setting  # to check system-wide settings
from ..user import User, ADMIN  # user model and role constant
from ..zones import Zone
from ..notifications import Notification, NotificationSetting
from ..cameras import Camera

# Initialize a single Blueprint for all API-related routes (UI and REST endpoints)
api_bp = Blueprint('api', __name__, url_prefix='/api')

# Global variable to hold the user associated with a request (set in decorator)
request_user = None

# Utility functions for API responses and authorization
def build_error(code, message):
    """Construct a standardized JSON error response body."""
    return {"error": {"code": code, "message": message}}

def build_success(data=None, message=None):
    """Construct a standardized JSON success response body."""
    resp = {"success": True}
    if message:
        resp["message"] = message
    if data is not None:
        resp["data"] = data
    return resp

def check_admin(user):
    """Check if the given user has administrative privileges."""
    return (user is not None) and (getattr(user, 'role_code', None) == ADMIN)

def api_authorized(f):
    """
    Decorator to check for valid API key authorization and system readiness.
    Verifies the API key (from Authorization header or 'apikey' param), checks
    that the AlarmDecoder device is initialized, and sets request_user.
    """
    from functools import wraps
    @wraps(f)
    def wrapped(*args, **kwargs):
        global request_user
        request_user = None
        # Only JSON body is accepted for POST/PUT
        if request.method in ['POST', 'PUT']:
            req_data = request.get_json(silent=True)
            if req_data is None:
                return jsonify(build_error(7102, "Missing request body or incorrect content type.")), UNPROCESSABLE_ENTITY  # ERROR_MISSING_BODY&#8203;:contentReference[oaicite:0]{index=0}

        # Get API key from header or query parameter
        api_key_val = request.headers.get('Authorization') or request.args.get('apikey')
        # Authenticate the API key using the service layer
        user = api_service.authenticate_api_key(api_key_val)
        if not user:
            return jsonify(build_error(7100, "Not authorized.")), UNAUTHORIZED  # ERROR_NOT_AUTHORIZED&#8203;:contentReference[oaicite:1]{index=1}

        # Check global setting if API functionality is enabled
        api_enabled = Setting.get_by_name('api_enabled', default=True).value
        if not api_enabled or str(api_enabled).lower() in ['0', 'false']:
            return jsonify(build_error(7100, "API access is currently disabled.")), UNAUTHORIZED

        # Ensure the AlarmDecoder device is initialized
        if current_app.decoder.device is None:
            return jsonify(build_error(7101, "Device has not finished initializing.")), SERVICE_UNAVAILABLE  # ERROR_DEVICE_NOT_INITIALIZED

        # Set the global request_user for use in the endpoint
        request_user = user
        return f(*args, **kwargs)
    return wrapped

### API Settings UI Routes (protected by login and admin)

@api_bp.route('/')
@login_required
def index():
    """Render the main API settings page (UI)."""
    return render_template('api/index.html')

@api_bp.route('/keys')
@login_required
@admin_required
def keys():
    """Admin-only page for managing API keys."""
    users = User.query.all()
    # Check if API is globally enabled (to inform the UI)
    api_enabled = bool(Setting.get_by_name('api_enabled', default=True).value)
    return render_template('api/keys.html', users=users, api_enabled=api_enabled)

@api_bp.route('/keys/generate', methods=['POST'])
@login_required
@admin_required
def generate_key():
    """Generate or regenerate an API key for a user (Admin only, via AJAX)."""
    user_id = request.form.get('user_id', type=int)
    if user_id is None:
        return jsonify({"success": False, "error": "Missing user_id"}), UNPROCESSABLE_ENTITY
    try:
        apikey = api_service.create_or_update_api_key(user_id)
    except ValueError as e:
        # Invalid user_id
        return jsonify({"success": False, "error": str(e)}), NOT_FOUND
    # Operation successful – return the new API key
    data = {"user_id": user_id, "api_key": apikey.key}
    return jsonify(build_success(data=data, message="API key generated.")), OK

@api_bp.route('/keys/disable', methods=['POST'])
@login_required
@admin_required
def disable_key():
    """Disable (revoke) the API key for a user (Admin only, via AJAX)."""
    user_id = request.form.get('user_id', type=int)
    if user_id is None:
        return jsonify({"success": False, "error": "Missing user_id"}), UNPROCESSABLE_ENTITY
    try:
        api_service.disable_api_key(user_id)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), NOT_FOUND
    # Success (no content needed beyond success message)
    return jsonify(build_success(message="API key disabled.")), OK

### Core API v1 Endpoints (RESTful API)

@api_bp.route('/v1/alarmdecoder', methods=['GET', 'OPTIONS'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def get_alarmdecoder_status():
    """Get the current status of the AlarmDecoder device."""
    device = current_app.decoder.device
    # Determine panel type as string
    mode = device.mode
    if mode == current_app.decoder.panels.ADEMCO:
        mode = 'ADEMCO'
    elif mode == current_app.decoder.panels.DSC:
        mode = 'DSC'
    else:
        mode = 'UNKNOWN'
    # Gather status information
    relay_status = [
        {"address": addr, "channel": ch, "value": val}
        for (addr, ch), val in device._relay_status.items()
    ]
    faulted_zones = [
        z.zone for zid, z in device._zonetracker.zones.items() if z.status != device.zonetracking.Zone.CLEAR
    ]
    status = {
        "panel_type": mode,
        "panel_powered": device._power_status,
        "panel_ready": getattr(device, "_ready_status", True),
        "panel_alarming": device._alarm_status,
        "panel_bypassed": device._bypass_status,
        "panel_armed": device._armed_status,
        "panel_armed_stay": getattr(device, "_armed_stay", False),
        "faulted_zones": faulted_zones,
        "relay_status": relay_status
    }
    return jsonify(build_success(data=status)), OK  # Wrap status in {"success": True, "data": {...}}&#8203;:contentReference[oaicite:2]{index=2}

@api_bp.route('/v1/alarmdecoder/send', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def send_alarmdecoder_command():
    """Send a raw command string to the AlarmDecoder (e.g., to arm/disarm)."""
    req = request.get_json() or {}
    cmd = req.get('command')
    if not cmd:
        return jsonify(build_error(7103, "Missing 'command' field.")), UNPROCESSABLE_ENTITY  # ERROR_MISSING_FIELD
    # Send the command to the alarm panel
    current_app.decoder.device.send(cmd)
    return jsonify(build_success(message="Command sent.")), ACCEPTED

@api_bp.route('/v1/alarmdecoder/event', methods=['SUBSCRIBE', 'UNSUBSCRIBE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder_event():
    """Handle subscription/unsubscription to AlarmDecoder events (for UPNP)."""
    # For SUBSCRIBE/UNSUBSCRIBE, integrate with internal notifier
    host = request.remote_addr or request.host
    if request.method == 'SUBSCRIBE':
        # Subscribe the client for events
        current_app.decoder._notifier_system.add_subscriber(host, request.url_root + 'api/v1/alarmdecoder/event')
        # Respond with required headers for UPNP
        resp = Response(status=200)
        resp.headers['SERVER'] = "Linux UPnP/1.0 AlarmDecoder"
        resp.headers['X-User-Agent'] = "AD2WEB"
        resp.headers['SID'] = "uuid:{}".format(current_app.decoder._notifier_system.last_sid)
        return resp
    elif request.method == 'UNSUBSCRIBE':
        sid = request.headers.get("SID")
        current_app.decoder._notifier_system.remove_subscriber(host, sid)
        resp = Response(status=200)
        resp.headers['SERVER'] = "Linux UPnP/1.0 AlarmDecoder"
        resp.headers['X-User-Agent'] = "AD2WEB"
        resp.headers['SID'] = sid
    return resp

@api_bp.route('/v1/alarmdecoder/reboot', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def reboot_alarmdecoder():
    """Reboot the AlarmDecoder device (Admin API key required)."""
    if not check_admin(request_user):
        return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
    current_app.decoder.device.reboot()
    return jsonify(build_success(message="Device rebooted.")), NO_CONTENT

@api_bp.route('/v1/alarmdecoder/configuration', methods=['GET', 'PUT'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def alarmdecoder_configuration():
    """Get or update the AlarmDecoder configuration (Admin required for PUT)."""
    device = current_app.decoder.device
    if request.method == 'GET':
        # Build configuration data
        config = {
            "address": device.address,
            "config_bits": device.configbits,
            "address_mask": device.address_mask,
            "emulate_zone": device.emulate_zone,
            "emulate_relay": device.emulate_relay,
            "emulate_lrr": device.emulate_lrr,
            "deduplicate": device.deduplicate,
            "mode": ('ADEMCO' if device.mode == current_app.decoder.panels.ADEMCO
                     else 'DSC' if device.mode == current_app.decoder.panels.DSC
                     else 'UNKNOWN')
        }
        return jsonify(build_success(data={"alarmdecoder_config": config})), OK
    elif request.method == 'PUT':
        if not check_admin(request_user):
            return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
        req = request.get_json() or {}
        # Update configuration from request (only allow certain fields)
        for field in ["address", "config_bits", "address_mask", "emulate_zone", "emulate_relay", "emulate_lrr", "deduplicate"]:
            if field in req:
                setattr(device, field, req[field])
    return jsonify(build_success(message="Configuration updated.")), NO_CONTENT

@api_bp.route('/v1/zones', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def zones_endpoint():
    """Get list of zones or create a new zone (Admin only for POST)."""
    if request.method == 'GET':
        zones = Zone.query.all()
        data = {"zones": [z.to_dict() if hasattr(z, 'to_dict') else {"id": z.id, "zone_id": z.zone_id, "name": z.name, "description": z.description} for z in zones]}
        return jsonify(build_success(data=data)), OK
    elif request.method == 'POST':
        if not check_admin(request_user):
            return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
        req = request.get_json() or {}
        zone_id = req.get('zone_id')
        name = req.get('name')
        description = req.get('description', "")
        if zone_id is None or name is None:
            return jsonify(build_error(7103, "Missing 'zone_id' or 'name' field.")), UNPROCESSABLE_ENTITY
        # Prevent duplicate zone ID
        if Zone.query.filter_by(zone_id=zone_id).first():
            return jsonify(build_error(7105, "Zone already exists.")), CONFLICT
        # Create and save new zone
        zone = Zone(zone_id=zone_id, name=name, description=description)
        db = current_app.extensions['sqlalchemy'].db
        db.session.add(zone)
        db.session.commit()
    return jsonify(build_success(data={"zone": {"id": zone.id, "zone_id": zone.zone_id, "name": zone.name, "description": zone.description}},
                                     message="Zone created.")), CREATED

@api_bp.route('/v1/zones/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def zone_detail(id):
    """Retrieve, update, or delete a specific zone (Admin only for PUT/DELETE)."""
    zone = Zone.query.filter_by(id=id).first()
    if not zone:
        return jsonify(build_error(7106, "Zone not found.")), NOT_FOUND
    if request.method == 'GET':
        data = {"zone": zone.to_dict() if hasattr(zone, 'to_dict') else {"id": zone.id, "zone_id": zone.zone_id, "name": zone.name, "description": zone.description}}
        return jsonify(build_success(data=data)), OK
    if not check_admin(request_user):
        return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
    db = current_app.extensions['sqlalchemy'].db
    if request.method == 'PUT':
        req = request.get_json() or {}
        # Update allowed fields
        if 'name' in req:
            zone.name = req['name']
        if 'description' in req:
            zone.description = req['description']
        db.session.commit()
        return jsonify(build_success(message="Zone updated.")), OK
    elif request.method == 'DELETE':
        db.session.delete(zone)
        db.session.commit()
    return jsonify(build_success(message="Zone deleted.")), NO_CONTENT

@api_bp.route('/v1/zones/<int:id>/fault', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def fault_zone(id):
    """Mark a zone as faulted (tripped)."""
    zone = Zone.query.filter_by(id=id).first()
    if not zone:
        return jsonify(build_error(7106, "Zone not found.")), NOT_FOUND
    current_app.decoder.device.fault_zone(zone.zone_id)
    return jsonify(build_success(message=f"Zone {zone.zone_id} faulted.")), OK

@api_bp.route('/v1/zones/<int:id>/restore', methods=['POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def restore_zone(id):
    """Restore a zone to clear status."""
    zone = Zone.query.filter_by(id=id).first()
    if not zone:
        return jsonify(build_error(7106, "Zone not found.")), NOT_FOUND
    current_app.decoder.device.restore_zone(zone.zone_id)
    return jsonify(build_success(message=f"Zone {zone.zone_id} restored.")), OK

@api_bp.route('/v1/notifications', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def notifications_endpoint():
    """Get list of notifications or create a new notification (Admin only for POST)."""
    if request.method == 'GET':
        notifications = Notification.query.all()
        data = {"notifications": [n.to_dict() if hasattr(n, 'to_dict') else {"id": n.id, "name": n.name, "enabled": getattr(n, "enabled", True)} for n in notifications]}
        return jsonify(build_success(data=data)), OK
    elif request.method == 'POST':
        if not check_admin(request_user):
            return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
        req = request.get_json() or {}
        name = req.get('name')
        if not name:
            return jsonify(build_error(7103, "Missing 'name' field.")), UNPROCESSABLE_ENTITY
        notification = Notification(name=name, enabled=1)
        db = current_app.extensions['sqlalchemy'].db
        db.session.add(notification)
        db.session.commit()
    return jsonify(build_success(data={"notification": {"id": notification.id, "name": notification.name, "enabled": True}},
                                     message="Notification created.")), CREATED

@api_bp.route('/v1/notifications/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def notification_detail(id):
    """Retrieve, update, or delete a notification (Admin only for PUT/DELETE)."""
    notif = Notification.query.filter_by(id=id).first()
    if not notif:
        return jsonify(build_error(7106, "Notification not found.")), NOT_FOUND
    if request.method == 'GET':
        data = {"notification": notif.to_dict() if hasattr(notif, 'to_dict') else {"id": notif.id, "name": notif.name, "enabled": bool(getattr(notif, "enabled", 1))}}
        return jsonify(build_success(data=data)), OK
    if not check_admin(request_user):
        return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
    db = current_app.extensions['sqlalchemy'].db
    if request.method == 'PUT':
        req = request.get_json() or {}
        if 'name' in req:
            notif.name = req['name']
        if 'enabled' in req:
            notif.enabled = 1 if req['enabled'] else 0
        db.session.commit()
        return jsonify(build_success(message="Notification updated.")), OK
    elif request.method == 'DELETE':
        db.session.delete(notif)
        db.session.commit()
    return jsonify(build_success(message="Notification deleted.")), NO_CONTENT

@api_bp.route('/v1/cameras', methods=['GET', 'POST'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def cameras_endpoint():
    """Get list of cameras or add a new camera (Admin only for POST)."""
    if request.method == 'GET':
        cameras = Camera.query.all()
        data = {"cameras": [cam.to_dict() if hasattr(cam, 'to_dict') else {"id": cam.id, "name": cam.name, "description": cam.description} for cam in cameras]}
        return jsonify(build_success(data=data)), OK
    elif request.method == 'POST':
        if not check_admin(request_user):
            return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
        req = request.get_json() or {}
        name = req.get('name')
        description = req.get('description', "")
        if not name:
            return jsonify(build_error(7103, "Missing 'name' field.")), UNPROCESSABLE_ENTITY
        camera = Camera(name=name, description=description)
        db = current_app.extensions['sqlalchemy'].db
        db.session.add(camera)
        db.session.commit()
    return jsonify(build_success(data={"camera": {"id": camera.id, "name": camera.name, "description": camera.description}},
                                     message="Camera added.")), CREATED

@api_bp.route('/v1/cameras/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@crossdomain(origin="*", headers=['Content-type', 'api_key', 'Authorization'])
@api_authorized
def camera_detail(id):
    """Retrieve, update, or delete a camera (Admin only for PUT/DELETE)."""
    cam = Camera.query.filter_by(id=id).first()
    if not cam:
        return jsonify(build_error(7106, "Camera not found.")), NOT_FOUND
    if request.method == 'GET':
        data = {"camera": cam.to_dict() if hasattr(cam, 'to_dict') else {"id": cam.id, "name": cam.name, "description": cam.description}}
        return jsonify(build_success(data=data)), OK
    if not check_admin(request_user):
        return jsonify(build_error(7100, "Insufficient privileges for request.")), UNAUTHORIZED
    db = current_app.extensions['sqlalchemy'].db
    if request.method == 'PUT':
        req = request.get_json() or {}
        if 'name' in req:
            cam.name = req['name']
        if 'description' in req:
            cam.description = req['description']
        db.session.commit()
        return jsonify(build_success(message="Camera updated.")), OK
    elif request.method == 'DELETE':
        db.session.delete(cam)
        db.session.commit()
    return jsonify(build_success(message="Camera deleted.")), NO_CONTENT
