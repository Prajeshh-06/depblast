# Supply Chain Compromise Simulation Report

## Target Project

Smart City Reporter (Node.js application)

## Objective

To evaluate the impact of a compromised third-party dependency
within the software supply chain.

## Compromised Dependency

- Package: @types/geojson
- Version: 7946.0.16
- Depth: 5
- Fan-out: 115
- Structural Risk Score: 122.5

## Impact Analysis

- Total impacted dependencies: 115
- Nature of impact:
  A compromise at this dependency propagates upstream through
  multiple transitive relationships, affecting both direct and
  indirect dependencies, including core geospatial processing
  libraries used by the application.

## Directly Impacted Dependencies

- @turf/turf@7.2.0
- @turf/rewind@7.2.0
- @turf/centroid@7.2.0
- geojson-equality-ts@1.0.2
- (and others)

## Security Implications

This simulation demonstrates that high-impact supply-chain risks
often originate from deeply nested, low-visibility dependencies.
Despite being non-executable type-definition packages, such
components form critical trust anchors within the dependency
graph.

A compromise at this level would bypass conventional perimeter
security controls and directly affect trusted application logic.

## Conclusion

The analysis confirms that dependency depth and fan-out are strong
indicators of supply-chain exposure. By modeling dependency
relationships and simulating compromise scenarios, the system
provides actionable insight into third-party risk beyond simple
vulnerability scanning.
