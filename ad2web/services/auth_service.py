# ad2web/services/auth_service.py
from uuid import uuid4

from ..extensions import db
from ..user.models import User, UserDetail, UserHistory, FailedLogin

class AuthService:
    @staticmethod
    def authenticate(login_identifier: str, password: str):
        """Validate user credentials. Returns User if correct, else None."""
        # Find user by username or email and verify password
        user = User.query.filter((User.name == login_identifier) | (User.email == login_identifier)).first()
        if user and user.check_password(password):
            return user
        return None

    @staticmethod
    def register_user(username: str, email: str, password: str):
        """Create a new user account with profile details."""
        # Double-check uniqueness (forms should validate this already)
        existing_user = User.query.filter((User.name == username) | (User.email == email)).first()
        if existing_user:
            return None  # Indicate failure due to duplicate
        # Create User and associated empty UserDetail profile
        new_user = User(name=username, email=email, password=password)
        new_user.user_detail = UserDetail()
        db.session.add(new_user)
        db.session.commit()
        return new_user

    @staticmethod
    def generate_reset_token(user: User):
        """Generate and store a password reset token for the user."""
        token = str(uuid4())
        user.activation_key = token
        db.session.add(user)
        db.session.commit()
        return token

    @staticmethod
    def verify_reset_token(token: str):
        """Check the password reset token and return the user if valid."""
        if not token:
            return None
        return User.query.filter_by(activation_key=token).first()

    @staticmethod
    def reset_password(token: str, new_password: str):
        """Reset the user's password using the token. Returns True if successful."""
        user = AuthService.verify_reset_token(token)
        if not user:
            return False
        # Set new password and invalidate the token
        user.password = new_password
        user.activation_key = None
        db.session.add(user)
        db.session.commit()
        return True

    @staticmethod
    def update_profile(user: User, name: str = None, email: str = None):
        """Update basic profile info for the user (name/email)."""
        if name is not None:
            user.name = name
        if email is not None:
            user.email = email
        db.session.add(user)
        db.session.commit()
        return user

    @staticmethod
    def record_login_history(user: User, ip_address: str, user_agent: str):
        """Log a successful login attempt for auditing."""
        entry = UserHistory(user_id=user.id, ip_address=ip_address, user_agent_string=user_agent)
        db.session.add(entry)
        db.session.commit()

    @staticmethod
    def record_failed_login(identifier: str, ip_address: str, user_agent: str):
        """Log a failed login attempt for security monitoring."""
        entry = FailedLogin(name=identifier, ip_address=ip_address, user_agent_string=user_agent)
        db.session.add(entry)
        db.session.commit()
