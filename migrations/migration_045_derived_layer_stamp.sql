-- migration_045_derived_layer_stamp.sql
--
-- WHAT: rows DERIVED from vectors carry the checksum identity of the model they were computed under.
-- Since migration_042 every vector row names the weights that produced it. Nothing stamped the rows derived from
-- those vectors -- a `memory_links` strength from a cosine distance, a community's membership, a community summary --
-- so after a model change a coverage report could read 100% while every link strength was a number from the old
-- space.
--
-- ONE nullable column, `weights_hash TEXT`, NO DEFAULT, on the three derived tables. A writer that computed the row
-- from vectors writes the install lock's hash; a row asserted by a person (a manual link, no vector involved) stays
-- NULL and is reported as UNSTAMPED, apart from STALE (stamped under another hash). Never back-filled: a row written
-- before this column existed keeps NULL, because the hash it was computed under is not knowable now.
--
-- ALREADY APPLIED is decided from the WORLD (schema_migrator.migration_deltas): the three columns are this file's
-- delta. Applied statement-at-a-time inside ONE transaction.
ALTER TABLE memory_links ADD COLUMN weights_hash TEXT;
ALTER TABLE memory_communities ADD COLUMN weights_hash TEXT;
ALTER TABLE community_summaries ADD COLUMN weights_hash TEXT;
