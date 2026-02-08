import json
import sys
from pathlib import Path

import networkx as nx
from flask import Flask, render_template, request, jsonify
from pyvis.network import Network

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
TEMPLATE_DIR = BASE_DIR / "templates"
GRAPH_HTML = TEMPLATE_DIR / "graph.html"

sys.path.insert(0, str(ROOT_DIR / "ingestion" / "npm"))

from extract_dependencies import (
    extract_dependencies,
    compute_fanout,
    compute_risk_scores,
    simulate_compromise
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

analysis_cache = {}


def map_risk_to_color(score):
    if score >= 100:
        return "#dc2626"
    elif score >= 50:
        return "#f59e0b"
    elif score >= 20:
        return "#facc15"
    return "#22c55e"


def build_dependency_graph(dependency_map):
    graph = nx.DiGraph()

    for pkg, meta in dependency_map.items():
        graph.add_node(
            pkg,
            depth=meta["depth"],
            fanout=meta["fanout"],
            risk=meta["risk_score"]
        )
        for child in meta["dependencies"]:
            graph.add_edge(pkg, child)

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

    for node, attrs in graph.nodes(data=True):
        net.add_node(
            node,
            label=node,
            size=10 + attrs["fanout"],
            color=map_risk_to_color(attrs["risk"]),
            title=(
                f"Package: {node}<br>"
                f"Depth: {attrs['depth']}<br>"
                f"Fan-out: {attrs['fanout']}<br>"
                f"Risk Score: {attrs['risk']:.1f}"
            )
        )

    for src, dst in graph.edges():
        net.add_edge(src, dst)

    net.save_graph(str(GRAPH_HTML))


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze_dependencies():
    global analysis_cache

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    try:
        raw_data = uploaded_file.read().decode("utf-8")
        lock_json = json.loads(raw_data)

        if "packages" not in lock_json:
            return jsonify({"error": "Invalid package-lock.json"}), 400

        import extract_dependencies as extractor
        temp_lock = ROOT_DIR / "temp_lock.json"

        with open(temp_lock, "w", encoding="utf-8") as f:
            json.dump(lock_json, f)

        original_lock_file = extractor.LOCK_FILE
        extractor.LOCK_FILE = temp_lock

        deps = extract_dependencies()
        compute_fanout(deps)
        compute_risk_scores(deps)

        extractor.LOCK_FILE = original_lock_file
        temp_lock.unlink()

        analysis_cache = deps

        build_dependency_graph(deps)

        total_count = len(deps)
        direct_count = sum(1 for d in deps.values() if d["direct"])

        top_risk = sorted(
            deps.items(),
            key=lambda x: x[1]["risk_score"],
            reverse=True
        )[:10]

        return jsonify({
            "success": True,
            "stats": {
                "total": total_count,
                "direct": direct_count
            },
            "top_risks": [
                {"name": pkg, "risk_score": round(meta["risk_score"], 1)}
                for pkg, meta in top_risk
            ]
        })

    except Exception as err:
        return jsonify({"error": str(err)}), 500


@app.route("/graph")
def show_graph():
    return render_template("graph.html")


@app.route("/simulate", methods=["POST"])
def simulate_attack():
    if not analysis_cache:
        return jsonify({"error": "No analysis data available"}), 400

    payload = request.get_json()
    target = payload.get("package")

    if target not in analysis_cache:
        return jsonify({"error": f"Package '{target}' not found"}), 404

    affected = simulate_compromise(target, analysis_cache)

    return jsonify({
        "target": target,
        "impacted_count": len(affected),
        "impacted_packages": list(affected)[:20]
    })


if __name__ == "__main__":
    app.run(debug=True)
