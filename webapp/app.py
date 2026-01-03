import json
import networkx as nx
from flask import Flask, render_template
from pyvis.network import Network
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
GRAPH_FILE = TEMPLATES_DIR / "graph.html"
app = Flask(__name__)

DATA_FILE = Path("reports/dependency_analysis.json")


def build_graph():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        deps = json.load(f)

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

    return G

def risk_color(risk):
    if risk >= 100:
        return "#dc2626"  # red
    elif risk >= 50:
        return "#f59e0b"  # amber
    elif risk >= 20:
        return "#facc15"  # yellow
    else:
        return "#22c55e"  # green


@app.route("/")
def index():
    G = build_graph()

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
    "barnesHut": {
      "gravitationalConstant": -30000,
      "centralGravity": 0.1,
      "springLength": 200,
      "springConstant": 0.02,
      "damping": 0.6,
      "avoidOverlap": 1
    },
    "stabilization": {
      "enabled": true,
      "iterations": 1000,
      "updateInterval": 25
    }
  },
  "interaction": {
    "hover": true,
    "tooltipDelay": 100
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

    graph_path = TEMPLATES_DIR / "graph.html"
    net.save_graph(str(graph_path))

    return render_template("graph.html")
    

if __name__ == "__main__":
    app.run(debug=True)
