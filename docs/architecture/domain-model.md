# RWI Domain Model

This document describes the initial domain model for RWI.

The model is intentionally small enough to start building. It will evolve through migrations and documented architecture decisions.

## Airport Infrastructure

```mermaid
erDiagram
    AIRPORT ||--o{ RUNWAY : contains
    RUNWAY ||--|{ RUNWAY_END : has
    RUNWAY_END ||--o{ EMAS_BED : protects

    AIRPORT {
        uuid id
        string name
        string iata_code
        string icao_code
        string faa_code
        string country
        string state_region
        string city
        decimal latitude
        decimal longitude
        string website_url
    }

    RUNWAY {
        uuid id
        uuid airport_id
        string designation
        integer length_m
        integer width_m
        string surface
    }

    RUNWAY_END {
        uuid id
        uuid runway_id
        string designation
        string heading
        integer resa_length_m
        string notes
    }

    EMAS_BED {
        uuid id
        uuid runway_end_id
        string manufacturer
        string product_name
        integer installation_year
        string status
        decimal length_m
        decimal width_m
        boolean faa_accepted
    }
```

## Knowledge Flow

```mermaid
flowchart LR
    A[Source] --> B[Document]
    B --> C[Observation]
    C --> D[Verification]
    D --> E[Fact]
    E --> F[Event]
    E --> G[Intelligence]
    F --> G
```

## Project Relationships

```mermaid
erDiagram
    AIRPORT ||--o{ PROJECT : has
    RUNWAY ||--o{ PROJECT : may_affect
    PROJECT ||--o{ EVENT : produces
    PROJECT ||--o{ DOCUMENT_LINK : supported_by
    DOCUMENT ||--o{ DOCUMENT_LINK : linked_to

    PROJECT {
        uuid id
        uuid airport_id
        uuid runway_id
        string title
        string project_type
        string status
        integer planning_year
        integer procurement_year
        decimal estimated_total_value_usd
        decimal estimated_emas_value_usd
    }

    EVENT {
        uuid id
        uuid project_id
        date event_date
        string event_type
        string title
        string description
    }

    DOCUMENT_LINK {
        uuid id
        uuid document_id
        string entity_type
        uuid entity_id
    }
```

## Initial Build Scope

Sprint 1 will implement only:

- Airport
- Runway
- Runway End
- EMAS Bed
- Project
- Source
- Document

Observation, Fact, Verification and Intelligence will be designed later, after the basic CRUD and import workflow work correctly.

## Design Rule

Design enough to start building. Then build.