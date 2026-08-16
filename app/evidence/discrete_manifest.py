"""Reviewable, source-specific legacy evidence candidates; no inference."""
from dataclasses import dataclass
from hashlib import sha256

SUPPORTED = {"runway_end", "physical_system"}

@dataclass(frozen=True)
class ManifestEntry:
    source_url: str; publisher: str; airport_code: str; assertion_type: str
    fragment: str; locator: str; raw_runway: str | None = None; raw_end: str | None = None
    raw_product: str | None = None; raw_year: str | None = None
    quality: str = "direct_strong"; review_state: str = "unreviewed"
    def hash(self): return sha256(self.fragment.encode()).hexdigest()
    def validate(self):
        if not self.source_url or not self.publisher or not self.locator or not self.fragment.strip(): raise ValueError("source identity, locator and fragment are required")
        if self.assertion_type not in SUPPORTED: raise ValueError("unsupported assertion type")

CGF_URL="https://cuyahogacounty.gov/executive/news/press-releases-archive/2019-press-releases/2020/11/10/cuyahoga-county-airport-celebrates-completion-of-$39-million-runway-safety-area-improvement-project"
PRWEB_URL="https://www.prweb.com/releases/chicago_airports_to_install_first_ever_sustainable_emas_solution_at_midway_and_o_hare/prweb12986556.htm"
ENTRIES=(
 ManifestEntry(CGF_URL,"Cuyahoga County","CGF","runway_end","EMAS beds were completed in 2018: one at runway end 06 (322 feet) and one at runway end 24 (435 feet).","script:import_faa_tableau_gaps.py:CGF:end-06","6/24","06","EMASMAX","2018"),
 ManifestEntry(CGF_URL,"Cuyahoga County","CGF","runway_end","EMAS beds were completed in 2018: one at runway end 06 (322 feet) and one at runway end 24 (435 feet).","script:import_faa_tableau_gaps.py:CGF:end-24","6/24","24","EMASMAX","2018"),
 ManifestEntry(PRWEB_URL,"PRWeb","MDW","runway_end","The first greenEMAS bed was completed in November 2014 on runway 22L.","script:add_gadelius_greenemas_installations.py:PRWeb:MDW-22L","22L","22L","greenEMAS","November 2014"),
)
for entry in ENTRIES: entry.validate()
