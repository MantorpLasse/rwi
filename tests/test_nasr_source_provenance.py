from app.evidence.nasr_apt_ars import NASR_EXTERNAL_ID,proposed_source
def test_nasr_source_identity_is_deterministic():
 m={'final_archive_url':'https://nfdc.faa.gov/x.zip','retrieved_at':'2026-08-16T00:00:00Z'}
 assert proposed_source(m)['external_id']==NASR_EXTERNAL_ID
