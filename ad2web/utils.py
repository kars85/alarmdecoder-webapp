"""
    ad2web/utils.py
    ~~~~~~~~~~~~~~~

    Provides miscellaneous utility functions and constants used across the
    AlarmDecoder web application. This includes path definitions, validation
    constants, date/time helpers, file/directory operations, random string
    generation, and compatibility wrappers for Flask-Login.
"""

import string
import random
import os
import sys
import io
import tarfile
import time

from datetime import datetime


# Define the instance folder path based on the operating system.
# This folder typically holds configuration files, logs, and other instance-specific data.
if sys.platform.startswith('win'):
    # For Windows, place the instance folder within the current working directory.
    INSTANCE_FOLDER_PATH = os.path.join(os.getcwd(), 'instance')
else:
    # For Linux/Unix-like systems, use a standard location like /opt.
    INSTANCE_FOLDER_PATH = os.path.join('/opt', 'alarmdecoder-webapp', 'instance')


# Define allowed file extensions for user avatar uploads.
ALLOWED_AVATAR_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Define constants for form input validation (e.g., length constraints).
# Used primarily in user registration or profile forms.
USERNAME_LEN_MIN = 4
USERNAME_LEN_MAX = 25

REALNAME_LEN_MIN = 4
REALNAME_LEN_MAX = 25

PASSWORD_LEN_MIN = 6
PASSWORD_LEN_MAX = 64

# Age constraints (potentially for user profiles, though not heavily used in core AD2Web).
AGE_MIN = 1
AGE_MAX = 300

# Deposit constraints (likely unused in core AD2Web, possibly from template origin).
DEPOSIT_MIN = 0.00
DEPOSIT_MAX = 9999999999.99

# Define constants for sex/gender types (potentially for user profiles).
MALE = 1
FEMALE = 2
OTHER = 9
SEX_TYPE = {
    MALE: 'Male',
    FEMALE: 'Female',
    OTHER: 'Other',
}

# Define a standard maximum string length for database models.
STRING_LEN = 64


def get_current_time():
    """Returns the current time in UTC."""
    return datetime.utcnow()


def pretty_date(dt, default=None):
    """
    Returns a human-readable string representing the time difference
    between a given datetime object and the current time (e.g., "3 days ago").

    Args:
        dt (datetime): The past datetime object to compare against now.
        default (str, optional): The string to return if the difference is negligible.
                                 Defaults to 'just now'.

    Returns:
        str: A string representing the time difference.
    """
    if default is None:
        default = 'just now'

    # Ensure comparison is done in UTC.
    now = datetime.utcnow()
    if dt > now:
        # Handle cases where dt might be slightly in the future due to timing issues.
        return default
    diff = now - dt

    # Define time periods for comparison.
    periods = (
        (diff.days // 365, 'year', 'years'),
        (diff.days // 30, 'month', 'months'),
        (diff.days // 7, 'week', 'weeks'),
        (diff.days, 'day', 'days'),
        (diff.seconds // 3600, 'hour', 'hours'),
        (diff.seconds // 60, 'minute', 'minutes'),
        (diff.seconds, 'second', 'seconds'),
    )

    # Iterate through periods and return the largest appropriate unit.
    for period, singular, plural in periods:
        if period: # Check if the period has a non-zero value
            if period == 1:
                return '%d %s ago' % (period, singular)
            else:
                return '%d %s ago' % (period, plural)

    # If no period matched (difference is very small), return the default.
    return default


def allowed_file(filename):
    """
    Checks if a filename has an extension that is allowed for avatar uploads.

    Args:
        filename (str): The name of the file to check.

    Returns:
        bool: True if the file extension is allowed, False otherwise.
    """
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_AVATAR_EXTENSIONS


def id_generator(size=10, chars=string.ascii_letters + string.digits):
    """
    Generates a random string of a specified size using the given characters.

    Args:
        size (int, optional): The desired length of the random string. Defaults to 10.
        chars (str, optional): The character set to use for generation.
                               Defaults to ASCII letters and digits.

    Returns:
        str: The randomly generated string.
    """
    return ''.join(random.choice(chars) for _ in range(size))


def make_dir(dir_path):
    """
    Creates a directory if it doesn't already exist.

    Args:
        dir_path (str): The path of the directory to create.

    Raises:
        OSError: If the directory cannot be created (e.g., due to permissions).
    """
    try:
        # os.makedirs can create intermediate directories if needed and handles existing dirs gracefully.
        os.makedirs(dir_path, exist_ok=True)
    except OSError as e:
        # Log or handle the error appropriately if needed.
        print(f"Error creating directory {dir_path}: {e}") # Consider using app logger
        raise # Re-raise the exception


def tar_add_directory(tar, name):
    """
    Adds a directory entry to a tar archive.

    Args:
        tar (tarfile.TarFile): The tarfile object to add to.
        name (str): The name/path of the directory within the archive.
    """
    ti = tarfile.TarInfo(name=name)
    ti.mtime = time.time()
    ti.type = tarfile.DIRTYPE
    ti.mode = 0o755 # Standard directory permissions
    tar.addfile(ti)


def tar_add_textfile(tar, name, data, parent_path=None):
    """
    Adds a text file entry to a tar archive.

    Args:
        tar (tarfile.TarFile): The tarfile object to add to.
        name (str): The name of the file within the archive.
        data (bytes): The content of the file as bytes.
        parent_path (str, optional): An optional parent directory path within the archive.
                                     Defaults to None.
    """
    path = name
    if parent_path:
        path = os.path.join(parent_path, name)

    # Create a TarInfo object for the file.
    ti = tarfile.TarInfo(name=path)
    ti.mtime = time.time()
    ti.size = len(data)
    ti.mode = 0o644 # Standard file permissions

    # Add the file to the archive using an in-memory BytesIO buffer.
    # Note: The original TextIOWrapper usage might be problematic if 'data' isn't text.
    # Directly using BytesIO is safer if 'data' is already bytes.
    # If 'data' is guaranteed to be text, encoding='utf-8' might be better than 'ascii'.
    tar.addfile(ti, io.BytesIO(data))

# --- Flask-Login Compatibility Wrappers ---
# These functions provide a consistent way to check user authentication status,
# accommodating potential differences between older and newer versions of Flask-Login
# where `is_authenticated` and `is_anonymous` changed from properties to methods.

def user_is_authenticated(user):
    """
    Checks if a user object is authenticated, compatible with different Flask-Login versions.

    Args:
        user: The user object (or None).

    Returns:
        bool: True if the user is authenticated, False otherwise.
    """
    if user is None:
        return False

    # Check if is_authenticated is a callable method (newer Flask-Login)
    if callable(getattr(user, 'is_authenticated', None)):
        return user.is_authenticated()
    # Otherwise, assume it's a property (older Flask-Login)
    else:
        return getattr(user, 'is_authenticated', False)

def user_is_anonymous(user):
    """
    Checks if a user object is anonymous, compatible with different Flask-Login versions.

    Args:
        user: The user object (or None).

    Returns:
        bool: True if the user is anonymous, False otherwise.
    """
    if user is None:
        # Technically, None represents an anonymous user in Flask-Login context
        return True

    # Check if is_anonymous is a callable method (newer Flask-Login)
    if callable(getattr(user, 'is_anonymous', None)):
        return user.is_anonymous()
    # Otherwise, assume it's a property (older Flask-Login)
    else:
        # Default to False if property doesn't exist, as an authenticated user isn't anonymous.
        return getattr(user, 'is_anonymous', False)
