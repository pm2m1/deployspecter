CREATE TABLE accounts (
    id    integer PRIMARY KEY,
    email text NOT NULL,
    plan  text NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro', 'team'))
);
