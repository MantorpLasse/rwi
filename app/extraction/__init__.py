"""Generic Document Extraction V1 (RWI Mission #12B).

    Snapshot -> GenericPdfExtractor -> ExtractedDocument -> ExtractedPage
    -> STOP

Extraction means: derive a non-authoritative textual representation from
one exact preserved Snapshot, while retaining provenance back to that
Snapshot and a specific page within it.

Extraction does NOT mean: truth, relevance, evidence, airport identity,
EMAS confirmation, project interpretation, CandidateFragment, Source,
SourceAssertion, Claim, Signal, or Installation. Nothing in this package
imports or constructs any of those - see
tests/test_extraction_architectural_safety.py, which enforces this by
AST inspection, not just convention.

This package never opens a database session, never makes a network call,
and never reads SearchResult/SearchQuery/Triage output - it receives
already-preserved bytes and explicit provenance, nothing else.
"""
