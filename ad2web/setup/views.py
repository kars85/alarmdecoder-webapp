import os
import glob
import platform

from flask import Blueprint, render_template, flash, redirect, url_for
from flask import current_app

from ..extensions import db
from ..decorators import admin_or_first_run_required
from ..settings.models import Setting
from ..certificate.models import Certificate
from ..certificate.constants import CA, SERVER, INTERNAL, ACTIVE as CERT_ACTIVE
from .forms import (DeviceTypeForm, NetworkDeviceForm, LocalDeviceForm,
                   SSLForm, SSLHostForm, DeviceForm, TestDeviceForm, CreateAccountForm, LocalDeviceFormUSB)
from .constants import (SETUP_TEST, DEFAULT_BAUDRATES, DEFAULT_PATHS, SETUP_ENDPOINT_STAGE)
from ..ser2sock import ser2sock
from ..user.models import User
from ..user.constants import ADMIN as USER_ADMIN, ACTIVE as USER_ACTIVE
from alarmdecoder.panels import ADEMCO

setup = Blueprint('setup', __name__, url_prefix='/setup')

def set_stage(stage):
    """Updates the 'setup_stage' setting in the database."""
    setup_stage = Setting.get_by_name('setup_stage')
    setup_stage.value = stage
    db.session.add(setup_stage)

@setup.context_processor
def setup_context_processor():
    """Injects variables into the template context for the setup blueprint."""
    return {}

@setup.route('/')
@admin_or_first_run_required
def index():
    """Displays the initial setup page."""
    return render_template('setup/index.html')

@setup.route('/type', methods=['GET', 'POST'])
def type():
    """Handles the selection of the AlarmDecoder device type and location."""
    form = DeviceTypeForm()
    if not form.is_submitted():
        # Pre-populate form with existing settings if available
        device_type = Setting.get_by_name('device_type').value
        if device_type:
            form.device_type.data = device_type

        device_location = Setting.get_by_name('device_location').value
        if device_location:
            managed_ser2sock = Setting.get_by_name('managed_ser2sock', default=False).value
            if managed_ser2sock:
                form.device_location.data = 'local'  # Treat managed ser2sock as local for this form
            else:
                form.device_location.data = device_location

    if form.validate_on_submit():
        # Save submitted device type and location settings
        device_type = Setting.get_by_name('device_type')
        device_type.value = form.device_type.data
        db.session.add(device_type)

        device_location = Setting.get_by_name('device_location')
        device_location.value = form.device_location.data
        db.session.add(device_location)
        # Determine the next setup stage based on location
        next_stage = 'setup.{}'.format(device_location.value)
        set_stage(SETUP_ENDPOINT_STAGE[next_stage])

        db.session.commit()

        return redirect(url_for(next_stage))

    return render_template('setup/type.html', form=form)

@setup.route('/local', methods=['GET', 'POST'])
@admin_or_first_run_required
def local():
    """Handles the configuration of a locally connected AlarmDecoder device."""
    operating_system = platform.system()
    device_search_path = None
    # Determine the default search path based on OS
    if operating_system != 'Darwin' and operating_system != 'Windows':
        device_search_path = '/dev/ttyUSB*'
    else:
        device_search_path = '/dev/tty.usb*'

    device_type = Setting.get_by_name('device_type').value

    form = None
    # Choose the appropriate form based on device type (AD2USB needs USB port selection)
    if device_type != 'AD2USB':
        form = LocalDeviceForm()
    else:
        form = LocalDeviceFormUSB()
        usb_devices = _iterate_usb(device_search_path)
        if not usb_devices:
            flash('No devices found - please make sure your AD2USB is plugged into a USB Port and refresh the page.', 'error')
        # Populate choices for USB devices
        form.device_path.choices = [(usb_devices[i], usb_devices[i]) for i in usb_devices]

    if not form.is_submitted():
        # Pre-populate form with existing settings
        if device_type != 'AD2USB':
            device_path = Setting.get_by_name('device_path').value
            if device_path:
                form.device_path.data = device_path
            else:
                form.device_path.data = DEFAULT_PATHS[device_type]
        # Ensure choices are up-to-date for AD2USB
        else:
            usb_devices = _iterate_usb(device_search_path)
            device_path = Setting.get_by_name('device_path').value
            form.device_path.choices = [(usb_devices[i], usb_devices[i]) for i in usb_devices]
            if device_path:
                form.device_path.default = device_path
            else:
                form.device_path.default = DEFAULT_PATHS[device_type]

        baudrate = Setting.get_by_name('device_baudrate').value
        if baudrate:
            form.baudrate.data = baudrate
        else:
            form.baudrate.data = DEFAULT_BAUDRATES[device_type]
        # Check if ser2sock management is possible and pre-populate
        if ser2sock.exists():
            managed = Setting.get_by_name('managed_ser2sock').value
            if managed:
                form.confirm_management.data = managed
        else:
            # Remove management option if ser2sock isn't found
            del form.confirm_management

    if form.validate_on_submit():
        device_path = Setting.get_by_name('device_path')
        # Ensure choices are up-to-date before validation/saving for AD2USB
        if device_type == 'AD2USB':
            usb_devices = _iterate_usb(device_search_path)
            form.device_path.choices = [(usb_devices[i], usb_devices[i]) for i in usb_devices]

        baudrate = Setting.get_by_name('device_baudrate')
        managed = Setting.get_by_name('managed_ser2sock')
        device_path.value = form.device_path.data
        baudrate.value = form.baudrate.data
        managed.value = form.confirm_management.data

        db.session.add(device_path)
        db.session.add(baudrate)
        db.session.add(managed)
        # Determine next stage based on ser2sock management choice
        next_stage = 'setup.device'
        if form.confirm_management.data == True:
            next_stage = 'setup.sslserver'  # Go to SSL setup if managing ser2sock
        else:
            # If not managing ser2sock, try to stop it and clear its device path config
            if ser2sock.exists():
                try:
                    ser2sock.stop()
                    config_path = Setting.get_by_name('ser2sock_config_path',default='/etc/ser2sock').value

                    config_settings = {
                        'device_path': '',
                    }

                    ser2sock.update_config(config_path, **config_settings)

                except OSError:
                    flash("We've detected that ser2sock is running and failed to stop it.  There may be communication issues unless it is killed manually.", 'warning')

        set_stage(SETUP_ENDPOINT_STAGE[next_stage])
        db.session.commit()

        return redirect(url_for(next_stage))

    return render_template('setup/local.html', form=form)

def _iterate_usb(device_path):
    """
    Finds potential USB serial devices based on a glob pattern.

    Args:
        device_path (str): The glob pattern to search for device files (e.g., '/dev/ttyUSB*').

    Returns:
        dict: A dictionary where keys and values are the full paths to the found devices.
    """
    ports = glob.glob(device_path)
    ports.sort()
    devices = {}

    for port in ports:
        port_path = os.path.join('/dev', port.split('/')[-1])
        devices[port_path] = port_path

    return devices

@setup.route('/network', methods=['GET', 'POST'])
@admin_or_first_run_required
def network():
    """Handles the configuration of a network-connected AlarmDecoder device."""
    form = NetworkDeviceForm()
    if not form.is_submitted():
        # Pre-populate form with existing settings
        device_address = Setting.get_by_name('device_address').value
        if device_address:
            form.device_address.data = device_address

        device_port = Setting.get_by_name('device_port').value
        if device_port:
            form.device_port.data = device_port

        use_ssl = Setting.get_by_name('use_ssl').value
        if use_ssl is not None:
            form.ssl.data = use_ssl

    if form.validate_on_submit():
        # Save submitted network settings
        device_address = Setting.get_by_name('device_address')
        device_port = Setting.get_by_name('device_port')
        ssl = Setting.get_by_name('use_ssl')

        device_address.value = form.device_address.data
        device_port.value = form.device_port.data
        ssl.value = form.ssl.data

        db.session.add(device_address)
        db.session.add(device_port)
        db.session.add(ssl)
        # Determine next stage based on SSL selection
        next_stage = 'setup.device'
        if form.ssl.data == True:
            next_stage = 'setup.sslclient'  # Go to SSL client cert upload if SSL is enabled

        set_stage(SETUP_ENDPOINT_STAGE[next_stage])
        db.session.commit()

        return redirect(url_for(next_stage))

    return render_template('setup/network.html', form=form)

import logging # Added for potential error logging
from sqlalchemy.orm.exc import MultipleResultsFound # To handle unexpected duplicates

# Assuming you have db, Certificate, Setting, admin_or_first_run_required,
# SSLForm, render_template, redirect, url_for, set_stage, SETUP_ENDPOINT_STAGE defined elsewhere

log = logging.getLogger(__name__) # Optional: for logging errors

@setup.route('/sslclient', methods=['GET', 'POST'])
@admin_or_first_run_required
def sslclient():
    """Handles the uploading of SSL certificates for connecting to a remote ser2sock."""
    form = SSLForm()
    # form.multipart = True # This is often handled automatically by Flask-WTF/WTForms with FileFields, may not be needed. Check your SSLForm definition.
    if form.validate_on_submit():
        try:
            # Read uploaded certificate data
            # Ensure data exists before trying to read stream (WTForms usually handles this via validation)
            ca_cert_data = form.ca_cert.data.stream.read() if form.ca_cert.data else b''
            cert_data = form.cert.data.stream.read() if form.cert.data else b''
            key_data = form.key.data.stream.read() if form.key.data else b''

            # --- FIXED SECTION 1: CA Certificate ---
            try:
                # Use first() to get the first matching record or None
                ca_cert = Certificate.query.filter_by(name='AlarmDecoder CA').first()

                if ca_cert is None:
                    # If it doesn't exist, create it
                    log.info("Creating new Certificate record for 'AlarmDecoder CA'")
                    ca_cert = Certificate(name='AlarmDecoder CA', certificate=ca_cert_data, key=b'') # Use bytes for key
                    db.session.add(ca_cert)
                else:
                    # If it exists, update it
                    log.info("Updating existing Certificate record for 'AlarmDecoder CA'")
                    ca_cert.certificate = ca_cert_data
                    ca_cert.key = b'' # Ensure key is explicitly empty (or bytes)
                    # No need to db.session.add(ca_cert) again if updating an existing managed object

            except MultipleResultsFound:
                log.error("Database Error: Multiple 'AlarmDecoder CA' certificates found. Check data integrity.")
                # Handle error appropriately - maybe flash a message and return to form
                flash('Configuration error: Duplicate CA certificate records found. Please contact support.', 'danger')
                return render_template('setup/sslclient.html', form=form)


            # --- FIXED SECTION 2: Internal Certificate ---
            try:
                # Use first() to get the first matching record or None
                internal_cert = Certificate.query.filter_by(name='AlarmDecoder Internal').first()

                if internal_cert is None:
                     # If it doesn't exist, create it
                    log.info("Creating new Certificate record for 'AlarmDecoder Internal'")
                    internal_cert = Certificate(name='AlarmDecoder Internal', certificate=cert_data, key=key_data)
                    db.session.add(internal_cert)
                else:
                    # If it exists, update it
                    log.info("Updating existing Certificate record for 'AlarmDecoder Internal'")
                    internal_cert.certificate = cert_data
                    internal_cert.key = key_data
                    # No need to db.session.add(internal_cert) again if updating an existing managed object

            except MultipleResultsFound:
                log.error("Database Error: Multiple 'AlarmDecoder Internal' certificates found. Check data integrity.")
                 # Handle error appropriately - maybe flash a message and return to form
                flash('Configuration error: Duplicate Internal certificate records found. Please contact support.', 'danger')
                return render_template('setup/sslclient.html', form=form)


            # --- SSL Setting Section (Kept original logic assuming Setting.get_by_name is robust) ---
            # Ensure SSL setting is enabled
            # Assuming Setting.get_by_name handles not found cases appropriately (e.g., creates or raises specific error)
            use_ssl = Setting.get_by_name('use_ssl')
            if use_ssl:
                use_ssl.value = True # Assuming value can be set to boolean True
                db.session.add(use_ssl) # Add might be needed depending on get_by_name implementation
            else:
                # Handle case where setting doesn't exist if get_by_name returns None
                log.warning("Setting 'use_ssl' not found. Creating it.")
                use_ssl = Setting(name='use_ssl', value=True) # Adjust type if value needs to be string 'true' etc.
                db.session.add(use_ssl)


            # --- Commit and Redirect ---
            db.session.commit()
            log.info("SSL Client certificates updated and use_ssl enabled.")
            flash('SSL Client certificates updated successfully.', 'success') # Optional: provide user feedback

            next_stage = 'setup.device'
            set_stage(SETUP_ENDPOINT_STAGE[next_stage])

            return redirect(url_for(next_stage))

        except Exception as e:
            db.session.rollback() # Rollback on any unexpected error during DB operations
            log.exception("Error processing SSL client certificate upload.") # Log the full exception
            flash(f'An unexpected error occurred: {e}', 'danger')
            # Fall through to render the template again

    # Render form on GET request or if validation fails
    return render_template('setup/sslclient.html', form=form)

@setup.route('/sslserver', methods=['GET', 'POST'])
@admin_or_first_run_required
def sslserver():
    """Handles the configuration of the managed ser2sock instance, including SSL."""
    form = SSLHostForm()
    if not form.is_submitted():
        # Pre-populate form with existing settings
        use_ssl = Setting.get_by_name('use_ssl').value
        if use_ssl is not None:
            form.ssl.data = use_ssl

        config_path = Setting.get_by_name('ser2sock_config_path').value
        if config_path:
            form.config_path.data = config_path

        device_address = Setting.get_by_name('device_address').value
        if device_address:
            form.device_address.data = device_address

        device_port = Setting.get_by_name('device_port').value
        if device_port:
            form.device_port.data = device_port

    if form.validate_on_submit():
        # Save settings related to managed ser2sock
        manage_ser2sock = Setting.get_by_name('manage_ser2sock')
        use_ssl = Setting.get_by_name('use_ssl')
        config_path = Setting.get_by_name('ser2sock_config_path')
        device_address = Setting.get_by_name('device_address')
        device_port = Setting.get_by_name('device_port')
        device_location = Setting.get_by_name('device_location')

        manage_ser2sock.value = True   # Explicitly set managed to true
        use_ssl.value = form.ssl.data
        config_path.value = form.config_path.data
        device_address.value = form.device_address.data  # Address ser2sock listens on
        device_port.value = form.device_port.data
        device_location.value = 'network'  # Managed ser2sock acts like a network device

        db.session.add(manage_ser2sock)
        db.session.add(use_ssl)
        db.session.add(config_path)
        db.session.add(device_address)
        db.session.add(device_port)
        db.session.add(device_location)

        next_stage = 'setup.device'
        set_stage(SETUP_ENDPOINT_STAGE[next_stage])
        db.session.commit()   # Commit settings before attempting ser2sock update

        try:
            # Generate certificates if SSL is enabled and they don't exist
            if form.ssl.data == True:
                _generate_certs()
            # Retrieve necessary certificates and settings for ser2sock config
            ca = Certificate.query.filter_by(type=CA).first()
            server_cert = Certificate.query.filter_by(type=SERVER).first()

            config_settings = {
                'device_path': Setting.get_by_name('device_path').value,  # From local setup step
                'device_port': device_port.value,  # Port ser2sock listens on
                'device_baudrate': Setting.get_by_name('device_baudrate').value,  # From local setup step
                # 'device_port': device_port.value, # Duplicate key, likely intended for listen port vs device port? Check ser2sock config needs.
                'raw_device_mode': 1,  # Assuming raw mode is desired
                'use_ssl': use_ssl.value,
                'ca_cert': ca,  # Pass Certificate object, ser2sock module handles path extraction
                'server_cert': server_cert  # Pass Certificate object
            }
            # Update the ser2sock configuration file and restart/HUP the service
            ser2sock.update_config(config_path.value, **config_settings)


        except RuntimeError as err:

            flash("{}".format(err), 'error')  # General errors during cert generation or config update

        except ser2sock.HupFailed as err:

            flash("We had an issue restarting ser2sock: {}".format(err), 'error')  # Specific error for HUP failure

        except ser2sock.NotFound:

            flash("We weren't able to find ser2sock on your system.", 'error')  # ser2sock executable not found

        except Exception as err:

            flash("Unexpected Error: {}".format(err), 'error')  # Catch-all for other exceptions

        else:

            # Only redirect if ser2sock update was successful

            return redirect(url_for(next_stage))

    return render_template('setup/ssl.html', form=form)  # Re-render form on error or initial GET

def _generate_certs():
    """Generates the necessary CA, Server, and Internal certificates if they don't exist."""
    # Check if CA certificate already exists
    if Certificate.query.filter_by(type=CA).first() is None:
        # Create and save CA certificate
        ca_cert = Certificate(
            name="AlarmDecoder CA",
            description='CA certificate used for authenticating others.',
            status=CERT_ACTIVE,
            type=CA)
        ca_cert.generate(common_name='AlarmDecoder CA')
        db.session.add(ca_cert)
        db.session.commit()  # Commit CA cert so its ID is available

        # Create and save Server certificate signed by the CA
        server_cert = Certificate(
            name="AlarmDecoder Server",
            description='Server certificate used by ser2sock.',
            status=CERT_ACTIVE,
            type=SERVER,
            ca_id=ca_cert.id)
        server_cert.generate(common_name='AlarmDecoder Server', parent=ca_cert)
        db.session.add(server_cert)

        # Create and save Internal client certificate signed by the CA
        internal_cert = Certificate(
            name="AlarmDecoder Internal",
            description='Internal certificate used to communicate with ser2sock.',
            status=CERT_ACTIVE,
            type=INTERNAL,
            ca_id=ca_cert.id)
        internal_cert.generate(common_name='AlarmDecoder Internal', parent=ca_cert)
        db.session.add(internal_cert)
        db.session.commit()  # Commit server and internal certs

@setup.route('/test', methods=['GET', 'POST'])
@admin_or_first_run_required
def test():
    """Displays a page to test the connection to the AlarmDecoder device."""
    form = TestDeviceForm()

    if not form.is_submitted():
        # Set the stage to indicate testing is in progress
        set_stage(SETUP_TEST)
        db.session.commit()
    else:
        # User clicked the 'Continue' button after testing
        setup_complete = Setting.get_by_name('setup_complete', default=False)

        # Determine next stage: account creation or main app if already set up
        next_stage = 'setup.account'
        if setup_complete.value:
            next_stage = 'frontend.index'
            flash('Setup complete!', 'success')  # Or maybe 'Configuration updated!'

        set_stage(SETUP_ENDPOINT_STAGE[next_stage])
        db.session.commit()

        return redirect(url_for(next_stage))

    # Renders the testing page, which likely uses JavaScript to interact with the backend API
    return render_template('setup/test.html', form=form)

@setup.route('/account', methods=['GET', 'POST'])
@admin_or_first_run_required
def account():
    """Handles the creation of the initial administrator account."""
    form = CreateAccountForm()

    if form.validate_on_submit():
        # Create the new admin user
        user = User(role_code=USER_ADMIN, status_code=USER_ACTIVE)
        form.populate_obj(user)  # Populates user attributes from form data
        db.session.add(user)

        # Mark setup as complete
        setup_complete = Setting.get_by_name('setup_complete', default=False)
        setup_complete.value = True
        db.session.add(setup_complete)

        next_stage = 'frontend.index'  # Final stage is the main application
        set_stage(SETUP_ENDPOINT_STAGE[next_stage])
        db.session.commit()

        flash('Setup complete!', 'success')

        return redirect(url_for(next_stage))

    return render_template('setup/account.html', form=form)

@setup.route('/device', methods=['GET', 'POST'])
@admin_or_first_run_required
def device():
    """Handles the configuration of AlarmDecoder device-specific settings."""
    panel_mode = Setting.get_by_name('panel_mode', default=ADEMCO).value  # Get default panel mode
    form = DeviceForm()
    if not form.is_submitted():
        # Try to pre-populate form from the currently running decoder instance
        if current_app.decoder.device is not None:
            form.panel_mode.data = current_app.decoder.device.mode
            form.keypad_address.data = current_app.decoder.device.address
            form.address_mask.data = '{:0>8x}'.format(current_app.decoder.device.address_mask)
            form.internal_address_mask.data = '{:0>8x}'.format(current_app.decoder.internal_address_mask)
            form.lrr_enabled.data = current_app.decoder.device.emulate_lrr
            form.deduplicate.data = current_app.decoder.device.deduplicate
            # Convert boolean lists to string lists for MultiCheckboxField
            form.zone_expanders.data = [str(idx + 1) for idx, value in
                                        enumerate(current_app.decoder.device.emulate_zone) if value]
            form.relay_expanders.data = [str(idx + 1) for idx, value in
                                         enumerate(current_app.decoder.device.emulate_relay) if value]
        else:
            # If decoder isn't running, try to open it briefly to allow 'Get Panel Info' button to work
            try:
                current_app.decoder.close()
                current_app.decoder.open()
            except Exception:  # Broad except as many things could go wrong here
                pass  # Ignore errors, just try to pre-populate from settings

            # Pre-populate form from saved settings if decoder instance isn't available
            panel_mode_setting = Setting.get_by_name('panel_mode').value
            if panel_mode_setting is not None:
                form.panel_mode.data = panel_mode_setting

            keypad_address = Setting.get_by_name('keypad_address').value
            if keypad_address:
                form.keypad_address.data = keypad_address

            address_mask = Setting.get_by_name('address_mask').value
            if address_mask is not None:
                form.address_mask.data = address_mask

            internal_address_mask = Setting.get_by_name('internal_address_mask').value
            if internal_address_mask is not None:
                form.internal_address_mask.data = internal_address_mask

            lrr_enabled = Setting.get_by_name('lrr_enabled').value
            if lrr_enabled is not None:
                form.lrr_enabled.data = lrr_enabled

            # Convert saved comma-separated string back to list for MultiCheckboxField
            zone_expanders_str = Setting.get_by_name('emulate_zone_expanders').value
            if zone_expanders_str is not None:
                form.zone_expanders.data = [str(idx + 1) for idx, value in
                                            enumerate(v == 'True' for v in zone_expanders_str.split(',')) if value]

            relay_expanders_str = Setting.get_by_name('emulate_relay_expanders').value
            if relay_expanders_str is not None:
                form.relay_expanders.data = [str(idx + 1) for idx, value in
                                             enumerate(v == 'True' for v in relay_expanders_str.split(',')) if value]

            deduplicate = Setting.get_by_name('deduplicate').value
            if deduplicate is not None:
                form.deduplicate.data = deduplicate


    # Handle form submission

    else:

        if form.validate_on_submit():
            # Get setting objects

            panel_mode_setting = Setting.get_by_name('panel_mode')

            keypad_address = Setting.get_by_name('keypad_address')

            address_mask = Setting.get_by_name('address_mask')

            internal_address_mask = Setting.get_by_name('internal_address_mask')

            lrr_enabled = Setting.get_by_name('lrr_enabled')

            zone_expanders = Setting.get_by_name('emulate_zone_expanders')

            relay_expanders = Setting.get_by_name('emulate_relay_expanders')

            deduplicate = Setting.get_by_name('deduplicate')

            # Convert checkbox data to boolean lists then to comma-separated strings for storage

            zx = [str(x) in form.zone_expanders.data for x in range(1, 6)]  # Check presence for zones 1-5

            rx = [str(x) in form.relay_expanders.data for x in range(1, 5)]  # Check presence for relays 1-4

            # Update setting values from form data

            panel_mode_setting.value = form.panel_mode.data

            keypad_address.value = form.keypad_address.data

            address_mask.value = form.address_mask.data

            internal_address_mask.value = form.internal_address_mask.data

            lrr_enabled.value = form.lrr_enabled.data

            zone_expanders.value = ','.join(map(str, zx))  # Store as 'True,False,True...'

            relay_expanders.value = ','.join(map(str, rx))  # Store as 'True,False,True...'

            deduplicate.value = form.deduplicate.data

            set_stage(SETUP_TEST)  # Next stage is testing the connection

            # Add all updated settings to the session

            db.session.add(panel_mode_setting)

            db.session.add(keypad_address)

            db.session.add(address_mask)

            db.session.add(internal_address_mask)

            db.session.add(lrr_enabled)

            db.session.add(zone_expanders)

            db.session.add(relay_expanders)

            db.session.add(deduplicate)

            db.session.commit()  # Save changes

            # Redirect to the test page

            return redirect(url_for('setup.test'))

        # Pass current panel mode to template for conditional display logic (e.g., DSC options)
        return render_template('setup/device.html', form=form, panel_mode=panel_mode)

    @setup.route('/complete', methods=['GET'])
    def complete():
        """Displays a simple 'Setup Complete' page (potentially unused if redirecting directly)."""
        # This route might not be strictly necessary if /account redirects directly to frontend.index
        return render_template('setup/complete.html')
