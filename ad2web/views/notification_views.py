from flask import Blueprint, render_template, request, flash, redirect, url_for, abort, jsonify
from flask_login import login_required, current_user
from ad2web.services.notification_service import notification_service
from ad2web.forms.notification_form import (CreateNotificationForm, EditNotificationMessageForm,
    EmailNotificationForm, PushoverNotificationForm, TwilioNotificationForm, TwiMLNotificationForm,
    ProwlNotificationForm, GrowlNotificationForm, CustomPostForm, ZoneFilterForm,
    NotificationReviewForm, UPNPPushNotificationForm)
from ad2web.notifications.constants import (NOTIFICATION_TYPES, DEFAULT_SUBSCRIPTIONS,
    ZONE_FAULT, ZONE_RESTORE)  # keep constants for event types

# Blueprint for notifications
notification_bp = Blueprint('notifications', __name__, url_prefix='/settings/notifications')

@notification_bp.context_processor
def notifications_context_processor():
    # Provide type mapping and event types to templates
    from ad2web.notifications.constants import EVENT_TYPES, NOTIFICATION_TYPES  # ensure latest mapping
    # TYPE_DETAILS mapping type name->(code, form class) for quick lookup (if needed in templates)
    TYPE_DETAILS = {
        'email':    ('EMAIL', EmailNotificationForm),
        'pushover': ('PUSHOVER', PushoverNotificationForm),
        'twilio':   ('TWILIO', TwilioNotificationForm),
        'prowl':    ('PROWL', ProwlNotificationForm),
        'growl':    ('GROWL', GrowlNotificationForm),
        'custom':   ('CUSTOM', CustomPostForm),
        'twiml':    ('TWIML', TwiMLNotificationForm),
        'upnppush': ('UPNPPUSH', UPNPPushNotificationForm),
        # 'matrix': ('MATRIX', MatrixNotificationForm)  # include if Matrix form is implemented
    }
    return {
        'TYPES': NOTIFICATION_TYPES,
        'TYPE_DETAILS': TYPE_DETAILS,
        'EVENT_TYPES': EVENT_TYPES
    }

@notification_bp.route('/')
@login_required
def index():
    # Use service to fetch notifications (filtering by user if not admin)
    notification_list = notification_service.get_notifications(current_user)
    return render_template('notifications/index.html',
                           notifications=notification_list, active='notifications')

@notification_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    form = CreateNotificationForm()
    if form.validate_on_submit():
        # User selected a type, proceed to type-specific creation form
        selected_type = form.type.data
        return redirect(url_for('notifications.create_by_type', type=selected_type))
    # Render initial create form (if AJAX, we'll load this into a modal)
    return render_template('notifications/create.html', form=form, active='notifications')

@notification_bp.route('/create/<string:type>', methods=['GET', 'POST'])
@login_required
def create_by_type(type):
    # Ensure type key is valid
    type = type.lower()
    # Mapping from type name to form class and numeric code
    type_form_map = {
        'email':    (EmailNotificationForm,    None),
        'pushover': (PushoverNotificationForm, None),
        'twilio':   (TwilioNotificationForm,   None),
        'twiml':    (TwiMLNotificationForm,    None),
        'prowl':    (ProwlNotificationForm,    None),
        'growl':    (GrowlNotificationForm,    None),
        'custom':   (CustomPostForm,          None),
        'upnppush': (UPNPPushNotificationForm, None),
        # 'matrix': (MatrixNotificationForm, None)  # if Matrix type supported
    }
    if type not in type_form_map:
        abort(404)
    FormClass, _ = type_form_map[type]
    form = FormClass()
    # Set the type field in form (if applicable)
    if hasattr(form, 'type'):
        # If using numeric codes for type, convert string to code
        try:
            type_code = int(type)  # if type was passed as numeric string
        except ValueError:
            # lookup in NOTIFICATION_TYPES by name
            from ad2web.notifications.constants import NOTIFICATION_TYPES
            type_code = None
            for code, name in NOTIFICATION_TYPES.items():
                if name.lower() == type:
                    type_code = code
                    break
        form.type.data = type_code
    # Initialize default event subscriptions for new notifications
    if not form.is_submitted():
        if hasattr(form, 'subscriptions'):
            form.subscriptions.data = [str(evt) for evt in DEFAULT_SUBSCRIPTIONS]
    # Handle form submission
    if form.validate_on_submit():
        # Create the new Notification via service
        new_notif = notification_service.create_notification(
            notif_type=(type_code if 'type_code' in locals() else type),
            description=form.description.data,
            user=current_user,
            settings_form=form
        )
        # Determine next step: zone filter if zone events subscribed, else review/finish
        zone_events = str(ZONE_FAULT) in getattr(form, 'subscriptions').data or str(ZONE_RESTORE) in getattr(form, 'subscriptions').data
        if request.is_xhr:
            # AJAX request: return JSON result instead of redirect
            result = {"status": "created", "id": new_notif.id, "description": new_notif.description,
                      "type": NOTIFICATION_TYPES[new_notif.type], "enabled": bool(new_notif.enabled)}
            if zone_events:
                result["zone_filter_required"] = True
                result["zone_filter_url"] = url_for('notifications.zone_filter', id=new_notif.id)
            return jsonify(result), 201
        else:
            if zone_events:
                return redirect(url_for('notifications.zone_filter', id=new_notif.id))
            return redirect(url_for('notifications.review', id=new_notif.id))
    # GET or validation failure:
    return render_template('notifications/create_by_type.html', form=form, type=type, active='notifications')

@notification_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    notif = notification_service.get_notification(id)
    if not notif:
        abort(404)
    # Permission check: only owner or admin can edit
    if notif.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    # Determine form class based on notification type
    # Map type code to form class
    type_code = notif.type
    # Reverse mapping from code to type name (assuming NOTIFICATION_TYPES)
    from ad2web.notifications.constants import NOTIFICATION_TYPES
    type_name = NOTIFICATION_TYPES.get(type_code, "").lower()
    form_class = {
        'email': EmailNotificationForm,
        'pushover': PushoverNotificationForm,
        'twilio': TwilioNotificationForm,
        'twiml': TwiMLNotificationForm,
        'prowl': ProwlNotificationForm,
        'growl': GrowlNotificationForm,
        'custom': CustomPostForm,
        'upnppush': UPNPPushNotificationForm,
        # 'matrix': MatrixNotificationForm
    }.get(type_name)
    if form_class is None:
        abort(400)
    # Instantiate form. For GET, populate from existing notification; for POST, validate input.
    form = form_class(obj=notif if request.method == 'GET' else None)
    if request.method == 'GET':
        # Pre-fill form from existing settings
        if hasattr(form, 'populate_from_settings'):
            form.populate_from_settings(id)
    if form.validate_on_submit():
        # Save changes via service
        notification_service.update_notification(notif, settings_form=form, new_description=form.description.data)
        # Determine if zone filter step needed
        zone_events = False
        if hasattr(form, 'subscriptions'):
            zone_events = str(ZONE_FAULT) in form.subscriptions.data or str(ZONE_RESTORE) in form.subscriptions.data
        if request.is_xhr:
            # AJAX: return JSON indicating success and any next step
            result = {"status": "updated", "id": notif.id, "description": notif.description, "enabled": bool(notif.enabled)}
            if zone_events:
                result["zone_filter_required"] = True
                result["zone_filter_url"] = url_for('notifications.zone_filter', id=notif.id)
            return jsonify(result)
        else:
            if zone_events:
                return redirect(url_for('notifications.zone_filter', id=notif.id))
            return redirect(url_for('notifications.review', id=notif.id))
    return render_template('notifications/edit.html', form=form, id=id, notification=notif, active='notifications')

@notification_bp.route('/<int:id>/zones', methods=['GET', 'POST'])
@login_required
def zone_filter(id):
    notif = notification_service.get_notification(id)
    if not notif:
        abort(404)
    # Only owner or admin can modify zone filters
    if notif.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    form = ZoneFilterForm()
    # Build zone choices via zone_service
    form.zones.choices = notification_service.get_zone_choices()
    if request.method == 'GET':
        form.populate_from_settings(id=id)
    if form.validate_on_submit():
        # Update zone filter settings
        notification_service.update_zone_filter(notif, form.zones.data)
        if request.is_xhr:
            # AJAX: indicate success, possibly instruct to proceed to review
            return jsonify({"status": "zones_updated"})
        else:
            return redirect(url_for('notifications.review', id=id))
    return render_template('notifications/zone_filter.html', form=form, id=id, active='notifications')

@notification_bp.route('/<int:id>/remove', methods=['POST', 'GET'])
@login_required
def remove(id):
    notif = notification_service.get_notification(id)
    if not notif:
        abort(404)
    if notif.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    notification_service.delete_notification(notif)
    if request.is_xhr:
        # For AJAX, return success status (and maybe a message)
        return jsonify({"status": "deleted"})
    flash('Notification deleted.', 'success')
    return redirect(url_for('notifications.index'))

@notification_bp.route('/<int:id>/copy', methods=['POST', 'GET'])
@login_required
def copy_notification(id):
    notif = notification_service.get_notification(id)
    if not notif:
        abort(404)
    if notif.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    new_notif = notification_service.copy_notification(notif)
    if request.is_xhr:
        # Return details of new notification to add to list dynamically
        return jsonify({
            "status": "cloned",
            "id": new_notif.id,
            "description": new_notif.description,
            "type": new_notif.type,
            "enabled": bool(new_notif.enabled),
            "owner": new_notif.user.name if hasattr(new_notif, 'user') else ""
        })
    flash('Notification cloned.', 'success')
    return redirect(url_for('notifications.index'))

@notification_bp.route('/<int:id>/toggle', methods=['POST', 'GET'])
@login_required
def toggle_notification(id):
    notif = notification_service.get_notification(id)
    if not notif:
        abort(404)
    if notif.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    new_status = notification_service.toggle_notification(notif)
    if request.is_xhr:
        return jsonify({"status": new_status.lower()})
    flash(f'Notification {new_status}.', 'success')
    return redirect(url_for('notifications.index'))

@notification_bp.route('/<int:id>/review', methods=['GET', 'POST'])
@login_required
def review(id):
    notif = notification_service.get_notification(id)
    if not notif:
        abort(404)
    if notif.user_id != current_user.id and not current_user.is_admin():
        abort(403)
    form = NotificationReviewForm()
    if form.validate_on_submit():
        error = None
        # If "Test" button was pressed
        if form.buttons.test.data:
            # Attempt to send a test notification via AlarmDecoder decoder
            try:
                error = current_app.decoder.test_notifier(notif.id)
            except Exception as e:
                error = str(e)
            if error:
                flash(f'Error sending test notification: {error}', 'error')
            else:
                flash('Test notification sent.', 'success')
        else:
            flash('Notification saved.', 'success')
        if error is None:
            # If no error or just saved, return to main list
            return redirect(url_for('notifications.index'))
    return render_template('notifications/review.html', notification=notif, form=form, active='notifications')

@notification_bp.route('/messages', methods=['GET'])
@login_required
def messages():
    # Only admins can view notification messages
    if not current_user.is_admin():
        abort(403)
    messages = NotificationMessage.query.all()
    return render_template('notifications/messages.html', messages=messages, active='notifications')

@notification_bp.route('/messages/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_message(id):
    if not current_user.is_admin():
        abort(403)
    message = NotificationMessage.query.filter_by(id=id).first_or_404()
    form = EditNotificationMessageForm()
    if request.method == 'GET':
        form.id.data = message.id
        form.text.data = message.text
    if form.validate_on_submit():
        message.text = form.text.data
        db = notification_service  # use service if it had DB, otherwise directly commit
        from ad2web.extensions import db as _db
        _db.session.add(message)
        _db.session.commit()
        flash('The notification message has been updated.', 'success')
        return redirect(url_for('notifications.messages'))
    return render_template('notifications/edit_message.html', form=form, message_id=message.id, active='notifications')
