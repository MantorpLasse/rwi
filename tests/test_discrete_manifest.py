import pytest
from app.evidence.discrete_manifest import ENTRIES, ManifestEntry
def test_entries_are_distinct_and_preserve_locations():
 assert len(ENTRIES)==3 and ENTRIES[0].hash()==ENTRIES[1].hash() and ENTRIES[0].locator!=ENTRIES[1].locator
 assert ENTRIES[2].raw_end=='22L'
def test_manifest_rejects_invalid_or_aggregate():
 with pytest.raises(ValueError): ManifestEntry('u','p','a','airport_inventory','x','l').validate()
 with pytest.raises(ValueError): ManifestEntry('','','a','runway_end','','').validate()
