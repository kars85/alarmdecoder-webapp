from ..extensions import db
from ..user import User  # User model
from ..api.models import APIKey  # APIKey model (one-to-one with User)
from ..api.utils import generate_api_key  # utility to generate random API key

def create_or_update_api_key(user_id):
    """
    Create a new API key for the given user, or update the existing one.
    Returns the APIKey object with the new key.
    """
    user = User.query.get(user_id)
    if not user:
        raise ValueError(f"User ID {user_id} does not exist")
    # Find existing APIKey record or create a new one
    apikey = APIKey.query.filter_by(user_id=user_id).first()
    if not apikey:
        apikey = APIKey(user_id=user_id)
    # Generate a new key and save
    apikey.key = generate_api_key()
    db.session.add(apikey)
    db.session.commit()
    return apikey

def disable_api_key(user_id):
    """
    Disable (revoke) the API key for the given user.
    Sets the key to None (removes access) and returns the APIKey object.
    """
    user = User.query.get(user_id)
    if not user:
        raise ValueError(f"User ID {user_id} does not exist")
    apikey = APIKey.query.filter_by(user_id=user_id).first()
    if not apikey:
        # If no APIKey record exists for the user, create one (to maintain one record per user)
        apikey = APIKey(user_id=user_id)
    apikey.key = None  # Remove the key
    db.session.add(apikey)
    db.session.commit()
    return apikey

def get_api_key_by_user(user_id):
    """
    Retrieve the APIKey object for a given user (if any).
    """
    return APIKey.query.filter_by(user_id=user_id).first()

def get_api_key_by_value(key_value):
    """
    Retrieve an APIKey object by its key string.
    """
    return APIKey.query.filter_by(key=key_value).first()

def authenticate_api_key(key_value):
    """
    Validate an API key string and return the associated User if valid, otherwise None.
    """
    if not key_value:
        return None
    apikey = get_api_key_by_value(key_value)
    if not apikey or apikey.key is None:
        # Key not found or explicitly disabled
        return None
    return apikey.user  # Return the linked User object (via relationship)
