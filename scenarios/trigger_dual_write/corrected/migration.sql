ALTER TABLE customers ADD COLUMN email_normalized text;

CREATE FUNCTION sync_email_normalized() RETURNS trigger AS $$
BEGIN
    IF NEW.email IS DISTINCT FROM OLD.email THEN
        NEW.email_normalized := lower(NEW.email);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER customers_sync_email BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION sync_email_normalized();
