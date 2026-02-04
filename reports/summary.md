# Supply Chain Risk Summary

## Overview

This report summarizes third-party supply-chain risks identified
in the Smart City Reporter application.

## Key Metrics

- Total dependencies analyzed: 421
- Direct dependencies: ~25
- Transitive dependencies: ~396

## Highest-Risk Dependencies

1. @types/geojson@7946.0.16

   - Depth: 5
   - Fan-out: 115
   - Structural Risk Score: 122.5

2. @turf/helpers@7.2.0

   - Depth: 4
   - Fan-out: 113
   - Structural Risk Score: 119.0

3. tslib@2.8.1
   - Depth: 5
   - Fan-out: 105
   - Structural Risk Score: 112.5

## Key Observation

High-risk dependencies are typically deeply nested utility or
type-definition packages with high fan-out, making them difficult
to audit but capable of large blast radius if compromised.
