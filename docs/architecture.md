# Architecture Overview

This project analyzes software supply-chain risks by examining
third-party dependencies used in a Node.js application.

High-level workflow:

1. Ingest dependency metadata from npm artifacts
2. Analyze dependency relationships and structure
3. Assess risk propagation through dependency chains
4. Generate actionable reports

Each stage is isolated to allow extension to additional ecosystems
in the future.
