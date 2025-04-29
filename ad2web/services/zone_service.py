from sqlalchemy.exc import IntegrityError

from ..extensions import db
from .models import Zone

class ZoneService:
    """Service layer for zone management logic."""

    @staticmethod
    def list_zones():
        """Return all zones."""
        return Zone.query.all()

    @staticmethod
    def get_zone(zone_id):
        """Retrieve a zone by its zone_id (the panel zone number)."""
        return Zone.query.filter_by(zone_id=zone_id).first()

    @staticmethod
    def create_zone(zone_id, name, description):
        """Create and persist a new zone. Returns the new Zone or raises IntegrityError."""
        zone = Zone(zone_id=zone_id, name=name, description=description)
        db.session.add(zone)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # Re-raise to let view handle duplicate zone_id error (if needed)
            raise
        return zone

    @staticmethod
    def update_zone(zone_id, name, description):
        """Update an existing zone identified by zone_id. Returns the updated Zone or None if not found."""
        zone = ZoneService.get_zone(zone_id)
        if not zone:
            return None
        zone.name = name
        zone.description = description
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise
        return zone

    @staticmethod
    def delete_zone(zone_id):
        """Delete a zone by its zone_id. Returns True if deleted, False if not found."""
        zone = ZoneService.get_zone(zone_id)
        if not zone:
            return False
        db.session.delete(zone)
        db.session.commit()
        return True

    @staticmethod
    def import_zones(zone_data_list):
        """
        Import zones in bulk from a list of zone data (each item is a dict with 'address', 'zone_name').
        Deletes existing zones and adds new ones from the provided data.
        Returns a dict of imported zones or 0 if none imported.
        """
        if not zone_data_list or len(zone_data_list) == 0:
            # No data provided
            return "Failure to enumerate zones, possibly unsupported"
        # Delete all existing zones
        try:
            db.session.query(Zone).delete()
            db.session.commit()
        except Exception:
            db.session.rollback()
            # If deletion fails, abort import
            return "Error deleting existing zones."
        imported = {}
        num_imported = 0
        for entry in zone_data_list:
            addr = entry.get('address')
            name = entry.get('zone_name', '')
            desc = entry.get('zone_name') if entry.get('zone_name') else 'Generated - No Alpha Found'
            # Avoid duplicates in the provided list
            if addr in imported:
                continue
            # Create new Zone object
            zone = Zone(zone_id=addr, name=name, description=desc)
            db.session.add(zone)
            imported[addr] = {'zone_id': addr, 'name': name, 'description': desc}
            num_imported += 1
        if num_imported > 0:
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return "Error importing zones (duplicate IDs)."
        # If no zones imported (all were duplicates or none provided), return 0
        return imported if num_imported > 0 else 0
