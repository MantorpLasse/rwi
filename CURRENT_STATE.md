# Current State

## Project

RWI – Runway Intelligence

## Version

0.1.0

## Current Status

Sprint 1 – Core Data completed.

## Completed

- Git repository created
- GitHub repository created
- FastAPI MVP running locally
- SQLite database working
- Project vision documented
- Project constitution documented
- Glossary documented
- Roadmap documented
- Architecture overview documented
- Initial domain model documented
- Raw, verified and intelligence data layers defined
- Domain-oriented SQLAlchemy models
- RunwayEnd
- EmasBed
- Model package
- SQLAlchemy contract tests
- Infrastructure tests
- Alembic configured
- Baseline migration created and validated
- Explicit database initialization
- 24 passing tests

## Current Focus

Prepare Sprint 2 – Source-first Intelligence.

## Sprint 1 Scope

- Airport
- Runway
- Runway End
- EMAS Bed
- Project
- Source
- Document
- FAA EMAS reference import

## Current Architecture

- Airport
- Runway
- RunwayEnd
- EmasBed
- Project
- Source
- Incident

## Next Sprint

Sprint 2 – Source-first Intelligence

### Primary Goals

- Source
- Document
- Observation
- Verification
- Fact
- Intelligence

## Next Task

Begin the source-first intelligence domain design and implementation.

## Known Issues

- Some seed-data source URLs point to publisher home pages rather than exact documents.
- The FAA installation inventory does not appear to have a public API.
- FAA installation data must initially be imported from public pages, maps and airport documents.
- The SQLite data directory must be created automatically.

## Working Rules

- Every fact must have a source.
- Intelligence must be explainable.
- Raw data is never treated as verified data.
- AI may assist but may not invent facts.
- Design enough to start building. Then build.

## Last Updated

2026-07-17
