from collections import Counter
from pathlib import Path
import json
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Source,SourceAssertion,Airport
from app.evidence.nasr_apt_ars import rows,proposed_source,NASR_EXTERNAL_ID
Z=Path('data/raw/nasr/2026-08-06/06_Aug_2026_APT_CSV.zip');M=Path(str(Z)+'.metadata.json')
def run(session=None,apply=False):
 r=list(rows(Z,M)); by=Counter(x.values['ARPT_ID'] for x in r)
 meta=json.loads(M.read_text(encoding='utf-8-sig')); existing=session.scalar(select(Source).where(Source.external_id==NASR_EXTERNAL_ID)) if session else None
 result={'candidates':len(r),'nasr_source_exists':bool(existing),'source_would_create':0 if existing else 1,'proposed_source':proposed_source(meta),'would_create':len(r),'already_present':0,'skipped':0,'duplicate_identities':len(r)-len({(x.locator(),x.hash()) for x in r}),'malformed_rows':0,'by_airport':dict(by),'multiple_ends':{k:v for k,v in by.items() if v>1}}
 if apply:
  if not existing: existing=Source(**proposed_source(meta));session.add(existing);session.flush()
  made=0;present=0
  for x in r:
   e=session.scalar(select(SourceAssertion.id).where(SourceAssertion.source_id==existing.id,SourceAssertion.artifact_identity==x.artifact_sha256,SourceAssertion.source_locator==x.locator(),SourceAssertion.raw_fragment_hash==x.hash()))
   if e:present+=1;continue
   airport=session.scalar(select(Airport).where((Airport.faa_code==x.values['ARPT_ID'])|(Airport.iata_code==x.values['ARPT_ID'])))
   session.add(SourceAssertion(source_id=existing.id,airport_id=airport.id if airport else None,assertion_type='runway_end',raw_airport_identifier=x.values['ARPT_ID'],raw_airport_name=x.values['CITY'],raw_runway_value=x.values['RWY_ID'],raw_runway_end_value=x.values['RWY_END_ID'],raw_relevant_text=x.raw(),source_locator=x.locator(),raw_fragment_hash=x.hash(),artifact_identity=x.artifact_sha256,parser_identifier='faa-nasr-apt-ars/2026-08-06',evidence_quality='direct_strong',review_state='unreviewed')) ;made+=1
  session.commit();result['would_create']=made;result['already_present']=present
 return result
if __name__=='__main__':
 import argparse;p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
 with SessionLocal() as s: print(run(s,a.apply))
