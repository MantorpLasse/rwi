import csv
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Airport, Installation, Runway, Signal, Source, SourceAssertion
from scripts.backfill_legacy_source_assertions import FAA_TITLE, run

def setup(tmp_path: Path):
    engine=create_engine('sqlite:///:memory:'); Base.metadata.create_all(engine); s=Session(engine)
    a=Airport(name='A', country='USA', faa_code='AAA'); r=Runway(airport=a, designation='06/24')
    faa=Source(title=FAA_TITLE, source_type='faa_tableau'); grant=Source(title='Grant', source_type='usaspending_grant', external_id='usaspending:1', summary='EMAS on Runway 06')
    s.add_all((a,r,faa,grant,Installation(airport=a,source=faa,type='EMASMAX'),Signal(airport=a,source=grant,title='g',category='new_installation',confidence='high'))); s.commit()
    path=tmp_path/'faa.csv'; path.write_text('ARPT_ID,ARPT_NAME,TYPE\nAAA,A,EMASMAX\nAAA,A,EMASMAX\n', encoding='utf-8')
    return engine,s,path

def test_dry_run_is_deterministic_and_does_not_mutate(tmp_path):
    engine,s,path=setup(tmp_path); first=run(s,csv_path=path); second=run(s,csv_path=path)
    assert first==second and first['stats']['would_create']==3 and s.query(SourceAssertion).count()==0 and s.query(Installation).count()==1 and s.query(Signal).count()==1
    s.close(); engine.dispose()

def test_apply_is_idempotent_and_same_airport_year_type_stay_distinct(tmp_path):
    engine,s,path=setup(tmp_path); run(s,apply=True,csv_path=path); again=run(s,apply=True,csv_path=path)
    assert s.query(SourceAssertion).count()==3 and again['stats']['already_present']==3
    assert len(s.query(SourceAssertion).filter_by(assertion_type='airport_inventory').all())==2
    s.close(); engine.dispose()

def test_unknown_and_ambiguous_legacy_evidence_are_not_invented(tmp_path):
    engine,s,path=setup(tmp_path); result=run(s,csv_path=path)
    assertion=next(item for item in __import__('scripts.backfill_legacy_source_assertions',fromlist=['candidates']).candidates(s,path)[0] if item.values['assertion_type']=='airport_inventory')
    assert assertion.values.get('runway_id') is None and assertion.values.get('runway_end') is None and result['installation_coverage']['partial']==1
    s.close(); engine.dispose()
