-- Expand prompt_version for descriptive AI prompt identifiers.
-- Existing interpretation records and data are preserved.

ALTER TABLE gwas_interpretation
ALTER COLUMN prompt_version TYPE VARCHAR(100);
