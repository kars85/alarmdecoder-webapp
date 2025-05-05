from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify
from flask_login import login_required

from ..extensions import db
# Removed: from ..decorators import admin_required # Removed top-level import
from ..settings import Setting
from .forms import ZoneForm
from .models import Zone

zones = Blueprint('zones', __name__, url_prefix='/settings/zones')

@zones.route('/')
@login_required
# @admin_required # Decorator moved inside
def index():
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # Original function code indented here
        zones_data = Zone.query.order_by(Zone.zone_id).all() # Query inside, order for consistency
        panel_mode = Setting.get_by_name('panel_mode').value

        use_ssl = Setting.get_by_name('use_ssl', default=False).value

        return render_template('zones/index.html', zones=zones_data, active="zones", ssl=use_ssl, panel_mode=panel_mode)
    return inner() # Execute the inner, decorated function


@zones.route('/create', methods=['GET', 'POST'])
@login_required
# @admin_required # Decorator moved inside
def create():
    from ..decorators import admin_required    # Import inside the function
    from flask import current_app  # Import current_app if needed for logging inside inner
    @admin_required
    def inner():
        # Original function code indented here
        form = ZoneForm()

        if form.validate_on_submit():
            try:
                zone = Zone()
                form.populate_obj(zone)

                db.session.add(zone)
                db.session.commit()

                flash('Zone created.', 'success')

                return redirect(url_for('zones.index'))
            except Exception as e:
                db.session.rollback()
                flash(f'Error creating zone: {e}', 'error')
                # Log the error
                current_app.logger.error(f"Error creating zone: {e}", exc_info=True)


        use_ssl = Setting.get_by_name('use_ssl', default=False).value
        # Render create page on GET or if validation/commit fails
        return render_template('zones/create.html', form=form, active="zones", ssl=use_ssl)
    return inner() # Execute the inner, decorated function

@zones.route('/edit/<int:id>', methods=['GET', 'POST'])
@login_required
# @admin_required # Decorator moved inside
def edit(id):
    from ..decorators import admin_required    # Import inside the function
    from flask import current_app  # Import current_app if needed for logging inside inner
    @admin_required
    def inner(zone_id): # Pass argument to inner function
        # Original function code indented here
        zone = Zone.query.filter_by(zone_id=zone_id).first_or_404()
        form = ZoneForm(obj=zone)

        if form.validate_on_submit():
            try:
                form.populate_obj(zone)

                db.session.add(zone)
                db.session.commit()

                flash('Zone updated.', 'success')
                # Redirect after successful update to show the updated index
                return redirect(url_for('zones.index'))
            except Exception as e:
                 db.session.rollback()
                 flash(f'Error updating zone: {e}', 'error')
                 # Log the error
                 current_app.logger.error(f"Error updating zone {zone_id}: {e}", exc_info=True)

        # Render edit page on GET or if validation/commit fails
        use_ssl = Setting.get_by_name('use_ssl', default=False).value
        return render_template('zones/edit.html', form=form, id=zone_id, active="zones", ssl=use_ssl)
    # Pass the original argument to the inner function call
    return inner(id)


# Changed method to POST for destructive action
@zones.route('/remove/<int:id>', methods=['POST'])
@login_required
# @admin_required # Decorator moved inside
def remove(id):
    from ..decorators import admin_required    # Import inside the function
    from flask import current_app  # Import current_app if needed for logging inside inner
    @admin_required
    def inner(zone_id): # Pass argument to inner function
        # Original function code indented here
        zone = Zone.query.filter_by(zone_id=zone_id).first_or_404()
        try:
            db.session.delete(zone)
            db.session.commit()
            flash('Zone deleted.', 'success')
        except Exception as e:
             db.session.rollback()
             flash(f'Error deleting zone: {e}', 'error')
             # Log the error
             current_app.logger.error(f"Error deleting zone {zone_id}: {e}", exc_info=True)

        return redirect(url_for('zones.index'))
    # Pass the original argument to the inner function call
    return inner(id)


@zones.route('/import', methods=['POST']) # Should be POST as it modifies data
@login_required
# @admin_required # Decorator moved inside
def import_zone():
    from ..decorators import admin_required    # Import inside the function
    from flask import current_app # Import current_app if needed for logging inside inner
    @admin_required
    def inner():
        # Original function code indented here
        zones_created = {}
        num_zones_created = 0
        error_message = None

        try:
            # Check content type? request.is_json ?
            data = request.get_json()
            if not data: # Handles None or empty list/dict
                raise ValueError("No zone data received or invalid format.")

            # Consider making delete optional via a request parameter?
            # For now, keep original behavior: delete all before import.
            delete_all_zones() # Call helper function

            for d in data:
                # Basic validation of incoming data structure
                if not isinstance(d, dict) or 'address' not in d:
                    current_app.logger.warning(f"Skipping invalid zone data item: {d}")
                    continue

                address = d.get('address')
                # Ensure address is usable (e.g., convert to int if needed, depending on model)
                try:
                    # Assuming Zone.zone_id is an Integer
                    zone_id_int = int(address)
                except (ValueError, TypeError):
                     current_app.logger.warning(f"Skipping zone with invalid address: {address}")
                     continue

                # Use .get with defaults for safer access
                name = d.get('zone_name', '') # Default to empty string if missing
                description = name if name else 'Generated - No Alpha Found' # Keep original logic

                # Check existence using validated integer ID
                if not zone_exists_in_db(zone_id_int):
                    zone = Zone()
                    zone.zone_id = zone_id_int
                    zone.name = name
                    zone.description = description

                    db.session.add(zone)
                    # Store created zone info using the validated ID
                    zones_created[zone_id_int] = { 'zone_id': zone_id_int, 'name': name, 'description': description }
                    num_zones_created += 1

            if num_zones_created > 0:
                db.session.commit() # Commit all added zones at once
                current_app.logger.info(f"Successfully imported {num_zones_created} zones.")
            else:
                 current_app.logger.info("No new zones were imported.")


        except ValueError as ve:
             error_message = str(ve)
             current_app.logger.error(f"Zone import validation error: {ve}", exc_info=True)
             db.session.rollback() # Rollback any partial adds
        except Exception as e:
            error_message = f"An unexpected error occurred during import: {e}"
            current_app.logger.error(f"Zone import failed: {e}", exc_info=True)
            db.session.rollback() # Rollback any partial adds

        # Return JSON response indicating success or failure
        if error_message:
            return jsonify(success=False, message=error_message, zones_imported=zones_created) # Return partial success if needed
        elif num_zones_created == 0 and not error_message:
             # Handle case where input was valid but resulted in 0 imports (e.g., all existed)
             return jsonify(success=True, message="No new zones needed to be imported.", count=0, zones_imported={})
        else:
             return jsonify(success=True, count=num_zones_created, zones_imported=zones_created)

    return inner() # Execute the inner, decorated function


# --- Helper Functions --- (No decorators needed)

def zone_exists_in_db(id):
    """Checks if a zone with the given integer ID exists."""
    # Add type check/conversion if id might not be int
    try:
        zone_id_int = int(id)
        zone = Zone.query.filter_by(zone_id=zone_id_int).first()
        return zone is not None # More explicit boolean return
    except (ValueError, TypeError):
        # Log error if ID is invalid?
        return False


def delete_all_zones():
    """Deletes all zones from the database."""
    from flask import current_app # Import if logging inside helper
    try:
        num_deleted = db.session.query(Zone).delete()
        db.session.commit()
        current_app.logger.info(f"Deleted {num_deleted} existing zones before import.")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting all zones: {e}", exc_info=True)
        # Re-raise or handle as needed - re-raising might be better
        # so the calling function knows deletion failed.
        raise RuntimeError(f"Failed to delete existing zones: {e}") from e