BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> b2dd4b2ded65

CREATE TABLE login_attempts (
    id SERIAL NOT NULL, 
    key VARCHAR(64) NOT NULL, 
    created TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_login_attempts_created ON login_attempts (created);

CREATE INDEX ix_login_attempts_key ON login_attempts (key);

CREATE TABLE users (
    id SERIAL NOT NULL, 
    email VARCHAR(254) NOT NULL, 
    name VARCHAR(120) NOT NULL, 
    password_hash TEXT NOT NULL, 
    role VARCHAR(20) NOT NULL, 
    active BOOLEAN NOT NULL, 
    wiw_user_id INTEGER NOT NULL, 
    location VARCHAR(120) NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (email), 
    UNIQUE (wiw_user_id)
);

CREATE TABLE changes (
    id SERIAL NOT NULL, 
    employee_id INTEGER NOT NULL, 
    location VARCHAR(120) NOT NULL, 
    wiw_user_id INTEGER NOT NULL, 
    action VARCHAR(20) NOT NULL, 
    event_id INTEGER, 
    proposed JSON NOT NULL, 
    before JSON NOT NULL, 
    read_start VARCHAR(60) NOT NULL, 
    read_end VARCHAR(60) NOT NULL, 
    status VARCHAR(40) NOT NULL, 
    employee_note TEXT NOT NULL, 
    manager_note TEXT NOT NULL, 
    manager_id INTEGER, 
    dry_run BOOLEAN, 
    created TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(employee_id) REFERENCES users (id), 
    FOREIGN KEY(manager_id) REFERENCES users (id)
);

CREATE INDEX ix_changes_employee_id ON changes (employee_id);

CREATE INDEX ix_changes_status ON changes (status);

CREATE TABLE manager_scopes (
    id SERIAL NOT NULL, 
    manager_id INTEGER NOT NULL, 
    location VARCHAR(120) NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(manager_id) REFERENCES users (id), 
    UNIQUE (manager_id, location)
);

CREATE TABLE sessions (
    digest VARCHAR(64) NOT NULL, 
    user_id INTEGER NOT NULL, 
    expires TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (digest), 
    FOREIGN KEY(user_id) REFERENCES users (id)
);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE audit (
    id SERIAL NOT NULL, 
    change_id INTEGER NOT NULL, 
    actor_id INTEGER, 
    event VARCHAR(60) NOT NULL, 
    details JSON NOT NULL, 
    created TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(actor_id) REFERENCES users (id), 
    FOREIGN KEY(change_id) REFERENCES changes (id)
);

CREATE INDEX ix_audit_change_id ON audit (change_id);

INSERT INTO alembic_version (version_num) VALUES ('b2dd4b2ded65') RETURNING alembic_version.version_num;

-- Running upgrade b2dd4b2ded65 -> c42a_audit_guard

CREATE FUNCTION deny_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit rows are append-only'; END; $$;

CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation();

UPDATE alembic_version SET version_num='c42a_audit_guard' WHERE alembic_version.version_num = 'b2dd4b2ded65';

-- Running upgrade c42a_audit_guard -> d73b_weekly

CREATE TABLE weekly_schedules (
    id SERIAL NOT NULL, 
    change_id INTEGER NOT NULL, 
    employee_id INTEGER NOT NULL, 
    effective_date DATE NOT NULL, 
    days JSON NOT NULL, 
    dry_run BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (change_id), 
    FOREIGN KEY(change_id) REFERENCES changes (id), 
    FOREIGN KEY(employee_id) REFERENCES users (id)
);

CREATE INDEX ix_weekly_schedules_employee_id ON weekly_schedules (employee_id);

CREATE TABLE managed_events (
    id SERIAL NOT NULL, 
    employee_id INTEGER NOT NULL, 
    event_id INTEGER NOT NULL, 
    snapshot JSON NOT NULL, 
    active BOOLEAN NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(employee_id) REFERENCES users (id), 
    UNIQUE (event_id)
);

CREATE INDEX ix_managed_events_employee_id ON managed_events (employee_id);

UPDATE alembic_version SET version_num='d73b_weekly' WHERE alembic_version.version_num = 'c42a_audit_guard';

COMMIT;


-- Administrator changes (Alembic e81_admin_audit)
CREATE TABLE admin_audit (
 id SERIAL PRIMARY KEY,
 actor_id INTEGER NOT NULL REFERENCES users(id),
 target_id INTEGER NOT NULL REFERENCES users(id),
 details JSON NOT NULL,
 created TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Two-location routing and email queue (Alembic f92_locations_email)
ALTER TABLE users ADD COLUMN secondary_location VARCHAR(120) NOT NULL DEFAULT '';
ALTER TABLE changes ADD COLUMN secondary_location VARCHAR(120) NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN notification_email VARCHAR(254) NOT NULL DEFAULT '';
CREATE TABLE locations (name VARCHAR(120) PRIMARY KEY);
INSERT INTO locations (name) SELECT location FROM users UNION SELECT location FROM manager_scopes;
CREATE TABLE email_outbox (
 id SERIAL PRIMARY KEY,
 change_id INTEGER NOT NULL REFERENCES changes(id),
 recipient_id INTEGER NOT NULL REFERENCES users(id),
 event VARCHAR(40) NOT NULL, recipient VARCHAR(254) NOT NULL,
 subject VARCHAR(254) NOT NULL, body TEXT NOT NULL,
 status VARCHAR(30) NOT NULL, attempts INTEGER NOT NULL,
 last_error VARCHAR(120) NOT NULL,
 created TIMESTAMP WITH TIME ZONE NOT NULL,
 next_attempt TIMESTAMP WITH TIME ZONE NOT NULL,
 UNIQUE(change_id,recipient_id,event)
);
