import argparse
from sqlalchemy import select
from app.database import SessionLocal
from app.models import Airport, Source, SourceAssertion
from app.evidence.discrete_manifest import ENTRIES

def run(session, apply=False):
 stats={"candidates":0,"would_create":0,"already_present":0,"skipped":0}
 for e in ENTRIES:
  stats["candidates"]+=1
  source=session.scalar(select(Source).where(Source.url==e.source_url))
  airport=session.scalar(select(Airport).where((Airport.faa_code==e.airport_code)|(Airport.iata_code==e.airport_code)))
  if not source or not airport: stats["skipped"]+=1; continue
  exists=session.scalar(select(SourceAssertion.id).where(SourceAssertion.source_id==source.id,SourceAssertion.source_locator==e.locator,SourceAssertion.raw_fragment_hash==e.hash()))
  if exists: stats["already_present"]+=1; continue
  stats["would_create"]+=1
  if apply: session.add(SourceAssertion(source_id=source.id,airport_id=airport.id,assertion_type=e.assertion_type,raw_runway_value=e.raw_runway,raw_runway_end_value=e.raw_end,raw_product_type=e.raw_product,raw_year_date_wording=e.raw_year,raw_relevant_text=e.fragment,source_locator=e.locator,raw_fragment_hash=e.hash(),artifact_identity=e.source_url,parser_identifier="discrete-manifest-v1",evidence_quality=e.quality,review_state=e.review_state))
 if apply: session.commit()
 return stats
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
 with SessionLocal() as s: print(run(s,a.apply))
