-- Auxiliary navigation must resolve through original chunk membership, not
-- through a common heading alone.
CREATE INDEX kf_records_original_chunks ON kf_records
USING gin ((metadata->'original'->'original_chunk_ids'));
