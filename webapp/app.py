import json
import sys
import networkx as nx
from flask import Flask, render_template, request, jsonify
from pyvis.network import Network
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
TEMPLATES_DIR = BASE_DIR / "templates"
GRAPH_FILE = TEMPLATES_DIR / "graph.html"

# Add ingestion module to path and import existing functions
sys.path.insert(0, str(PROJECT_ROOT / "ingestion" / "npm"))
from extract_dependencies import (
    extract_dependencies,
    compute_fanout,
    compute_risk_scores,
    simulate_compromise
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Store current analysis in memory
current_analysis = {}


def risk_color(risk):
    if risk >= 100:
        return "#dc2626"  # red
    elif risk >= 50:
        return "#f59e0b"  # amber
    elif risk >= 20:
        return "#facc15"  # yellow
    else:
        return "#22c55e"  # green


def generate_graph(deps):
    """Generate graph from dependency data."""
    G = nx.DiGraph()

    for pkg_id, data in deps.items():
        G.add_node(
            pkg_id,
            depth=data["depth"],
            fanout=data["fanout"],
            risk=data["risk_score"]
        )
        for child in data["dependencies"]:
            G.add_edge(pkg_id, child)

    net = Network(
        height="750px",
        width="100%",
        bgcolor="#0f172a",
        font_color="white",
        directed=True
    )
    net.set_options("""
var options = {
  "physics": {
    "enabled": true,
    "solver": "forceAtlas2Based",
    "forceAtlas2Based": {
      "gravitationalConstant": -100,
      "centralGravity": 0.005,
      "springLength": 250,
      "springConstant": 0.02,
      "damping": 0.4,
      "avoidOverlap": 1
    },
    "stabilization": {
      "enabled": true,
      "iterations": 2000,
      "updateInterval": 25,
      "fit": true
    },
    "minVelocity": 0.75
  },
  "interaction": {
    "hover": true,
    "tooltipDelay": 100,
    "hideEdgesOnDrag": true,
    "hideEdgesOnZoom": true
  },
  "nodes": {
    "scaling": {
      "min": 10,
      "max": 50
    }
  }
}
""")

    for node, attrs in G.nodes(data=True):
        net.add_node(
            node,
            label=node,
            size=10 + attrs["fanout"],
            color=risk_color(attrs["risk"]),
            title=(
                f"Package: {node}<br>"
                f"Depth: {attrs['depth']}<br>"
                f"Fan-out: {attrs['fanout']}<br>"
                f"Risk Score: {attrs['risk']:.1f}"
            )
        )

    for src, dst in G.edges():
        net.add_edge(src, dst)

    net.save_graph(str(GRAPH_FILE))


@app.route("/")
def index():
    """Landing page with file upload."""
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    """Handle file upload and run analysis."""
    global current_analysis

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        content = file.read().decode("utf-8")
        lock_data = json.loads(content)

        if "packages" not in lock_data:
            return jsonify({"error": "Invalid package-lock.json"}), 400

        # Save temporarily for extract_dependencies to use
        import extract_dependencies as ext_mod
        temp_path = PROJECT_ROOT / "temp_lock.json"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(lock_data, f)

        # Temporarily change LOCK_FILE and run existing functions
        original_lock = ext_mod.LOCK_FILE
        ext_mod.LOCK_FILE = temp_path

        deps = extract_dependencies()
        compute_fanout(deps)
        compute_risk_scores(deps)

        ext_mod.LOCK_FILE = original_lock
        temp_path.unlink()

        current_analysis = deps

        # Generate graph
        generate_graph(deps)

        # Stats
        total_deps = len(deps)
        direct_deps = sum(1 for d in deps.values() if d["direct"])

        sorted_by_risk = sorted(deps.items(), key=lambda x: x[1]["risk_score"], reverse=True)[:10]
        top_risks = [
            {"name": pkg_id, "risk_score": round(data["risk_score"], 1)}
            for pkg_id, data in sorted_by_risk
        ]

        return jsonify({
            "success": True,
            "stats": {"total": total_deps, "direct": direct_deps},
            "top_risks": top_risks
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/graph")
def graph():
    """Display the generated graph."""
    return render_template("graph.html")


@app.route("/simulate", methods=["POST"])
def simulate_route():
    """Simulate package compromise using existing function."""
    global current_analysis

    data = request.get_json()
    target_pkg = data.get("package")

    if not current_analysis:
        return jsonify({"error": "No analysis data. Upload a file first."}), 400

    if target_pkg not in current_analysis:
        return jsonify({"error": f"Package '{target_pkg}' not found"}), 404

    impacted = simulate_compromise(target_pkg, current_analysis)

    return jsonify({
        "target": target_pkg,
        "impacted_count": len(impacted),
        "impacted_packages": list(impacted)[:20]
    })


if __name__ == "__main__":
    app.run(debug=True)
