# RWI Architecture

## Purpose

RWI is an intelligence platform for runway safety.

The architecture is designed to be simple, explainable and maintainable.

---

## Architecture Layers

Presentation

↓

API

↓

Knowledge

↓

Data

↓

Infrastructure

---

## Core Domains

- Airport
- Runway
- Runway End
- EMAS Bed
- Project
- Document
- Source
- Observation
- Fact
- Intelligence

---

## Domain Rules

### Airport

- An Airport contains one or more Runways.

### Runway

- A Runway belongs to exactly one Airport.
- A Runway has exactly two Runway Ends.

### Runway End

- A Runway End belongs to exactly one Runway.
- A Runway End may have zero or one current EMAS Bed.

### EMAS Bed

- An EMAS Bed belongs to exactly one Runway End.
- Historical EMAS Beds must be preserved and never overwritten.

---

## Data Flow

Documents

↓

Observations

↓

Facts

↓

Intelligence

---

## Technology

- Python
- FastAPI
- SQLAlchemy
- SQLite
- Alembic
- Jinja2
- HTMX
- Bootstrap

---

## Principles

- Every Fact has a Source.
- Documents are first-class entities.
- History is never deleted.
- Intelligence must always be explainable.
- Design enough to start building. Then build.
