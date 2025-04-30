from sqlalchemy.exc import IntegrityError

from ad2web.extensions import db
from ad2web.user.models import User, FailedLogin
from ad2web.user.constants import ACTIVE, INACTIVE


class DuplicateUserError(Exception):
    """Exception raised for duplicate username or email when creating/updating a user."""
    pass


class AdminService:
    """Service layer for administrative user management tasks."""

    @staticmethod
    def list_users():
        """Fetch all users."""
        return User.query.all()

    @staticmethod
    def get_user(user_id):
        """Fetch a single user by ID, or return None if not found."""
        return User.query.filter_by(id=user_id).first()

    @staticmethod
    def create_user(name, email, password, role_code, status_code):
        """Create a new user. Returns the new User, or raises DuplicateUserError on duplicates."""
        new_user = User(name=name, email=email, password=password,
                        role_code=role_code, status_code=status_code)
        try:
            db.session.add(new_user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # Unique constraint (username or email) violated
            raise DuplicateUserError("Username or email already exists.")
        return new_user

    @staticmethod
    def update_user(user_id, name, email, password, role_code, status_code):
        """Update an existing user by ID. Returns the updated User or None if not found.
        If a non-empty password is provided, the user's password is updated; otherwise it remains unchanged.
        Raises DuplicateUserError if username or email uniqueness is violated."""
        user = AdminService.get_user(user_id)
        if not user:
            return None
        # Update basic fields
        user.name = name
        user.email = email
        if password is not None and password != "":
            # Update password if a new password was provided (model will hash it)
            user.password = password
        user.role_code = role_code
        user.status_code = status_code
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise DuplicateUserError("Username or email already exists.")
        return user

    @staticmethod
    def delete_user(user_id):
        """Delete a user by ID. Returns True if deleted, False if not found or not allowed.
        Prevents deletion of the primary admin (user_id == 1)."""
        user = AdminService.get_user(user_id)
        if not user:
            return False
        if user.id == 1:
            # Do not allow deleting the primary admin account
            return False
        db.session.delete(user)
        db.session.commit()
        return True

    @staticmethod
    def list_failed_logins():
        """Fetch all failed login attempts (most recent first)."""
        return FailedLogin.query.order_by(FailedLogin.login_time.desc()).all()

    @staticmethod
    def toggle_user_status(user_id):
        """Toggle a user's status between active and inactive (treating 'new' as inactive).
        Returns the updated User, or None if not found."""
        user = AdminService.get_user(user_id)
        if not user:
            return None
        if user.status_code == ACTIVE:
            # Currently active -> deactivate
            user.status_code = INACTIVE
        else:
            # Currently inactive or new -> activate
            user.status_code = ACTIVE
        db.session.commit()
        return user
