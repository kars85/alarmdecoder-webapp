# ad2web/utils/path_utils.py

"""Utilities for filesystem paths, directories, and file archiving operations."""

import os
import io
import tarfile
import time

# Default instance folder path (for storing instance-specific files)
INSTANCE_FOLDER_PATH: str = os.path.join('/opt', 'alarmdecoder-webapp', 'instance')

# Allowed file extensions for user avatar uploads
ALLOWED_AVATAR_EXTENSIONS: set[str] = {'png', 'jpg', 'jpeg', 'gif'}


def make_dir(dir_path: str) -> None:
    """Create a directory at the specified path if it does not already exist."""
    try:
        if not os.path.exists(dir_path):
            os.mkdir(dir_path)
    except Exception as e:
        # Re-raise any exception to let the caller handle it
        raise e


def allowed_file(filename: str) -> bool:
    """Check if a filename has an allowed extension for avatar uploads.

    Returns True if the file's extension (after the last dot) is in ALLOWED_AVATAR_EXTENSIONS.
    """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_AVATAR_EXTENSIONS


def tar_add_directory(tar: tarfile.TarFile, name: str) -> None:
    """Add an empty directory entry to an open tar archive.

    Sets the directory name with current timestamp and default permissions.
    """
    ti = tarfile.TarInfo(name=name)
    ti.mtime = time.time()
    ti.type = tarfile.DIRTYPE
    ti.mode = 0o755  # Directory permissions (rwxr-xr-x)
    tar.addfile(ti)


def tar_add_textfile(tar: tarfile.TarFile, name: str, data: bytes, parent_path: str = None) -> None:
    """Add a text file with the given data to an open tar archive.

    If parent_path is provided, the file will be created under that directory inside the archive.
    'data' should be provided as bytes (text content encoded appropriately).
    """
    # Determine the full path for the file inside the archive
    path = name if parent_path is None else os.path.join(parent_path, name)
    ti = tarfile.TarInfo(name=path)
    ti.mtime = time.time()
    ti.size = len(data)
    # Add the file to the tar archive using an in-memory bytes buffer
    tar.addfile(ti, io.TextIOWrapper(buffer=io.BytesIO(data), encoding='ascii'))
