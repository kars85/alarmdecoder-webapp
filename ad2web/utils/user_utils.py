# ad2web/utils/user_utils.py

"""User authentication utility functions for compatibility with Flask-Login."""


def user_is_authenticated(user) -> bool:
    """Determine if a user is authenticated, supporting Flask-Login 0.x and 1.x.

    - Returns False if the user is None.
    - If `user.is_authenticated` is callable (old Flask-Login), call it.
    - Otherwise, use the property (new Flask-Login).
    """
    if user is None:
        return False
    if callable(user.is_authenticated):
        return user.is_authenticated()  # old Flask-Login style (method)
    return bool(user.is_authenticated)  # new Flask-Login style (property)


def user_is_anonymous(user) -> bool:
    """Determine if a user is anonymous (not logged in), supporting Flask-Login versions.

    - Returns False if user is None.
    - If `user.is_anonymous` is callable, call it (old Flask-Login).
    - Otherwise, use the property (new Flask-Login).
    """
    if user is None:
        return False
    if callable(user.is_anonymous):
        return user.is_anonymous()
    return bool(user.is_anonymous)
