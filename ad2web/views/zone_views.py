from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import login_required

from ad2web.forms.zones_form import ZoneForm
from ad2web.services.zone_service import ZoneService
from ..decorators import admin_required

zones_bp = Blueprint('zones', __name__, url_prefix='/settings/zones')

@zones_bp.route('/')
@login_required
@admin_required
def index():
    """List all zones."""
    zones = ZoneService.list_zones()
    return render_template('zones/index.html', zones=zones, active="zones")

@zones_bp.route('/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create():
    """Create a new zone."""
    form = ZoneForm()
    if form.validate_on_submit():
        try:
            ZoneService.create_zone(zone_id=form.zone_id.data, name=form.name.data, description=form.description.data)
            flash('Zone created.', 'success')
            return redirect(url_for('zones.index'))
        except Exception:
            flash('Failed to create zone. Ensure the Zone ID is unique.', 'error')
            return redirect(url_for('zones.index'))
    return render_template('zones/create.html', form=form, active="zones")

@zones_bp.route('/edit/<int:zone_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit(zone_id):
    """Edit an existing zone (identified by zone_id)."""
    zone = ZoneService.get_zone(zone_id)
    if not zone:
        abort(404)
    form = ZoneForm(obj=zone)
    if form.validate_on_submit():
        try:
            ZoneService.update_zone(zone_id=zone_id, name=form.name.data, description=form.description.data)
            flash('Zone updated.', 'success')
            return redirect(url_for('zones.index'))
        except Exception:
            flash('Failed to update zone. Ensure the Zone ID is unique.', 'error')
            return redirect(url_for('zones.index'))
    return render_template('zones/edit.html', form=form, zone_id=zone_id, active="zones")

@zones_bp.route('/delete/<int:zone_id>', methods=['POST'])
@login_required
@admin_required
def delete(zone_id):
    """Delete a zone (AJAX endpoint)."""
    if not ZoneService.delete_zone(zone_id):
        return jsonify(success=False), 404
    flash('Zone deleted.', 'success')
    return jsonify(success=True)

@zones_bp.route('/import', methods=['POST'])
@login_required
@admin_required
def import_zone():
    """Import zones from JSON data (AJAX endpoint triggered by "Scan Panel")."""
    data = request.get_json(force=True, silent=True)
    result = ZoneService.import_zones(data or [])
    if isinstance(result, str):
        # result is an error message
        return jsonify(success=result)
    elif result == 0:
        # No zones imported
        return jsonify(success=0)
    else:
        # Successfully imported zones, return the new zones data
        return jsonify(success=result)
