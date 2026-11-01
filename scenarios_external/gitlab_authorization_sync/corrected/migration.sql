CREATE TABLE project_grants_compact (
    user_id      bigint   NOT NULL,
    project_id   bigint   NOT NULL,
    access_level smallint NOT NULL,
    PRIMARY KEY (user_id, project_id)
);

CREATE FUNCTION sync_project_grants() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        WITH remaining AS (
            SELECT min(access_level)::smallint AS lowest
            FROM project_grants
            WHERE user_id = OLD.user_id AND project_id = OLD.project_id
            HAVING count(*) > 0
        ), refreshed AS (
            INSERT INTO project_grants_compact (user_id, project_id, access_level)
            SELECT OLD.user_id, OLD.project_id, lowest FROM remaining
            ON CONFLICT (user_id, project_id) DO UPDATE SET access_level = EXCLUDED.access_level
        )
        DELETE FROM project_grants_compact
        WHERE user_id = OLD.user_id AND project_id = OLD.project_id
          AND NOT EXISTS (SELECT 1 FROM remaining);
        RETURN OLD;
    END IF;

    INSERT INTO project_grants_compact (user_id, project_id, access_level)
    VALUES (NEW.user_id, NEW.project_id, NEW.access_level)
    ON CONFLICT (user_id, project_id) DO UPDATE SET access_level = EXCLUDED.access_level;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER project_grants_sync AFTER INSERT OR UPDATE OR DELETE ON project_grants
    FOR EACH ROW EXECUTE FUNCTION sync_project_grants();
