import os
os.environ['SECRET_KEY'] = 'test-secret-not-for-production-123456789'
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL', 'sqlite://')
os.environ['WIW_MODE'] = 'demo'
os.environ['WIW_AUTO_REFRESH'] = 'false'
os.environ['WIW_DEVELOPER_KEY'] = ''
os.environ['DRY_RUN'] = 'true'
os.environ['EMAIL_ENABLED'] = 'false'
os.environ['ENVIRONMENT'] = 'development'
os.environ['ALLOWED_HOSTS'] = 'testserver,localhost,127.0.0.1'
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.db import Base, get_db
from app.models import User, Scope
from app.security import hasher
from app.main import app

@pytest.fixture
def db():
    url = os.environ['DATABASE_URL']
    engine = create_engine(url, **({'poolclass': StaticPool, 'connect_args': {'check_same_thread': False}} if url == 'sqlite://' else {}))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        for email, role, location, wid in [('employee@test.local','employee','North',1),('manager@test.local','manager','North',2),('other@test.local','manager','South',3)]:
            session.add(User(email=email, name=email.split('@')[0], role=role, location=location,
                wiw_user_id=wid, password_hash=hasher.hash('test-password-123')))
        session.flush()
        session.add_all([Scope(manager_id=2, location='North'), Scope(manager_id=3, location='South')])
        session.commit()
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()

@pytest.fixture
def client(db):
    def override(): yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as client: yield client
    app.dependency_overrides.clear()
