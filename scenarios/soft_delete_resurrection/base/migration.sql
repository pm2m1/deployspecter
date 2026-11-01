CREATE TABLE users_v2 (
    id         integer PRIMARY KEY,
    email      text NOT NULL,
    deleted_at timestamptz
);
