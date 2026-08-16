import argparse
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Airport,Source,SourceAssertion
from app.evidence.faa_fact_sheet_manifest import ROWS,URL
def run(s,apply=False):
 d={'candidates':0,'would_create':0,'already_present':0,'skipped':0,'nasr_candidates':0,'nasr_skipped':'no preserved APT_ARS artifact'}
 src=s.scalar(select(Source).where(Source.url==URL))
 for r in ROWS:
  d['candidates']+=1;a=s.scalar(select(Airport).where((Airport.faa_code==r.code)|(Airport.iata_code==r.code)))
  if not src or not a:d['skipped']+=1;continue
  e=s.scalar(select(SourceAssertion.id).where(SourceAssertion.source_id==src.id,SourceAssertion.source_locator==r.locator(),SourceAssertion.raw_fragment_hash==r.hash()))
  if e:d['already_present']+=1;continue
  d['would_create']+=1
  if apply:s.add(SourceAssertion(source_id=src.id,airport_id=a.id,assertion_type='historical',raw_airport_name=r.fragment.split(' 2 ')[0],raw_count=r.count,raw_year_date_wording=r.years,raw_relevant_text=r.fragment,source_locator=r.locator(),raw_fragment_hash=r.hash(),artifact_identity='docs/sources/2011-03-07_faa-emas-fact-sheet.pdf',parser_identifier='faa-fact-sheet-manifest-v1',evidence_quality='partial',review_state='reviewed'))
 if apply:s.commit()
 return d
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
 with SessionLocal() as s:print(run(s,a.apply))
