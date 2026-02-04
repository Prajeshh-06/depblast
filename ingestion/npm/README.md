# NPM Dependency Ingestion

This module is responsible for extracting dependency information
from Node.js projects using npm metadata.

The primary data source is `package-lock.json`, which provides:

- Exact dependency versions
- Transitive dependency relationships
- Deterministic dependency trees

At this stage, this directory defines the boundary for all
npm-specific ingestion logic.
