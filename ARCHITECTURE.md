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

## Data Flow

Documents

↓

Observations

↓

Facts

↓

Knowledge

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
