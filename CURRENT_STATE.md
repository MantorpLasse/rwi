# Current State

## Project

RWI – Runway Intelligence

## Version

0.1.0

## Current Sprint

Sprint 1 – Core Data

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

## Current Focus

Build the first maintainable RWI data model and CRUD workflow.

## Sprint 1 Scope

- Airport
- Runway
- Runway End
- EMAS Bed
- Project
- Source
- Document
- FAA EMAS reference import

## Next Task

Refactor the current MVP into domain-based SQLAlchemy models.

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