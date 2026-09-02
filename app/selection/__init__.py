"""Fragment Selection V1 (RWI Mission #13B).

    ExtractedDocument -> deterministic local-page matching -> bounded
    context windows -> overlap/nearby-window merging -> generic
    repeated-header/footer suppression (matching only) -> FragmentSelection
    -> STOP

SELECT means: deterministically identify and bound a portion of one
ExtractedPage's parser-extracted text that contains a literal term or
nearby context making it potentially worth human review, while preserving
its exact original substring, exact page/document provenance, and an
explicit literal reason.

SELECT does NOT mean evidence accepted, EMAS confirmed, project
confirmed, airport identity confirmed, vendor confirmed, opportunity
detected, a claim extracted, or a contradiction resolved. Nothing in this
package imports or constructs CandidateFragment, Source, SourceAssertion,
Signal, Installation, or any governance-mutation code - see
tests/test_selection_architectural_safety.py, which enforces this by AST
inspection, not just convention. Nothing here opens a database session or
a network connection, and nothing here imports SearchResult, SearchQuery,
or TriagedResult/triage rank.
"""
