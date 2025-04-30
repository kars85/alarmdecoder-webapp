from ad2web.extensions import db
from ad2web.cameras.models import Camera

def get_cameras_for_user(user_id):
    """Retrieve all Camera records for the given user."""
    return Camera.query.filter_by(user_id=user_id).all()

def get_camera(camera_id, user_id):
    """Get a single Camera by ID, ensuring it belongs to the given user."""
    return Camera.query.filter_by(id=camera_id, user_id=user_id).first()

def create_camera(user_id, name, url, username=None, password=None):
    """Create and save a new Camera record for the given user."""
    new_cam = Camera(name=name, get_jpg_url=url, username=username or "", password=password or "", user_id=user_id)
    db.session.add(new_cam)
    db.session.commit()
    return new_cam

def update_camera(camera_id, user_id, name, url, username=None, password=None):
    """Update an existing Camera (if authorized) and save changes."""
    cam = get_camera(camera_id, user_id)
    if not cam:
        return None  # Camera not found or not authorized
    cam.name = name
    cam.get_jpg_url = url
    cam.username = username or ""
    cam.password = password or ""
    db.session.commit()
    return cam

def delete_camera(camera_id, user_id):
    """Delete the Camera with given ID if it belongs to the user."""
    cam = get_camera(camera_id, user_id)
    if not cam:
        return False
    db.session.delete(cam)
    db.session.commit()
    return True
