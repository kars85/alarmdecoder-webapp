from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import login_required, current_user
from ..forms.keypad_form import KeypadButtonForm, SpecialButtonFormAdemco, SpecialButtonFormDSC
from ..services import keypad_service  # Import the service module
from ..keypad.models import KeypadButton
from alarmdecoder.panels import ADEMCO, DSC

keypad = Blueprint('keypad', __name__, url_prefix='/keypad')

@keypad.route('/')
@login_required
def index():
    """Main keypad UI page (modern layout)."""
    panel_mode = keypad_service.get_panel_mode()
    custom_buttons = keypad_service.get_custom_buttons(current_user)
    special_buttons = keypad_service.get_special_buttons()
    # Choose template based on panel type (None/Ademco uses standard, DSC uses DSC-specific layout)
    if panel_mode is None or panel_mode == ADEMCO:
        return render_template('keypad/index.html', buttons=custom_buttons, special_buttons=special_buttons)
    elif panel_mode == DSC:
        return render_template('keypad/dsc.html', buttons=custom_buttons, special_buttons=special_buttons)
    # Fallback to Ademco template
    return render_template('keypad/index.html', buttons=custom_buttons, special_buttons=special_buttons)

@keypad.route('/legacy')
@login_required
def legacy():
    """Legacy (non-responsive) keypad UI page."""
    panel_mode = keypad_service.get_panel_mode()
    custom_buttons = keypad_service.get_custom_buttons(current_user)
    if panel_mode is None or panel_mode == ADEMCO:
        return render_template('keypad/index_legacy.html', buttons=custom_buttons)
    elif panel_mode == DSC:
        return render_template('keypad/dsc_legacy.html', buttons=custom_buttons)
    return render_template('keypad/index_legacy.html', buttons=custom_buttons)

@keypad.route('/buttons')
@login_required
def list_buttons():
    """List all custom keypad buttons for the current user (management interface)."""
    buttons = keypad_service.get_custom_buttons(current_user)
    # 'active="keypad"' and 'ssl' flag (use_ssl) preserved for template compatibility
    use_ssl = False
    setting_obj = None
    try:
        from ..settings.models import Setting
        setting_obj = Setting.get_by_name('use_ssl', default=False)
    except Exception:
        pass
    if setting_obj:
        use_ssl = bool(setting_obj.value)
    return render_template('keypad/custom_button_index.html', buttons=buttons, active="keypad", ssl=use_ssl)

@keypad.route('/buttons/new', methods=['GET', 'POST'])
@login_required
def create_button():
    """Create a new custom keypad button."""
    form = KeypadButtonForm()
    if form.validate_on_submit():
        new_button = keypad_service.create_custom_button(current_user, form)
        flash('Keypad Button Created', 'success')
        # Return JSON for AJAX requests, or redirect for normal form submissions
        if request.is_xhr:
            return jsonify(success=True, action="create",
                           button={"id": new_button.button_id, "label": new_button.label, "code": new_button.code},
                           message="Keypad Button Created")
        return redirect(url_for('keypad.list_buttons'))
    # If form fails validation under AJAX, return form HTML fragment with errors
    if request.method == 'POST' and request.is_xhr:
        return render_template('keypad/_button_form.html', form=form, action=url_for('keypad.create_button')), 400
    # GET request or non-AJAX POST (render the full page form)
    return render_template('keypad/create.html', form=form)

@keypad.route('/buttons/<int:button_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_button(button_id):
    """Edit an existing custom keypad button."""
    button = KeypadButton.query.filter_by(button_id=button_id).first_or_404()
    # Security: ensure the current user owns this button (or is admin, if applicable)
    if button.user_id != current_user.id:
        abort(403)
    form = KeypadButtonForm(obj=button)
    if form.validate_on_submit():
        keypad_service.update_custom_button(button, form)
        flash('Keypad Button Updated', 'success')
        if request.is_xhr:
            return jsonify(success=True, action="edit",
                           button={"id": button.button_id, "label": button.label, "code": button.code},
                           message="Keypad Button Updated")
        return redirect(url_for('keypad.list_buttons'))
    if request.method == 'POST' and request.is_xhr:
        # On AJAX validation failure, return the form fragment with errors
        return render_template('keypad/_button_form.html', form=form, action=url_for('keypad.edit_button', button_id=button_id)), 400
    return render_template('keypad/edit.html', form=form, id=button_id)

@keypad.route('/buttons/<int:button_id>/delete', methods=['POST'])
@login_required
def delete_button(button_id):
    """Delete a custom keypad button."""
    button = KeypadButton.query.filter_by(button_id=button_id).first_or_404()
    if button.user_id != current_user.id:
        abort(403)
    keypad_service.delete_custom_button(button)
    if request.is_xhr:
        # Respond with JSON on AJAX deletion
        return jsonify(success=True, message="Keypad Button deleted")
    flash('Keypad Button deleted', 'success')
    return redirect(url_for('keypad.list_buttons'))

@keypad.route('/specials', methods=['GET', 'POST'])
@login_required
def special_buttons():
    """Configure special function buttons (Fire, Police, Medical, etc.)."""
    panel_mode = keypad_service.get_panel_mode()
    # Choose the appropriate form based on panel type
    form = SpecialButtonFormDSC() if panel_mode == DSC else SpecialButtonFormAdemco()
    if request.method == 'GET':
        # Pre-fill the form with current settings on initial load
        keypad_service.fill_special_form(form, panel_mode)
    if form.validate_on_submit():
        # Save the special button configuration and return to the button list
        keypad_service.update_special_settings(form, panel_mode)
        flash('Special button settings updated', 'success')
        return redirect(url_for('keypad.list_buttons'))
    return render_template('keypad/special_buttons.html', form=form)
