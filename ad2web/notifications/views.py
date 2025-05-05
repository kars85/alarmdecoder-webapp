from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user
from ..services.notification_service import NotificationService
from .forms import (
    CreateNotificationForm, EditNotificationMessageForm,
    EmailNotificationForm, PushoverNotificationForm, TwilioNotificationForm, TwiMLNotificationForm,
    ProwlNotificationForm, GrowlNotificationForm, CustomPostForm, ZoneFilterForm, ReviewNotificationForm,
    MatrixNotificationForm, UPNPPushNotificationForm
)
from .constants import NOTIFICATION_TYPES, DEFAULT_SUBSCRIPTIONS, ZONE_FAULT, ZONE_RESTORE

notifications = Blueprint('notifications', __name__, url_prefix='/settings/notifications')

@notifications.context_processor
def notifications_context():
    # Provide common data to notification templates (unchanged)
    from .constants import NOTIFICATION_TYPES, EVENT_TYPES
    return {
        'TYPES': NOTIFICATION_TYPES,
        'EVENT_TYPES': EVENT_TYPES,
    }

@notifications.route('/')
@login_required
def index():
    # Fetch notifications list via service
    notification_list = NotificationService.get_notifications(current_user)
    use_ssl = False
    try:
        # Example of retrieving a setting value (unchanged logic)
        from ..settings import Setting
        use_ssl = Setting.get_by_name('use_ssl', default=False).value
    except Exception:
        pass
    return render_template('notifications/index.html',
                           notifications=notification_list,
                           active='notifications', ssl=use_ssl)

@notifications.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    # Ensure the notification exists and current_user has access
    try:
        notification = NotificationService.get_notification(id, current_user)
    except (NotFound, Forbidden) as e:
        abort(e.code)  # Will produce 404 or 403 as appropriate

    # Determine the appropriate form class for this notification type
    type_code = notification.type
    # Find corresponding form class from NOTIFICATION_TYPE_DETAILS mapping
    # We invert the mapping to get form class by type code
    form_class = None
    for type_str, (code, form_cls) in NOTIFICATION_TYPE_DETAILS.items():
        if code == type_code:
            form_class = form_cls
            break
    if form_class is None:
        abort(404)  # Unknown notification type

    # Instantiate the form. On GET, populate with current object; on POST, use submitted data.
    form = form_class(obj=notification if request.method == 'GET' else None)
    if request.method == 'GET':
        # Pre-fill form fields from existing settings
        form.populate_from_settings(id)  # Uses NotificationSetting values to fill the form
    if form.validate_on_submit():
        # Build description and settings data from form inputs
        desc = form.description.data
        settings = {}
        # Common settings (if present on this form)
        if hasattr(form, 'subscriptions'):
            # Convert list of events to JSON map of event->True
            subs_dict = {str(k): True for k in form.subscriptions.data}
            settings['subscriptions'] = NotificationService._json_dumps(subs_dict)
        if hasattr(form, 'time_field'):
            settings['starttime'] = form.time_field.starttime.data or '00:00:00'
            settings['endtime'] = form.time_field.endtime.data or '23:59:59'
            settings['delay'] = form.time_field.delaytime.data or 0
            settings['suppress'] = form.time_field.suppress.data or False
        if hasattr(form, 'suppress_timestamp'):
            settings['suppress_timestamp'] = form.suppress_timestamp.data or False
        # Type-specific settings
        if type_code == NOTIFICATION_TYPE_DETAILS['email'][0]:  # Email
            settings.update({
                'source': form.form_field.source.data,
                'destination': form.form_field.destination.data,
                'subject': form.form_field.subject.data,
                'server': form.form_field.server.data,
                'port': form.form_field.port.data,
                'tls': form.form_field.tls.data or False,
                'ssl': form.form_field.ssl.data or False,
                'authentication_required': form.form_field.authentication_required.data or False,
                'username': form.form_field.username.data or "",
                'password': form.form_field.password.data or ""
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['pushover'][0]:  # Pushover
            settings.update({
                'token': form.form_field.token.data,
                'user_key': form.form_field.user_key.data,
                'priority': form.form_field.priority.data,
                'title': form.form_field.title.data or ""
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['twilio'][0]:  # Twilio
            settings.update({
                'account_sid': form.form_field.account_sid.data,
                'auth_token': form.form_field.auth_token.data,
                'number_to': form.form_field.number_to.data,
                'number_from': form.form_field.number_from.data
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['twiml'][0]:  # TwiML (Twilio via Twimlet URL)
            settings.update({
                'account_sid': form.form_field.account_sid.data,
                'auth_token': form.form_field.auth_token.data,
                'number_to': form.form_field.number_to.data,
                'number_from': form.form_field.number_from.data,
                'twimlet_url': form.form_field.twimlet_url.data
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['prowl'][0]:  # Prowl
            settings.update({
                'prowl_api_key': form.form_field.prowl_api_key.data,
                'prowl_app_name': form.form_field.prowl_app_name.data,
                'prowl_priority': form.form_field.prowl_priority.data
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['growl'][0]:  # Growl
            settings.update({
                'growl_hostname': form.form_field.growl_hostname.data,
                'growl_port': form.form_field.growl_port.data,
                'growl_password': form.form_field.growl_password.data or "",
                'growl_title': form.form_field.growl_title.data,
                'growl_priority': form.form_field.growl_priority.data
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['custom'][0]:  # Custom HTTP
            settings.update({
                'custom_url': form.form_field.custom_url.data,
                'custom_path': form.form_field.custom_path.data,
                'is_ssl': form.form_field.is_ssl.data or False,
                'method': form.form_field.method.data,
                'post_type': form.form_field.post_type.data,
                'require_auth': form.form_field.require_auth.data or False,
                'auth_username': form.form_field.auth_username.data or "",
                'auth_password': form.form_field.auth_password.data or "",
                'custom_values': form.form_field.custom_values.data  # List of {custom_key, custom_value} dicts
            })
        elif type_code == NOTIFICATION_TYPE_DETAILS['matrix'][0]:  # Matrix.org
            settings.update({
                'domain': form.form_field.domain.data,
                'room_id': form.form_field.room_id.data,
                'token': form.form_field.token.data,
                'custom_values': form.form_field.custom_values.data  # Additional data similar to Custom
            })
        # (UPNPPUSH type is not expected here, since UPNPPushNotificationForm did not use EditNotificationForm.
        # UPNP notifications are not edited via this route in the original UI.)

        # Save changes via service
        NotificationService.update_notification(id, current_user, description=desc, settings_data=settings)
        # If certain event types selected, redirect to zone filter step, otherwise to review.
        if str(ZONE_FAULT) in form.subscriptions.data or str(ZONE_RESTORE) in form.subscriptions.data:
            return redirect(url_for('notifications.zone_filter', id=id))
        return redirect(url_for('notifications.review', id=id))
    # GET or validation failure
    use_ssl = False
    try:
        from ..settings import Setting
        use_ssl = Setting.get_by_name('use_ssl', default=False).value
    except Exception:
        pass
    return render_template('notifications/edit.html', form=form, id=id, notification=notification,
                           active='notifications', ssl=use_ssl, legend=getattr(form, 'legend', None))

@notifications.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    form = CreateNotificationForm()
    if form.validate_on_submit():
        # User selected a notification type; proceed to detailed creation step
        return redirect(url_for('notifications.create_by_type', type=form.type.data))
    use_ssl = False
    try:
        from ..settings import Setting
        use_ssl = Setting.get_by_name('use_ssl', default=False).value
    except Exception:
        pass
    return render_template('notifications/create.html', form=form, active='notifications', ssl=use_ssl)

@notifications.route('/create/<string:type>', methods=['GET', 'POST'])
@login_required
def create_by_type(type):
    if type not in NOTIFICATION_TYPE_DETAILS:
        abort(404)
    type_id, form_class = NOTIFICATION_TYPE_DETAILS[type]
    form = form_class()
    form.type.data = type_id  # set hidden type field
    if request.method == 'GET':
        # Initialize default event subscriptions on first load
        if hasattr(form, 'subscriptions'):
            form.subscriptions.data = [str(k) for k in DEFAULT_SUBSCRIPTIONS]
    if form.validate_on_submit():
        # Build notification data from form
        desc = form.description.data
        settings = {}
        # Base settings for all types (except UPNP, handled separately below)
        if type_id != UPNPPUSH:
            if hasattr(form, 'subscriptions'):
                subs_dict = {str(k): True for k in form.subscriptions.data}
                settings['subscriptions'] = NotificationService._json_dumps(subs_dict)
            if hasattr(form, 'time_field'):
                settings['starttime'] = form.time_field.starttime.data or '00:00:00'
                settings['endtime'] = form.time_field.endtime.data or '23:59:59'
                settings['delay'] = form.time_field.delaytime.data or 0
                settings['suppress'] = form.time_field.suppress.data or False
            if hasattr(form, 'suppress_timestamp'):
                settings['suppress_timestamp'] = form.suppress_timestamp.data or False
        # Type-specific settings
        if type == 'email':
            settings.update({
                'source': form.form_field.source.data,
                'destination': form.form_field.destination.data,
                'subject': form.form_field.subject.data,
                'server': form.form_field.server.data,
                'port': form.form_field.port.data,
                'tls': form.form_field.tls.data or False,
                'ssl': form.form_field.ssl.data or False,
                'authentication_required': form.form_field.authentication_required.data or False,
                'username': form.form_field.username.data or "",
                'password': form.form_field.password.data or ""
            })
        elif type == 'pushover':
            settings.update({
                'token': form.form_field.token.data,
                'user_key': form.form_field.user_key.data,
                'priority': form.form_field.priority.data,
                'title': form.form_field.title.data or ""
            })
        elif type == 'twilio':
            settings.update({
                'account_sid': form.form_field.account_sid.data,
                'auth_token': form.form_field.auth_token.data,
                'number_to': form.form_field.number_to.data,
                'number_from': form.form_field.number_from.data
            })
        elif type == 'twiml':
            settings.update({
                'account_sid': form.form_field.account_sid.data,
                'auth_token': form.form_field.auth_token.data,
                'number_to': form.form_field.number_to.data,
                'number_from': form.form_field.number_from.data,
                'twimlet_url': form.form_field.twimlet_url.data
            })
        elif type == 'prowl':
            settings.update({
                'prowl_api_key': form.form_field.prowl_api_key.data,
                'prowl_app_name': form.form_field.prowl_app_name.data,
                'prowl_priority': form.form_field.prowl_priority.data
            })
        elif type == 'growl':
            settings.update({
                'growl_hostname': form.form_field.growl_hostname.data,
                'growl_port': form.form_field.growl_port.data,
                'growl_password': form.form_field.growl_password.data or "",
                'growl_title': form.form_field.growl_title.data,
                'growl_priority': form.form_field.growl_priority.data
            })
        elif type == 'custom':
            settings.update({
                'custom_url': form.form_field.custom_url.data,
                'custom_path': form.form_field.custom_path.data,
                'is_ssl': form.form_field.is_ssl.data or False,
                'method': form.form_field.method.data,
                'post_type': form.form_field.post_type.data,
                'require_auth': form.form_field.require_auth.data or False,
                'auth_username': form.form_field.auth_username.data or "",
                'auth_password': form.form_field.auth_password.data or "",
                'custom_values': form.form_field.custom_values.data
            })
        elif type == 'matrix':
            settings.update({
                'domain': form.form_field.domain.data,
                'room_id': form.form_field.room_id.data,
                'token': form.form_field.token.data,
                'custom_values': form.form_field.custom_values.data
            })
        elif type == 'upnppush':
            # UPNP Push: only store token (other settings like events/times not applicable or fixed)
            settings['token'] = form.form_field.token.data or ""
        # Create notification via service
        new_notif = NotificationService.create_notification(current_user, type_id, desc, settings)
        # Determine next step: zone filter if zone events selected, else review
        if 'subscriptions' in settings:
            subs_json = settings['subscriptions']
            # If zone-related events in chosen subscriptions, proceed to zone filter form
            if str(ZONE_FAULT) in subs_json or str(ZONE_RESTORE) in subs_json:
                return redirect(url_for('notifications.zone_filter', id=new_notif.id))
        return redirect(url_for('notifications.review', id=new_notif.id))
    use_ssl = False
    try:
        from ..settings import Setting
        use_ssl = Setting.get_by_name('use_ssl', default=False).value
    except Exception:
        pass
    return render_template('notifications/create_by_type.html', form=form, type=type,
                           active='notifications', ssl=use_ssl, legend=getattr(form, 'legend', None))

def _json_dumps(data):
    """Utility to JSON-encode data (used by service or controllers)."""
    import json
    return json.dumps(data)

@notifications.route('/<int:id>/zones', methods=['GET', 'POST'])
@login_required
def zone_filter(id):
    # Only the owner or admin should update zone filters; fetch to verify access
    try:
        NotificationService.get_notification(id, current_user)
    except (NotFound, Forbidden) as e:
        abort(e.code)
    form = ZoneFilterForm()
    form.zones.choices = [(str(i), f"Zone {i:02d}") for i in range(1, 100)]
    # Replace choices with real zone names for existing zones
    from ..zones import Zone
    zones = Zone.query.all()
    for z in zones:
        if 1 <= z.zone_id < len(form.zones.choices) + 1:
            form.zones.choices[z.zone_id - 1] = (str(z.zone_id), f"Zone {z.zone_id:02d} - {z.name}")
    if request.method == 'GET':
        # Prepopulate selected zones from current settings
        form.populate_from_settings(id=id)
    if form.validate_on_submit():
        # Convert selected zones list to JSON list string for storage
        selected_zones = form.zones.data  # list of zone IDs as strings
        zone_filter_value = NotificationService._json_dumps(selected_zones)
        # Update the notification's zone_filter setting
        NotificationService.update_notification(id, current_user, settings_data={'zone_filter': zone_filter_value})
        return redirect(url_for('notifications.review', id=id))
    return render_template('notifications/zone_filter.html', id=id, form=form, active='notifications')

@notifications.route('/<int:id>/remove', methods=['POST', 'GET'])
@login_required
def remove(id):
    # Delete a notification (accessible to owner or admin only)
    try:
        NotificationService.delete_notification(id, current_user)
    except (NotFound, Forbidden) as e:
        abort(e.code)
    flash('Notification deleted.', 'success')
    # For normal browser request, redirect back to list. (AJAX requests handle UI update on client side.)
    if request.is_xhr:
        return jsonify({'success': True})
    return redirect(url_for('notifications.index'))

@notifications.route('/<int:id>/copy', methods=['POST', 'GET'])
@login_required
def copy_notification(id):
    # Clone a notification (permissions check inside service)
    try:
        new_notif = NotificationService.copy_notification(id, current_user)
    except (NotFound, Forbidden) as e:
        abort(e.code)
    flash('Notification cloned.', 'success')
    if request.is_xhr:
        # Return the new notification ID if needed for client-side use (optional)
        return jsonify({'success': True, 'new_id': new_notif.id})
    return redirect(url_for('notifications.index'))

@notifications.route('/<int:id>/toggle', methods=['POST', 'GET'])
@login_required
def toggle_notification(id):
    # Toggle enable/disable status
    try:
        new_status = NotificationService.toggle_notification(id, current_user)
    except (NotFound, Forbidden) as e:
        abort(e.code)
    # Prepare status text for user feedback
    status_text = "Enabled" if new_status else "Disabled"
    flash(f'Notification {status_text}.', 'success')
    if request.is_xhr:
        return jsonify({'enabled': new_status})
    return redirect(url_for('notifications.index'))

@notifications.route('/<int:id>/review', methods=['GET', 'POST'])
@login_required
def review(id):
    # Final review step: allow testing or finishing saving
    try:
        notification = NotificationService.get_notification(id, current_user)
    except (NotFound, Forbidden) as e:
        abort(e.code)
    form = ReviewNotificationForm()
    if form.validate_on_submit():
        error = None
        if form.buttons.test.data:
            # User clicked "Save & Test"
            error = NotificationService.test_notification(id, current_user)
            if error:
                flash(f'Error sending test notification: {error}', 'error')
            else:
                flash('Test notification sent.', 'success')
        else:
            # User clicked "Save" (without test)
            flash('Notification saved.', 'success')
        if error is None:
            # On successful save (or test), go back to list
            return redirect(url_for('notifications.index'))
        # If error, remain on review page to allow another attempt
    return render_template('notifications/review.html', notification=notification,
                           form=form, active='notifications')

@notifications.route('/messages', methods=['GET'])
@login_required
def messages():
    # Only admin can view system notification messages
    if not current_user.is_admin():
        abort(403)
    messages = NotificationService.get_notification_messages(current_user)
    return render_template('notifications/messages.html', messages=messages, active='notifications')

@notifications.route('/messages/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_message(id):
    # Only admin can edit notification message templates
    if not current_user.is_admin():
        abort(403)
    message = NotificationService.get_notification(id=id, user=current_user)
    # (Alternatively, we could use a separate service method for messages, but reuse get_notification for brevity)
    form = EditNotificationMessageForm()
    if request.method == 'GET':
        form.id.data = message.id
        form.text.data = message.text
    if form.validate_on_submit():
        NotificationService.update_notification_message(message.id, form.text.data, current_user)
        flash('The notification message has been updated.', 'success')
        return redirect(url_for('notifications.messages'))
    return render_template('notifications/edit_message.html', form=form, message_id=message.id, active='notifications')
