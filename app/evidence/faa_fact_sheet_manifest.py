"""Exact 2011 FAA PDF table fragments, intentionally aggregate/historical."""
from dataclasses import dataclass
from hashlib import sha256
URL='http://www.faa.gov/news/fact_sheets/news_story.cfm?newsId=12497'
@dataclass(frozen=True)
class Row:
 code:str; page:int; fragment:str; count:str; years:str
 def locator(self): return f'docs/sources/2011-03-07_faa-emas-fact-sheet.pdf:page={self.page}:table=EMAS Installations:airport={self.code}'
 def hash(self): return sha256(self.fragment.encode()).hexdigest()
ROWS=(
 Row('JFK',2,'JFK International Jamaica, NY 2 1996(1999)/2007','2','1996(1999)/2007'),
 Row('BOS',3,'Boston Logan Boston, MA 2 2005/2006','2','2005/2006'),
 Row('MDW',3,'Chicago Midway Chicago, IL 4 2006/2007','4','2006/2007'),
 Row('ORD',3,"Chicago-O'Hare Chicago, IL 2 2008",'2','2008'),
 Row('LGA',3,'LaGuardia Flushing, NY 2 2005','2','2005'),
 Row('FLL',3,'Fort Lauderdale International Fort Lauderdale, FL 2 2004','2','2004'),
)
