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
        "hideEdgesOnZoom": false,
        "zoomView": true,
        "zoomSpeed": 1.1,
        "navigationButtons": false,
        "keyboard": false
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

    # Inject professional loading screen over pyvis defaults
    html = GRAPH_HTML.read_text(encoding="utf-8")

    custom_styles = """
    <style>
    html, body {
        margin: 0; padding: 0;
        overflow: hidden !important;
        height: 100%; width: 100%;
        background: #0f172a;
    }
    .card, .card-body {
        border: none !important;
        padding: 0 !important;
        margin: 0 !important;
        background: transparent !important;
    }
    #mynetwork {
        position: fixed !important;
        top: 0 !important; left: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        background-color: #0f172a !important;
        border: none !important;
        touch-action: auto !important;
    }

    #loadingBar {
        position: fixed !important;
        inset: 0 !important;
        width: 100% !important;
        height: 100% !important;
        background: #0f172a !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        z-index: 9999;
        transition: opacity 0.6s ease !important;
    }

    div.outerBorder {
        position: relative !important;
        top: auto !important;
        width: 420px !important;
        height: auto !important;
        background: rgba(15, 23, 42, 0.95) !important;
        border: 1px solid #1e3a5f !important;
        border-radius: 18px !important;
        box-shadow: 0 0 60px rgba(59, 130, 246, 0.15), 0 25px 50px rgba(0,0,0,0.5) !important;
        padding: 36px 32px 32px !important;
        overflow: hidden;
        filter: none !important;
    }

    div.outerBorder::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 2px;
        background: linear-gradient(90deg, transparent, #3b82f6, #22c55e, transparent);
        animation: scanline 2s ease-in-out infinite;
    }

    @keyframes scanline {
        0% { transform: translateX(-100%); }
        100% { transform: translateX(100%); }
    }

    .loading-inner-header {
        display: flex;
        align-items: center;
        gap: 14px;
        margin-bottom: 28px;
    }

    .circle-loader {
        width: 44px; height: 44px;
        flex-shrink: 0;
        border-radius: 50%;
        border: 3px solid rgba(59,130,246,0.2);
        border-top-color: #3b82f6;
        border-right-color: #22c55e;
        animation: circle-spin 0.9s linear infinite;
    }
    @keyframes circle-spin { 100% { transform: rotate(360deg); } }

    .loading-title-wrap { flex: 1; }
    .loading-main-title {
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 0.9rem;
        font-weight: 600;
        color: #e2e8f0;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        margin: 0 0 4px;
    }
    .loading-subtitle {
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 0.75rem;
        color: #3b82f6;
        margin: 0;
    }

    #border {
        position: relative !important;
        top: auto !important; left: auto !important;
        width: 100% !important;
        height: 6px !important;
        border-radius: 999px !important;
        border: none !important;
        background: rgba(30, 41, 59, 0.8) !important;
        box-shadow: none !important;
        overflow: hidden;
        margin-top: 0 !important;
    }

    #bar {
        position: absolute !important;
        top: 0 !important; left: 0 !important;
        height: 100% !important;
        border-radius: 999px !important;
        border: none !important;
        background: linear-gradient(90deg, #2563eb, #22c55e) !important;
        box-shadow: 0 0 10px rgba(59,130,246,0.6) !important;
        transition: width 0.3s ease !important;
        min-width: 4px !important;
    }

    #text {
        position: relative !important;
        top: auto !important; left: auto !important;
        width: 100% !important;
        height: auto !important;
        font-family: 'SF Mono', 'Fira Code', monospace !important;
        font-size: 0.72rem !important;
        color: #3b82f6 !important;
        text-align: right;
        margin: 8px 0 0 !important;
        font-weight: 600;
    }

    .progress-label {
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 0.72rem;
        color: #475569;
        margin: 8px 0 0;
    }
    </style>
    """

    custom_html_injection = """
    <script>
    // Replace the default outerBorder content with styled version
    document.addEventListener('DOMContentLoaded', function() {
        var outer = document.querySelector('div.outerBorder');
        if (!outer) return;

        var header = document.createElement('div');
        header.className = 'loading-inner-header';
        header.innerHTML = `
            <div class="circle-loader"></div>
            <div class="loading-title-wrap">
                <p class="loading-main-title">Building Graph</p>
                <p class="loading-subtitle" id="physicsStatus">Simulating physics...</p>
            </div>
        `;
        outer.insertBefore(header, outer.firstChild);

        var label = document.createElement('div');
        label.className = 'progress-label';
        label.textContent = 'Stabilizing node positions';
        outer.appendChild(label);

    });

    // Prevent page scroll.
    document.body.style.overflow = 'hidden';

    // The #loadingBar overlay (z-index:9999) blocks all mouse events including
    // wheel-to-zoom. As soon as stabilization finishes, disable pointer-events
    // on it so the canvas underneath can receive wheel events immediately,
    // even during the 500ms fade-out transition.
    document.addEventListener('DOMContentLoaded', function() {
        var checkNetwork = setInterval(function() {
            if (typeof network !== 'undefined') {
                clearInterval(checkNetwork);
                network.once('stabilizationIterationsDone', function() {
                    var bar = document.getElementById('loadingBar');
                    if (bar) { bar.style.pointerEvents = 'none'; }
                });
            }
        }, 100);
    });
    </script>
    """

    html = html.replace("</style>", custom_styles + "</style>", 1)
    html = html.replace("</body>", custom_html_injection + "</body>")
    GRAPH_HTML.write_text(html, encoding="utf-8")


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
