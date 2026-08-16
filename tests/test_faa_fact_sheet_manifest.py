from app.evidence.faa_fact_sheet_manifest import ROWS
def test_raw_parenthesized_year_and_counts_are_preserved():
 j=ROWS[0];assert j.years=='1996(1999)/2007' and j.count=='2' and 'page=2' in j.locator()
def test_rows_are_aggregate_historical_not_systems(): assert all(r.count in {'2','4'} for r in ROWS)
