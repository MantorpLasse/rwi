import csv, hashlib, json, zipfile
from datetime import date
from dataclasses import dataclass
from pathlib import Path
REQUIRED=('EFF_DATE','SITE_NO','SITE_TYPE_CODE','STATE_CODE','ARPT_ID','CITY','COUNTRY_CODE','RWY_ID','RWY_END_ID','ARREST_DEVICE_CODE')
NASR_EXTERNAL_ID='faa_nasr:airport_csv:2026-08-06:06_Aug_2026_APT_CSV.zip'
def proposed_source(metadata):
 return {'title':'FAA NASR Airport CSV — 2026-08-06 cycle','source_type':'faa_nasr_apt_ars','publisher':'Federal Aviation Administration','url':metadata['final_archive_url'],'retrieved_at':date.fromisoformat(metadata['retrieved_at'][:10]),'document_reference':'06_Aug_2026_APT_CSV.zip','summary':'Official FAA NASR Airport CSV archive; APT_ARS.csv runway arresting-system records.','reliability_level':'official','external_id':NASR_EXTERNAL_ID}
@dataclass(frozen=True)
class NasrRow:
 line:int; values:dict; artifact_sha256:str; cycle:str
 def raw(self): return json.dumps(self.values,sort_keys=True,separators=(',',':'))
 def hash(self): return hashlib.sha256(self.raw().encode()).hexdigest()
 def locator(self): return f'APT_ARS.csv:line={self.line}'
def rows(zip_path:Path, metadata_path:Path):
 meta=json.loads(metadata_path.read_text(encoding='utf-8-sig'))
 actual=hashlib.sha256(zip_path.read_bytes()).hexdigest()
 if actual!=meta['sha256']: raise ValueError('artifact SHA-256 mismatch')
 with zipfile.ZipFile(zip_path) as z:
  name=next(n for n in z.namelist() if n.upper().endswith('APT_ARS.CSV'))
  with z.open(name) as h:
   for line,row in enumerate(csv.DictReader((x.decode('utf-8-sig') for x in h)),2):
    if tuple(row)!=REQUIRED: raise ValueError('unexpected APT_ARS schema')
    clean={k:row[k].strip() for k in REQUIRED}
    if clean['ARREST_DEVICE_CODE']=='EMAS': yield NasrRow(line,clean,actual,meta['nasr_cycle'])
