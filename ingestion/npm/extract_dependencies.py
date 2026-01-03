import sys
import json
from pathlib import Path

LOCK_FILE = Path("target_project/package-lock.json")


def extract_dependencies():
    with open(LOCK_FILE, "r", encoding="utf-8") as f:
        lock_data = json.load(f)

    packages = lock_data.get("packages", {})

    visited = set()
    dependency_map = {}

    def walk(package_key, depth, parent_id=None):
        if package_key not in packages:
            return

        pkg = packages[package_key]

        if package_key != "":
            name = package_key.replace("node_modules/", "")
            version = pkg.get("version", "unknown")
            pkg_id = f"{name}@{version}"

            if pkg_id not in dependency_map:
                dependency_map[pkg_id] = {
                    "name": name,
                    "version": version,
                    "depth": depth,
                    "direct": depth == 1,
                    "dependencies": []
                }

            if parent_id:
                dependency_map[parent_id]["dependencies"].append(pkg_id)

            if pkg_id in visited:
                return

            visited.add(pkg_id)

        dependencies = pkg.get("dependencies", {})
        for dep_name in dependencies:
            dep_key = f"node_modules/{dep_name}"
            current_id = None if package_key == "" else pkg_id
            walk(dep_key, depth + 1, current_id)

    walk("", 0)
    return dependency_map

def compute_fanout(dependency_map):
    # Initialize fanout
    for pkg in dependency_map.values():
        pkg["fanout"] = 0

    # Count incoming edges
    for pkg in dependency_map.values():
        for dep in pkg["dependencies"]:
            if dep in dependency_map:
                dependency_map[dep]["fanout"] += 1

def compute_risk_scores(dependency_map):
    for pkg in dependency_map.values():
        depth = pkg["depth"]
        fanout = pkg["fanout"]
        pkg["risk_score"] = (depth * 1.5) + fanout

def build_reverse_dependencies(dependency_map):
    reverse_map = {pkg_id: [] for pkg_id in dependency_map}

    for parent_id, data in dependency_map.items():
        for child_id in data["dependencies"]:
            if child_id in reverse_map:
                reverse_map[child_id].append(parent_id)

    return reverse_map

def simulate_compromise(target_pkg, dependency_map):
    reverse_map = build_reverse_dependencies(dependency_map)

    impacted = set()
    stack = [target_pkg]

    while stack:
        current = stack.pop()
        for parent in reverse_map.get(current, []):
            if parent not in impacted:
                impacted.add(parent)
                stack.append(parent)

    return impacted

def export_to_json(dependency_map, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dependency_map, f, indent=2)

if __name__ == "__main__":
    deps = extract_dependencies()
    compute_fanout(deps)
    compute_risk_scores(deps)

    if len(sys.argv) > 1:
        compromised = sys.argv[1]
    else:
        compromised = "@types/geojson@7946.0.16"


    impacted = simulate_compromise(compromised, deps)

    print("\n[!] COMPROMISE SIMULATION")
    print(f"Compromised package: {compromised}")
    print(f"Total impacted packages: {len(impacted)}")

    print("\nDirectly impacted packages:")
    for pkg in list(impacted)[:10]:
        print(f"- {pkg}")
    
    export_to_json(deps, "reports/dependency_analysis.json")
    print("\nAnalysis exported to reports/dependency_analysis.json")

