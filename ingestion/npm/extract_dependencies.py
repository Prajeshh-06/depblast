import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

LOCK_FILE = Path("target_project/package-lock.json")
NPM_REGISTRY = "https://registry.npmjs.org"
REQUEST_TIMEOUT = 4   # seconds per package lookup
NPM_WORKERS    = 20   # concurrent threads for registry fetching
BLAST_RADIUS_LIMIT = 150  # only pre-compute blast radius for top-N packages by fan-in


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_npm_metadata(package_name: str) -> dict:
    """Query the NPM registry for age / maintainer info.
    Returns an empty dict on any failure so the caller can gracefully degrade.
    Designed to be called concurrently from a thread pool."""
    try:
        safe_name = urllib.parse.quote(package_name, safe="@%")
        url = f"{NPM_REGISTRY}/{safe_name}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        time_block = data.get("time", {})
        modified_str = time_block.get("modified", "")
        created_str = time_block.get("created", "")

        days_since_publish = None
        package_age_days = None

        if modified_str:
            from datetime import datetime, timezone
            try:
                modified_dt = datetime.fromisoformat(modified_str.replace("Z", "+00:00"))
                days_since_publish = (datetime.now(timezone.utc) - modified_dt).days
            except Exception:
                pass

        if created_str:
            from datetime import datetime, timezone
            try:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                package_age_days = (datetime.now(timezone.utc) - created_dt).days
            except Exception:
                pass

        maintainers = data.get("maintainers", [])
        maintainer_count = len(maintainers) if maintainers else 1

        latest_version = data.get("dist-tags", {}).get("latest", "")
        downloads_hint = len(data.get("versions", {}))  # proxy for popularity

        return {
            "days_since_publish": days_since_publish,
            "package_age_days": package_age_days,
            "maintainer_count": maintainer_count,
            "latest_version": latest_version,
            "version_count": downloads_hint,
        }

    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Core dependency extraction
# ---------------------------------------------------------------------------

def extract_dependencies(enrich_npm: bool = False) -> dict:
    """Parse package-lock.json v3 and return a rich dependency map.

    Parameters
    ----------
    enrich_npm : bool
        When True, query the NPM registry for each unique package name to
        enrich the risk model with age & maintainer data.
    """
    with open(LOCK_FILE, "r", encoding="utf-8") as f:
        lock_data = json.load(f)

    packages = lock_data.get("packages", {})
    root_pkg = packages.get("", {})
    root_dev_deps = set(root_pkg.get("devDependencies", {}).keys())
    root_prod_deps = set(root_pkg.get("dependencies", {}).keys())

    visited: set = set()
    dependency_map: dict = {}

    def walk(package_key: str, depth: int, parent_id=None, inherited_is_dev: bool = False):
        if package_key not in packages:
            return

        pkg = packages[package_key]

        if package_key != "":
            name = package_key.replace("node_modules/", "")
            # Handle nested node_modules paths like node_modules/a/node_modules/b
            if "/" in name and not name.startswith("@"):
                name = name.split("/")[-1]
            elif name.startswith("@"):
                parts = name.split("/")
                if len(parts) > 2:
                    name = "/".join(parts[-2:])

            version = pkg.get("version", "unknown")
            pkg_id = f"{name}@{version}"

            # Determine if this is a dev dependency
            is_dev = pkg.get("dev", False) or inherited_is_dev
            # Override: if root marks it as dev explicitly
            top_name = package_key.split("node_modules/")[-1].split("/")[0]
            if top_name in root_dev_deps:
                is_dev = True
            elif top_name in root_prod_deps:
                is_dev = False

            if pkg_id not in dependency_map:
                dependency_map[pkg_id] = {
                    "name": name,
                    "version": version,
                    "depth": depth,
                    "direct": depth == 1,
                    "is_dev": is_dev,
                    "dependencies": [],
                    "fanout": 0,          # will be computed later (reverse-dep count)
                    "blast_radius": 0,    # will be computed later
                    "is_chokepoint": False,
                    # NPM enrichment placeholders
                    "days_since_publish": None,
                    "package_age_days": None,
                    "maintainer_count": None,
                    "latest_version": None,
                    "version_count": None,
                    "risk_score": 0.0,
                    "risk_level": "unknown",
                }

            if parent_id:
                if pkg_id not in dependency_map[parent_id]["dependencies"]:
                    dependency_map[parent_id]["dependencies"].append(pkg_id)

            if pkg_id in visited:
                return
            visited.add(pkg_id)

        child_dev_flag = pkg.get("dev", False) or inherited_is_dev
        for dep_name, _dep_version in pkg.get("dependencies", {}).items():
            dep_key = f"node_modules/{dep_name}"
            current_id = None if package_key == "" else pkg_id
            walk(dep_key, depth + 1, current_id, child_dev_flag)

    walk("", 0)

    # NPM Enrichment (optional — network calls)
    if enrich_npm:
        _enrich_with_npm_data(dependency_map)

    return dependency_map


def _enrich_with_npm_data(dependency_map: dict) -> None:
    """Mutates dependency_map in-place, concurrently fetching NPM registry data."""
    unique_names = list({meta["name"] for meta in dependency_map.values()})
    total = len(unique_names)
    print(f"[DepBlast] Fetching NPM metadata for {total} packages ({NPM_WORKERS} threads)…", flush=True)

    npm_cache: dict = {}
    completed = 0

    with ThreadPoolExecutor(max_workers=NPM_WORKERS) as pool:
        future_to_name = {pool.submit(_fetch_npm_metadata, name): name for name in unique_names}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                npm_cache[name] = future.result()
            except Exception:
                npm_cache[name] = {}
            completed += 1
            if completed % 50 == 0:
                print(f"  … {completed}/{total} fetched", flush=True)

    print(f"  … {total}/{total} fetched ✓", flush=True)

    for pkg_id, meta in dependency_map.items():
        npm = npm_cache.get(meta["name"], {})
        meta["days_since_publish"] = npm.get("days_since_publish")
        meta["package_age_days"] = npm.get("package_age_days")
        meta["maintainer_count"] = npm.get("maintainer_count")
        meta["latest_version"] = npm.get("latest_version")
        meta["version_count"] = npm.get("version_count")


# ---------------------------------------------------------------------------
# Graph metrics
# ---------------------------------------------------------------------------

def compute_fanout(dependency_map: dict) -> None:
    """Fan-out = number of packages that directly depend on this one (fan-in metric)."""
    for pkg in dependency_map.values():
        pkg["fanout"] = 0

    for pkg in dependency_map.values():
        for dep_id in pkg["dependencies"]:
            if dep_id in dependency_map:
                dependency_map[dep_id]["fanout"] += 1


def build_reverse_dependencies(dependency_map: dict) -> dict:
    reverse_map = {pkg_id: [] for pkg_id in dependency_map}
    for parent_id, data in dependency_map.items():
        for child_id in data["dependencies"]:
            if child_id in reverse_map:
                reverse_map[child_id].append(parent_id)
    return reverse_map


def compute_blast_radii(dependency_map: dict) -> None:
    """Pre-compute blast radius for the most critical packages only.

    Full O(n²) computation is too slow for large lockfiles (500+ packages).
    We compute exact blast radius for the top BLAST_RADIUS_LIMIT packages
    by fan-in score. All others get an estimated value based on depth + fan-in.
    """
    reverse_map = build_reverse_dependencies(dependency_map)

    # Sort by fan-in descending — high fan-in = most likely chokepoints
    by_fanin = sorted(dependency_map.keys(), key=lambda k: dependency_map[k]["fanout"], reverse=True)
    exact_set = set(by_fanin[:BLAST_RADIUS_LIMIT])

    for pkg_id in dependency_map:
        if pkg_id in exact_set:
            affected = simulate_compromise(pkg_id, dependency_map, _reverse_map=reverse_map)
            dependency_map[pkg_id]["blast_radius"] = len(affected)
        else:
            # Fast estimate: fanout + depth proxy (no BFS needed)
            meta = dependency_map[pkg_id]
            dependency_map[pkg_id]["blast_radius"] = meta["fanout"]


def detect_chokepoints(dependency_map: dict,
                       fanin_threshold: int = 5,
                       depth_threshold: int = 2) -> None:
    """Mark packages as chokepoints if they have high fan-in and are transitive."""
    for meta in dependency_map.values():
        meta["is_chokepoint"] = (
            meta["fanout"] >= fanin_threshold and
            meta["depth"] >= depth_threshold
        )


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

def compute_risk_scores(dependency_map: dict) -> None:
    """
    Risk formula:
        Base   = (depth × 1.5) + (fan_in × 3.0) + (blast_radius × 0.5)
        Age    += (days_since_publish / 730) × 5.0   [stale = risky]
        Bus    += (1 / maintainer_count) × 10.0      [solo maintainer = risky]
        Scope  += 2.0 if package name is unscoped     [unscoped historically hijacked more]
        Multiplier = 2.0 if production dep, 1.0 if dev
    """
    for meta in dependency_map.values():
        base = (meta["depth"] * 1.5) + (meta["fanout"] * 3.0) + (meta["blast_radius"] * 0.5)

        # Staleness risk
        age_risk = 0.0
        if meta.get("days_since_publish") is not None:
            age_risk = (meta["days_since_publish"] / 730) * 5.0

        # Bus-factor risk
        bus_risk = 0.0
        mc = meta.get("maintainer_count")
        if mc and mc > 0:
            bus_risk = (1 / mc) * 10.0

        # Scope risk (unscoped packages more often typosquatted / hijacked)
        scope_risk = 0.0 if meta["name"].startswith("@") else 2.0

        # Prod vs dev multiplier
        prod_mult = 1.0 if meta["is_dev"] else 2.0

        raw_score = (base + age_risk + bus_risk + scope_risk) * prod_mult
        meta["risk_score"] = round(raw_score, 2)

        # Classify
        if raw_score >= 120:
            meta["risk_level"] = "critical"
        elif raw_score >= 60:
            meta["risk_level"] = "high"
        elif raw_score >= 25:
            meta["risk_level"] = "medium"
        else:
            meta["risk_level"] = "low"


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate_compromise(target_pkg: str, dependency_map: dict,
                        _reverse_map: dict = None) -> set:
    """BFS upward through the reverse-dependency graph from target_pkg.
    Returns the set of all packages that would be transitively impacted."""
    if _reverse_map is None:
        _reverse_map = build_reverse_dependencies(dependency_map)

    impacted: set = set()
    stack = [target_pkg]
    while stack:
        current = stack.pop()
        for parent in _reverse_map.get(current, []):
            if parent not in impacted:
                impacted.add(parent)
                stack.append(parent)
    return impacted


# ---------------------------------------------------------------------------
# Structural health
# ---------------------------------------------------------------------------

def compute_structural_health(dependency_map: dict) -> dict:
    """Compute aggregate metrics for the structural health panel."""
    if not dependency_map:
        return {}

    depths = [m["depth"] for m in dependency_map.values()]
    blast_radii = [m["blast_radius"] for m in dependency_map.values()]
    risk_levels = [m["risk_level"] for m in dependency_map.values()]
    chokepoints = [pkg_id for pkg_id, m in dependency_map.items() if m["is_chokepoint"]]
    solo_maintainer_pkgs = [
        m["name"] for m in dependency_map.values()
        if m.get("maintainer_count") == 1 and not m["is_dev"]
    ]

    return {
        "total": len(dependency_map),
        "direct": sum(1 for m in dependency_map.values() if m["direct"]),
        "transitive": sum(1 for m in dependency_map.values() if not m["direct"]),
        "dev_deps": sum(1 for m in dependency_map.values() if m["is_dev"]),
        "prod_deps": sum(1 for m in dependency_map.values() if not m["is_dev"]),
        "max_depth": max(depths) if depths else 0,
        "avg_depth": round(sum(depths) / len(depths), 1) if depths else 0,
        "max_blast_radius": max(blast_radii) if blast_radii else 0,
        "chokepoint_count": len(chokepoints),
        "chokepoints": chokepoints[:20],
        "solo_maintainer_count": len(solo_maintainer_pkgs),
        "solo_maintainer_pkgs": solo_maintainer_pkgs[:10],
        "risk_distribution": {
            "critical": risk_levels.count("critical"),
            "high": risk_levels.count("high"),
            "medium": risk_levels.count("medium"),
            "low": risk_levels.count("low"),
        }
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_to_json(dependency_map: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dependency_map, f, indent=2)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import urllib.parse

    enrich = "--enrich" in sys.argv
    deps = extract_dependencies(enrich_npm=enrich)
    compute_fanout(deps)
    compute_blast_radii(deps)
    detect_chokepoints(deps)
    compute_risk_scores(deps)

    health = compute_structural_health(deps)

    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        compromised = sys.argv[1]
    else:
        compromised = next(iter(deps))  # default: first package

    impacted = simulate_compromise(compromised, deps)

    print("\n╔══════════════════════════════════════════╗")
    print("║       DepBlast — Compromise Simulation   ║")
    print("╚══════════════════════════════════════════╝")
    print(f"\n  Target  : {compromised}")
    print(f"  Impacted: {len(impacted)} packages")
    print(f"\nStructural Health:")
    for k, v in health.items():
        if not isinstance(v, (dict, list)):
            print(f"  {k}: {v}")

    export_to_json(deps, "reports/dependency_analysis.json")
    print("\nExported → reports/dependency_analysis.json")
