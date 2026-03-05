-- One-time fix: deduplicate any existing alias arrays
UPDATE entities
SET aliases = ARRAY(SELECT DISTINCT unnest(aliases))
WHERE array_length(aliases, 1) > 0;
