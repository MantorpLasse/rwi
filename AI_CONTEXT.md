# RWI AI Context

## Project

RWI stands for Runway Intelligence.

RWI is an intelligence platform for runway safety, EMAS systems, airport infrastructure, projects, incidents, documents and procurement.

## Mission

Transform public aviation documents into verified, explainable and searchable intelligence.

## Technology

- Python
- FastAPI
- SQLAlchemy 2
- SQLite initially
- Alembic
- Jinja2
- HTMX
- Bootstrap
- Pytest
- GitHub

## Architecture Principles

- Domain-oriented structure
- Source-first data model
- Documents are first-class entities
- Every verified fact must have a source
- Raw data, verified data and intelligence must remain separate
- Scores must be reproducible and explainable
- History must be preserved
- Prefer simple solutions
- Avoid premature microservices
- Use SQLite until there is a concrete need for PostgreSQL

## Core Domains

### Airport Infrastructure

- Airport
- Runway
- Runway End
- EMAS Bed

### Projects

- Project
- Event
- Funding
- Procurement

### Documents

- Source
- Document
- Document Link

### Knowledge

Future scope:

- Observation
- Verification
- Fact
- Research Note

### Intelligence

Future scope:

- RWI Score
- Opportunity
- Risk
- Confidence
- Score Calculation

## Current Sprint

Sprint 1 – Core Data

## Current Scope

Implement:

- Airport
- Runway
- Runway End
- EMAS Bed
- Project
- Source
- Document
- Basic CRUD
- Search
- FAA reference import

Do not yet implement:

- AI search
- Crawler
- Knowledge graph database
- Neo4j
- PostgreSQL
- React
- Authentication
- Background workers
- Automatic scoring

## Important Data Rule

A runway has two runway ends.

An EMAS Bed belongs to one runway end.

A runway can therefore have zero, one or two EMAS Beds.

## FAA Data

FAA currently reports EMAS installations and arrestments publicly, but no confirmed public API has been identified.

Initial FAA data ingestion should use:

- HTML parsing where tables exist
- Curated CSV reference files
- FAA Airport Diagrams
- FAA Chart Supplements
- Manual verification

Automation may create candidate observations. Human verification is required before data becomes verified.

## Current Next Task

Refactor the MVP models into domain-oriented SQLAlchemy model files and create Alembic migrations.