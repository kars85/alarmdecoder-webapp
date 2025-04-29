from sqlalchemy.exc import IntegrityError

from ..extensions import db
from .models import User, FailedLogin
from .constants import ACTIVE, INACTIVE

class DuplicateUserError(Exception):
    """Exception raised when attempting to create/update a user with duplicate data."""
    pass

class UserService:
    """Service layer for user management logic."""

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
        """Create a new user with the given attributes. Returns the new User."""
        new_user = User(name=name, email=email, password=password,
                        role_code=role_code, status_code=status_code)
        try:
            db.session.add(new_user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # Duplicate username or email (unique constraints) triggered
            raise DuplicateUserError("Username or email already exists.")
        return new_user

    @staticmethod
    def update_user(user_id, name, email, password, role_code, status_code):
        """Update an existing user. Returns the updated User, or None if not found."""
        user = UserService.get_user(user_id)
        if not user:
            return None
        # Update fields (password property setter will hash the new password)
        user.name = name
        user.email = email
        if password is not None and password != "":  # password is required to be provided
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
        """Delete a user by ID. Returns True if deleted, False if not found or not deleted."""
        user = UserService.get_user(user_id)
        if not user:
            return False
        # Prevent deletion of the initial super-admin user (id=1) for safety
        if user.id == 1:
            return False
        db.session.delete(user)
        db.session.commit()
        return True

    @staticmethod
    def list_failed_logins():
        """Fetch all failed login attempts."""
        return FailedLogin.query.all()

    @staticmethod
    def toggle_user_status(user_id):
        """
        Toggle the active status of a user. If the user is active, deactivate them;
        if inactive or new, activate them. Returns the updated User or None if not found.
        """
        user = UserService.get_user(user_id)
        if not user:
            return None
        if user.status_code == ACTIVE:
            # Deactivate user
            user.status_code = INACTIVE
        else:
            # Activate user (also treat 'new' as activating)
            user.status_code = ACTIVE
        db.session.commit()
        return user
