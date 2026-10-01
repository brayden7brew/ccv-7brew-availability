from sqlalchemy import select, func
from fastapi import HTTPException
from .models import User, Scope, Location

def assigned_locations(person):
    return [value for value in (person.location, person.secondary_location) if value]

def location_choices(db):
    # Include existing operator-created mappings for backward compatibility.
    return sorted(set(db.scalars(select(Location.name))) | set(db.scalars(select(User.location))) |
        {v for v in db.scalars(select(User.secondary_location)) if v} | set(db.scalars(select(Scope.location))))

def lock_admin_changes(db):
    # All admin-role mutations share a stable row lock in PostgreSQL.
    db.scalar(select(User).order_by(User.id).limit(1).with_for_update())

def check_admin_cap(db):
    if db.scalar(select(func.count()).select_from(User).where(User.role=='admin')) >= 5:
        raise HTTPException(409,'The portal supports up to five administrators. Remove an administrator role before adding another.')
