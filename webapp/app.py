import json
import sys
import tempfile
import networkx as nx
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from pyvis.network import Network

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
TEMPLATES_DIR = BASE_DIR / "templates"
GRAPH_FILE = TEMPLATES_DIR / "graph.html"

sys.path.insert(0, str(PROJECT_ROOT / "ingestion" / "npm"))
from extract_dependencies import (
    extract_dependencies,
    compute_fanout,
    compute_risk_scores,
    simulate_compromise
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

analysis_cache = {}

def risk_color(score: float) -> str:
    if score >= 100:
        return "#dc2626"
    if score >= 50:
        return "#f59e0b"
    if score >= 20:
        return "#facc15"
    return "#22c55e"

def generate_graph(dependencies: dict) -> None:
    graph = nx.DiGraph()

    for pkg, data in dependencies.items():
        graph.add_node(
            pkg,
            depth=data["depth"],
            fanout=data["fanout"],
            risk=data["risk_score"]
        )
        for dep in data["dependencies"]:
            graph.add_edge(pkg, dep)

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
        "solver": "forceAtlas2Based"
      },
      "interaction": {
        "hover": true,
        "tooltipDelay": 100
      }
    }
    """)

    for node, attrs in graph.nodes(data=True):
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

    for src, dst in graph.edges():
        net.add_edge(src, dst)

    net.save_graph(str(GRAPH_FILE))

def run_analysis(lock_json: dict) -> dict:
    import extract_dependencies as ext_mod

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tmp:
        json.dump(lock_json, tmp)
        temp_path = Path(tmp.name)

    original_lock = ext_mod.LOCK_FILE
    ext_mod.LOCK_FILE = temp_path

    try:
        deps = extract_dependencies()
        compute_fanout(deps)
        compute_risk_scores(deps)
    finally:
        ext_mod.LOCK_FILE = original_lock
        temp_path.unlink(missing_ok=True)

    return deps

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analyze", methods=["POST"])
def analyze():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return jsonify({"error": "No file selected"}), 400

    try:
        lock_data = json.loads(uploaded_file.read().decode("utf-8"))

        if "packages" not in lock_data:
            return jsonify({"error": "Invalid package-lock.json"}), 400

        deps = run_analysis(lock_data)
        analysis_cache.clear()
        analysis_cache.update(deps)

        generate_graph(deps)

        total = len(deps)
        direct = sum(1 for d in deps.values() if d["direct"])

        top_risks = sorted(
            deps.items(),
            key=lambda x: x[1]["risk_score"],
            reverse=True
        )[:10]

        return jsonify({
            "success": True,
            "stats": {"total": total, "direct": direct},
            "top_risks": [
                {"name": name, "risk_score": round(data["risk_score"], 1)}
                for name, data in top_risks
            ]
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.route("/graph")
def graph():
    return render_template("graph.html")

@app.route("/simulate", methods=["POST"])
def simulate():
    if not analysis_cache:
        return jsonify({"error": "Run analysis first"}), 400

    data = request.get_json()
    target = data.get("package")

    if target not in analysis_cache:
        return jsonify({"error": f"Package '{target}' not found"}), 404

    impacted = simulate_compromise(target, analysis_cache)

    return jsonify({
        "target": target,
        "impacted_count": len(impacted),
        "impacted_packages": list(impacted)[:20]
    })

if __name__ == "__main__":
    app.run(debug=True)
