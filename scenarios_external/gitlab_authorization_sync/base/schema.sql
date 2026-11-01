CREATE TABLE project_grants (
    user_id      bigint  NOT NULL,
    project_id   bigint  NOT NULL,
    access_level integer NOT NULL,
    PRIMARY KEY (user_id, project_id, access_level)
);
