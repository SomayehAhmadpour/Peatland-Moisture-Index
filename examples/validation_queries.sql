-- Validation queries for iris_staging (PostGIS). Run with psql against the
-- staging container, e.g.:
--   docker compose exec staging-db psql -U iris_staging -d iris_staging_db -f /dev/stdin < examples/validation_queries.sql

-- 1. Every processed or rejected scene is in the ledger, with a reason when rejected.
SELECT selected, split_part(coalesce(rejection_reason, 'selected'), ':', 1) AS reason, count(*)
FROM iris_staging.s2_scene
GROUP BY 1, 2
ORDER BY 1 DESC, 3 DESC;

-- 2. No duplicate acquisitions survive selection (platform + tile + UTC day).
SELECT platform, mgrs_tile, acquired_at::date AS day, count(*)
FROM iris_staging.s2_scene
WHERE selected
GROUP BY 1, 2, 3
HAVING count(*) > 1;                                   -- expect 0 rows

-- 3. Parcel geometries are valid, in EPSG:25832, with plausible areas.
SELECT parcel_id, kind, ST_SRID(geometry) AS srid, ST_IsValid(geometry) AS valid,
       round((ST_Area(geometry) / 1e4)::numeric, 2) AS area_ha
FROM iris_staging.s2_ndwi_parcel
ORDER BY parcel_id;

-- 4. Pixel accounting closes and NDWI stays in [-1, 1] (also enforced by CHECK constraints).
SELECT count(*) FILTER (WHERE n_pixels <> n_valid + n_nodata + n_scl_masked + n_bright_surface + n_non_positive_reflectance
                                         + n_zero_denominator + n_out_of_range) AS count_mismatch,
       count(*) FILTER (WHERE ndwi_min < -1 OR ndwi_max > 1)                        AS out_of_range,
       count(*) FILTER (WHERE status = 'accepted' AND valid_fraction < 0.8)          AS accepted_below_threshold
FROM iris_staging.s2_ndwi_observation;                 -- expect 0, 0, 0

-- 5. Monthly NDWI for the bog core vs. the reference field (accepted, real data only).
SELECT parcel_id, date_trunc('month', acquired_at)::date AS month,
       count(*) AS scenes, round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ndwi_median)::numeric, 3) AS ndwi
FROM iris_staging.s2_ndwi_observation o
JOIN iris_staging.s2_ndwi_run r USING (run_id)
WHERE status = 'accepted' AND NOT r.synthetic_data AND parcel_id IN ('wr-core-100m', 'ref-field-100m')
GROUP BY 1, 2
ORDER BY 2, 1;

-- 6. Summer (JJA) comparison across years: only same-season values, real data only.
SELECT parcel_id, extract(year FROM acquired_at)::int AS year, count(*) AS scenes,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ndwi_median)::numeric, 3) AS ndwi_jja
FROM iris_staging.s2_ndwi_observation o
JOIN iris_staging.s2_ndwi_run r USING (run_id)
WHERE status = 'accepted' AND NOT r.synthetic_data AND extract(month FROM acquired_at) IN (6, 7, 8)
GROUP BY 1, 2
ORDER BY 1, 2;

-- 7. Provenance of one observation: from result back to the exact COG files.
SELECT o.observation_id, o.ndwi_median, s.collection, s.processing_baseline, s.boa_offset_applied_flag,
       s.boa_offset, s.green_href, s.nir_href, s.scl_href, r.config_sha256
FROM iris_staging.s2_ndwi_observation o
JOIN iris_staging.s2_scene s USING (item_id)
JOIN iris_staging.s2_ndwi_run r ON r.run_id = o.run_id
WHERE o.parcel_id = 'wr-core-100m' AND o.status = 'accepted' AND NOT r.synthetic_data
ORDER BY o.acquired_at DESC
LIMIT 1;
