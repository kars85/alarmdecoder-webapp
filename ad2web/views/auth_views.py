# ad2web/views/auth_views.py
from flask import Blueprint, render_template, request, flash, url_for, redirect, jsonify
from flask_login import login_user, logout_user, login_required, current_user, confirm_login
from flask_babel import gettext as _

from ..services.auth_service import AuthService
from ..extensions import db
from ..forms.auth_form import LoginForm, SignupForm, ForgotPasswordForm, ResetPasswordForm, ProfileEditForm, ChangePasswordForm, ReauthForm
from ..settings.models import Setting  # for license agreement check

auth = Blueprint('auth', __name__, url_prefix='/auth')

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Already logged in; redirect to main application
        return redirect(url_for('keypad.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = AuthService.authenticate(form.login.data, form.password.data)
        if user:
            login_user(user, remember=form.remember.data)
            # Record successful login attempt
            AuthService.record_login_history(user, ip_address=request.remote_addr, user_agent=request.headers.get('User-Agent'))
            # Check if license agreement needs to be accepted
            license_accepted = Setting.get_by_name('license_agreement', default=False).value
            if not license_accepted:
                return redirect(url_for('frontend.license'))
            # Redirect to next page or dashboard
            next_page = form.next.data or url_for('keypad.index')
            if request.is_xhr:  # AJAX login can get JSON instead of redirect
                return jsonify({'success': True, 'redirect': next_page})
            return redirect(next_page)
        else:
            # Invalid credentials
            AuthService.record_failed_login(form.login.data, ip_address=request.remote_addr, user_agent=request.headers.get('User-Agent'))
            error_msg = _("Sorry, invalid login")
            if request.is_xhr:
                return jsonify({'success': False, 'error': str(error_msg)}), 400
            flash(error_msg, 'error')
    else:
        # If form validation failed (e.g., missing fields)
        if request.method == 'POST' and request.is_xhr:
            return jsonify({'success': False, 'errors': form.errors}), 400
    return render_template('frontend/login.html', form=form)

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash(_("Logged out"), 'success')
    return redirect(url_for('frontend.index'))

@auth.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('user.index'))
    form = SignupForm()
    if form.validate_on_submit():
        new_user = AuthService.register_user(form.name.data, form.email.data, form.password.data)
        if new_user:
            login_user(new_user)
            # Redirect to next or user dashboard
            return redirect(form.next.data or url_for('user.index'))
        else:
            flash(_("Unable to create account. Please try again."), 'error')
    return render_template('frontend/signup.html', form=form)

@auth.route('/reauth', methods=['GET', 'POST'])
@login_required
def reauth():
    form = ReauthForm()
    if form.validate_on_submit():
        # Re-verify the current logged-in user's password
        user = AuthService.authenticate(current_user.name, form.password.data)
        if user:
            confirm_login()  # mark session as fresh
            flash(_("Reauthenticated."), 'success')
            return redirect(form.next.data or url_for('frontend.index'))
        else:
            flash(_("Password is wrong."), 'error')
    return render_template('frontend/reauth.html', form=form)

@auth.route('/reset_password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        # Logged-in users don't need password reset – redirect to profile page
        return redirect(url_for('auth.profile'))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        from ..user.models import User  # import here to avoid circular import at top
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            token = AuthService.generate_reset_token(user)
            reset_url = url_for('auth.reset_password_token', token=token, _external=True)
            # Send reset email to user (omitted actual email sending for brevity)
            # e.g., use flask_mail to send an email with reset_url
            flash(_("Please check your email for password reset instructions."), 'success')
        else:
            flash(_("Sorry, no user found for that email address"), 'error')
        # Render the same form page with a flash message
        return render_template('frontend/reset_password.html', form=form)
    return render_template('frontend/reset_password.html', form=form)

@auth.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password_token(token):
    if current_user.is_authenticated:
        # If a logged-in user hits a reset link, redirect to profile or login
        return redirect(url_for('auth.profile'))
    user = AuthService.verify_reset_token(token)
    if not user:
        flash(_("The password reset link is invalid or has expired."), 'error')
        return redirect(url_for('auth.forgot_password'))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        AuthService.reset_password(token, form.password.data)
        flash(_("Your password has been changed. Please log in with your new password."), 'success')
        return redirect(url_for('auth.login'))
    return render_template('frontend/change_password.html', form=form, token=token)

@auth.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = current_user
    form = ProfileEditForm(obj=user)
    if form.validate_on_submit():
        AuthService.update_profile(user, name=form.name.data, email=form.email.data)
        flash(_("Profile updated successfully."), 'success')
        return redirect(url_for('auth.profile'))
    return render_template('frontend/profile_edit.html', form=form)

@auth.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    user = current_user
    form = ChangePasswordForm()
    if form.validate_on_submit():
        # Current password correctness is checked by form validation
        user.password = form.new_password.data
        db.session.add(user)
        db.session.commit()
        flash(_("Password updated successfully."), 'success')
        return redirect(url_for('auth.profile'))
    return render_template('frontend/change_password.html', form=form)
