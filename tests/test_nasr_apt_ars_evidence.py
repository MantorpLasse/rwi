from pathlib import Path
def test_authentic_fixture_has_required_schema_and_emas_only():
 import csv
 r=list(csv.DictReader(Path('tests/fixtures/nasr_apt_ars_sample.csv').open()))
 e=[x for x in r if x['ARREST_DEVICE_CODE']=='EMAS']
 assert len(e)==2 and {x['RWY_END_ID'] for x in e}=={'04R','22L'} and all(x['ARREST_DEVICE_CODE']=='EMAS' for x in e)
