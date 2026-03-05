-- Seed: create the baseline "self" entity for the user
INSERT INTO entities (name, type)
VALUES ('self', 'self')
ON CONFLICT (name) DO NOTHING;
