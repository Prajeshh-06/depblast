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
    compute_blast_radii,
    detect_chokepoints,
    compute_risk_scores,
    compute_structural_health,
    simulate_compromise,
    build_reverse_dependencies,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024

# In-memory cache for current analysis session
analysis_cache: dict = {}
health_cache: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def risk_level_to_color(level: str) -> str:
    return {
        "critical": "#ef4444",
        "high":     "#f97316",
        "medium":   "#f59e0b",
        "low":      "#22c55e",
        "unknown":  "#64748b",
    }.get(level, "#64748b")


def build_dependency_graph(dependency_map: dict) -> None:
    """Build and save the interactive PyVis graph with click-to-simulate support."""
    graph = nx.DiGraph()

    for pkg, meta in dependency_map.items():
        graph.add_node(
            pkg,
            depth=meta["depth"],
            fanout=meta["fanout"],
            blast=meta["blast_radius"],
            risk=meta["risk_score"],
            level=meta["risk_level"],
            is_dev=meta["is_dev"],
            chokepoint=meta["is_chokepoint"],
        )
        for child in meta["dependencies"]:
            graph.add_edge(pkg, child)

    net = Network(
        height="100vh",
        width="100%",
        bgcolor="#060d1a",
        font_color="#94a3b8",
        directed=True,
    )

    net.set_options("""
    var options = {
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {
          "gravitationalConstant": -120,
          "centralGravity": 0.006,
          "springLength": 280,
          "springConstant": 0.025,
          "damping": 0.42,
          "avoidOverlap": 1
        },
        "stabilization": { "enabled": true, "iterations": 2000, "updateInterval": 25, "fit": true },
        "minVelocity": 0.75
      },
      "interaction": {
        "hover": true,
        "tooltipDelay": 80,
        "hideEdgesOnDrag": true,
        "zoomView": true,
        "zoomSpeed": 1.1
      },
      "nodes": { "scaling": { "min": 8, "max": 55 } },
      "edges": {
        "color": { "color": "#1e3a5f", "highlight": "#3b82f6", "hover": "#60a5fa" },
        "smooth": { "type": "continuous" },
        "arrows": { "to": { "enabled": true, "scaleFactor": 0.5 } }
      }
    }
    """)

    for node, attrs in graph.nodes(data=True):
        color = risk_level_to_color(attrs.get("level", "unknown"))
        size = 8 + min(attrs.get("fanout", 0) * 2, 40) + (5 if attrs.get("chokepoint") else 0)
        border = "#fbbf24" if attrs.get("chokepoint") else color
        shape = "diamond" if attrs.get("chokepoint") else ("dot" if not attrs.get("is_dev") else "square")

        meta = dependency_map[node]
        maintainer_line = f"<br>Maintainers: {meta.get('maintainer_count', '?')}" if meta.get("maintainer_count") is not None else ""
        stale_line = f"<br>Last publish: {meta.get('days_since_publish', '?')} days ago" if meta.get("days_since_publish") is not None else ""

        net.add_node(
            node,
            label=meta["name"],
            size=size,
            color={"background": color, "border": border, "highlight": {"background": "#ffffff", "border": "#fbbf24"}},
            borderWidth=3 if attrs.get("chokepoint") else 1,
            shape=shape,
            title=(
                f"<div style='font-family:monospace;padding:8px;max-width:260px'>"
                f"<b style='color:#e2e8f0'>{node}</b><br>"
                f"<span style='color:{color}'>▲ {attrs.get('level','?').upper()}</span><br>"
                f"Risk Score: <b>{attrs.get('risk', 0):.1f}</b><br>"
                f"Depth: {attrs.get('depth', 0)} | Fan-in: {attrs.get('fanout', 0)}<br>"
                f"Blast Radius: <b>{attrs.get('blast', 0)}</b> pkgs"
                f"{maintainer_line}{stale_line}<br>"
                f"{'🎯 CHOKEPOINT' if attrs.get('chokepoint') else ''}"
                f"{'[DEV]' if attrs.get('is_dev') else '[PROD]'}"
                f"</div>"
            )
        )

    for src, dst in graph.edges():
        net.add_edge(src, dst, width=1)

    TEMPLATE_DIR.mkdir(exist_ok=True)
    net.save_graph(str(GRAPH_HTML))

    # Inject custom styles + click-to-simulate JS
    _inject_graph_enhancements()


def _inject_graph_enhancements():
    """Read the raw pyvis output and inject our custom UI layer."""
    html = GRAPH_HTML.read_text(encoding="utf-8")

    custom_css = """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; width: 100%; overflow: hidden; background: #060d1a; font-family: 'Inter', sans-serif; }
    .card, .card-body { border: none !important; padding: 0 !important; margin: 0 !important; background: transparent !important; }
    #mynetwork {
        position: fixed !important; inset: 0 !important;
        width: 100vw !important; height: 100vh !important;
        background: #060d1a !important; border: none !important;
    }

    /* ── Loading bar ── */
    #loadingBar {
        position: fixed !important; inset: 0 !important;
        width: 100% !important; height: 100% !important;
        background: #060d1a !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
        z-index: 9999; transition: opacity 0.6s ease !important;
    }
    div.outerBorder {
        position: relative !important; top: auto !important;
        width: 420px !important; height: auto !important;
        background: rgba(6,13,26,0.97) !important;
        border: 1px solid rgba(59,130,246,0.3) !important;
        border-radius: 18px !important;
        box-shadow: 0 0 60px rgba(59,130,246,0.12), 0 25px 50px rgba(0,0,0,0.6) !important;
        padding: 36px 32px 32px !important;
        overflow: hidden; filter: none !important;
    }
    div.outerBorder::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
        background: linear-gradient(90deg, transparent, #3b82f6, #22c55e, transparent);
        animation: scanline 2s ease-in-out infinite;
    }
    @keyframes scanline { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }

    .db-loading-header { display: flex; align-items: center; gap: 14px; margin-bottom: 28px; }
    .db-spinner {
        width: 44px; height: 44px; flex-shrink: 0; border-radius: 50%;
        border: 3px solid rgba(59,130,246,0.2);
        border-top-color: #3b82f6; border-right-color: #22c55e;
        animation: db-spin 0.9s linear infinite;
    }
    @keyframes db-spin { 100% { transform: rotate(360deg); } }
    .db-loading-title { font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; font-weight: 600; color: #e2e8f0; letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 4px; }
    .db-loading-sub { font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; color: #3b82f6; }

    #border { position: relative !important; top: auto !important; left: auto !important; width: 100% !important; height: 6px !important; border-radius: 999px !important; border: none !important; background: rgba(30,41,59,0.8) !important; box-shadow: none !important; overflow: hidden; margin-top: 0 !important; }
    #bar { position: absolute !important; top: 0 !important; left: 0 !important; height: 100% !important; border-radius: 999px !important; border: none !important; background: linear-gradient(90deg, #2563eb, #22c55e) !important; box-shadow: 0 0 10px rgba(59,130,246,0.5) !important; transition: width 0.3s ease !important; min-width: 4px !important; }
    #text { position: relative !important; top: auto !important; left: auto !important; width: 100% !important; height: auto !important; font-family: 'JetBrains Mono', monospace !important; font-size: 0.72rem !important; color: #3b82f6 !important; text-align: right; margin: 8px 0 0 !important; font-weight: 600; }

    /* ── Sidebar panel ── */
    #db-sidebar {
        position: fixed; top: 20px; right: 20px; bottom: 20px;
        width: 320px; z-index: 100;
        background: rgba(6, 13, 26, 0.93);
        border: 1px solid rgba(59,130,246,0.25);
        border-radius: 16px;
        box-shadow: 0 0 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03);
        display: none; flex-direction: column;
        overflow: hidden;
        backdrop-filter: blur(16px);
        animation: slideIn 0.25s ease;
    }
    @keyframes slideIn { from { opacity: 0; transform: translateX(20px); } to { opacity: 1; transform: translateX(0); } }
    #db-sidebar.open { display: flex; }

    .sb-header {
        padding: 18px 20px 14px;
        border-bottom: 1px solid rgba(255,255,255,0.06);
        display: flex; align-items: flex-start; justify-content: space-between; gap: 10px;
    }
    .sb-pkg-name { font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; color: #e2e8f0; font-weight: 600; word-break: break-all; line-height: 1.4; }
    .sb-version { font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; color: #64748b; margin-top: 2px; }
    .sb-close { background: none; border: none; color: #64748b; cursor: pointer; font-size: 1.1rem; padding: 2px; flex-shrink: 0; line-height: 1; transition: color 0.15s; }
    .sb-close:hover { color: #e2e8f0; }

    .sb-body { flex: 1; overflow-y: auto; padding: 16px 20px; display: flex; flex-direction: column; gap: 14px; }
    .sb-body::-webkit-scrollbar { width: 4px; }
    .sb-body::-webkit-scrollbar-track { background: transparent; }
    .sb-body::-webkit-scrollbar-thumb { background: rgba(59,130,246,0.3); border-radius: 2px; }

    .sb-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 6px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; font-family: 'JetBrains Mono', monospace; }
    .badge-critical { background: rgba(239,68,68,0.15); color: #ef4444; border: 1px solid rgba(239,68,68,0.3); }
    .badge-high     { background: rgba(249,115,22,0.15); color: #f97316; border: 1px solid rgba(249,115,22,0.3); }
    .badge-medium   { background: rgba(245,158,11,0.15); color: #f59e0b; border: 1px solid rgba(245,158,11,0.3); }
    .badge-low      { background: rgba(34,197,94,0.15);  color: #22c55e; border: 1px solid rgba(34,197,94,0.3); }

    .sb-metric-row { display: flex; flex-direction: column; gap: 8px; }
    .sb-metric { display: flex; justify-content: space-between; align-items: center; }
    .sb-metric-label { font-size: 0.78rem; color: #64748b; }
    .sb-metric-value { font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; color: #cbd5e1; font-weight: 500; }
    .sb-metric-value.accent { color: #3b82f6; font-weight: 700; }

    .sb-risk-bar-wrap { height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; margin-top: 4px; }
    .sb-risk-bar { height: 100%; border-radius: 3px; transition: width 0.4s ease; }

    .sb-section-title { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: #475569; margin-bottom: 2px; }

    .sb-chokepoint-badge {
        display: flex; align-items: center; gap: 8px; padding: 10px 12px;
        background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.25);
        border-radius: 10px; font-size: 0.78rem; color: #fbbf24;
    }

    .sb-footer { padding: 14px 20px; border-top: 1px solid rgba(255,255,255,0.06); display: flex; flex-direction: column; gap: 8px; }

    .btn-simulate {
        width: 100%; padding: 11px; border: none; border-radius: 10px; cursor: pointer;
        font-family: 'Inter', sans-serif; font-size: 0.82rem; font-weight: 600;
        background: linear-gradient(135deg, #dc2626, #991b1b);
        color: white; letter-spacing: 0.04em;
        box-shadow: 0 4px 14px rgba(220,38,38,0.3);
        transition: all 0.2s ease;
    }
    .btn-simulate:hover { transform: translateY(-1px); box-shadow: 0 6px 20px rgba(220,38,38,0.4); }
    .btn-simulate:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

    .btn-reset {
        width: 100%; padding: 9px; border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; cursor: pointer;
        font-family: 'Inter', sans-serif; font-size: 0.78rem; font-weight: 500;
        background: transparent; color: #64748b;
        transition: all 0.2s ease;
    }
    .btn-reset:hover { border-color: rgba(255,255,255,0.2); color: #94a3b8; }

    /* ── Simulation Overlay ── */
    #db-sim-result {
        position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
        background: rgba(6,13,26,0.95); border: 1px solid rgba(220,38,38,0.4);
        border-radius: 12px; padding: 14px 22px;
        display: none; align-items: center; gap: 12px;
        font-size: 0.82rem; color: #e2e8f0;
        box-shadow: 0 0 30px rgba(220,38,38,0.2);
        backdrop-filter: blur(16px); z-index: 200;
        animation: fadeUp 0.25s ease;
        white-space: nowrap;
    }
    #db-sim-result.show { display: flex; }
    @keyframes fadeUp { from { opacity: 0; transform: translate(-50%, 10px); } to { opacity: 1; transform: translate(-50%, 0); } }
    .sim-count { font-family: 'JetBrains Mono', monospace; font-weight: 700; color: #ef4444; font-size: 1.1rem; }

    /* ── Top-left legend ── */
    #db-legend {
        position: fixed; top: 20px; left: 20px; z-index: 100;
        background: rgba(6,13,26,0.85); border: 1px solid rgba(255,255,255,0.06);
        border-radius: 12px; padding: 12px 16px;
        backdrop-filter: blur(12px);
        display: flex; flex-direction: column; gap: 6px;
    }
    .legend-row { display: flex; align-items: center; gap: 8px; font-size: 0.72rem; color: #64748b; }
    .legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
    .legend-diamond { width: 10px; height: 10px; transform: rotate(45deg); flex-shrink: 0; background: #fbbf24; }
    </style>
    """

    sidebar_html = """
    <!-- DepBlast Sidebar -->
    <div id="db-sidebar">
      <div class="sb-header">
        <div>
          <div class="sb-pkg-name" id="sb-name">—</div>
          <div class="sb-version" id="sb-version"></div>
        </div>
        <button class="sb-close" id="sb-close">✕</button>
      </div>
      <div class="sb-body">
        <div id="sb-badge-wrap"></div>
        <div id="sb-chokepoint-wrap"></div>

        <div class="sb-section-title">Risk Metrics</div>
        <div class="sb-metric-row">
          <div class="sb-metric">
            <span class="sb-metric-label">Risk Score</span>
            <span class="sb-metric-value accent" id="sb-risk-score">—</span>
          </div>
          <div class="sb-risk-bar-wrap"><div class="sb-risk-bar" id="sb-risk-bar" style="width:0%"></div></div>

          <div class="sb-metric"><span class="sb-metric-label">Blast Radius</span><span class="sb-metric-value" id="sb-blast">—</span></div>
          <div class="sb-metric"><span class="sb-metric-label">Depth</span><span class="sb-metric-value" id="sb-depth">—</span></div>
          <div class="sb-metric"><span class="sb-metric-label">Fan-in (dependents)</span><span class="sb-metric-value" id="sb-fanout">—</span></div>
          <div class="sb-metric"><span class="sb-metric-label">Type</span><span class="sb-metric-value" id="sb-type">—</span></div>
        </div>

        <div class="sb-section-title" id="sb-npm-title" style="display:none">NPM Intelligence</div>
        <div class="sb-metric-row" id="sb-npm-metrics">
          <div class="sb-metric" id="sb-npm-maintainers-row" style="display:none"><span class="sb-metric-label">Maintainers</span><span class="sb-metric-value" id="sb-maintainers">—</span></div>
          <div class="sb-metric" id="sb-npm-stale-row" style="display:none"><span class="sb-metric-label">Last published</span><span class="sb-metric-value" id="sb-stale">—</span></div>
          <div class="sb-metric" id="sb-npm-age-row" style="display:none"><span class="sb-metric-label">Package age</span><span class="sb-metric-value" id="sb-age">—</span></div>
        </div>
      </div>
      <div class="sb-footer">
        <button class="btn-simulate" id="btn-simulate">☢ Simulate Compromise</button>
        <button class="btn-reset" id="btn-reset">Clear Simulation</button>
      </div>
    </div>

    <!-- Simulation result toast -->
    <div id="db-sim-result">
      <span>☢ Blast radius:</span>
      <span class="sim-count" id="sim-count">0</span>
      <span>packages impacted</span>
    </div>

    <!-- Legend -->
    <div id="db-legend">
      <div class="legend-row"><div class="legend-dot" style="background:#ef4444"></div> Critical</div>
      <div class="legend-row"><div class="legend-dot" style="background:#f97316"></div> High</div>
      <div class="legend-row"><div class="legend-dot" style="background:#f59e0b"></div> Medium</div>
      <div class="legend-row"><div class="legend-dot" style="background:#22c55e"></div> Low</div>
      <div class="legend-row"><div class="legend-diamond"></div> Chokepoint</div>
      <div class="legend-row"><div class="legend-dot" style="background:#64748b;border-radius:2px"></div> Dev dep</div>
    </div>
    """

    sidebar_js = """
    <script>
    // ── Sidebar logic ────────────────────────────────────────────────────────
    const sidebar   = document.getElementById('db-sidebar');
    const sbClose   = document.getElementById('sb-close');
    const btnSim    = document.getElementById('btn-simulate');
    const btnReset  = document.getElementById('btn-reset');
    const simResult = document.getElementById('db-sim-result');
    const simCount  = document.getElementById('sim-count');

    let selectedNodeId = null;
    let originalColors = {};

    const LEVEL_COLORS = {
        critical: '#ef4444', high: '#f97316', medium: '#f59e0b', low: '#22c55e', unknown: '#64748b'
    };
    const BADGE_CLASSES = {
        critical: 'badge-critical', high: 'badge-high', medium: 'badge-medium', low: 'badge-low'
    };

    function formatDays(d) {
        if (d === null || d === undefined) return null;
        if (d < 30) return d + ' days';
        if (d < 365) return Math.round(d/30) + ' months';
        return (d/365).toFixed(1) + ' years';
    }

    function openSidebar(nodeId, nodeData) {
        selectedNodeId = nodeId;
        sidebar.classList.add('open');

        const parts = nodeId.split('@');
        const version = parts.length > 1 ? '@' + parts[parts.length - 1] : '';
        const name = parts.length > 1 ? parts.slice(0, parts.length - 1).join('@') : nodeId;

        document.getElementById('sb-name').textContent = name;
        document.getElementById('sb-version').textContent = version;

        // Badge
        const level = nodeData.level || 'unknown';
        document.getElementById('sb-badge-wrap').innerHTML =
            `<span class="sb-badge ${BADGE_CLASSES[level] || ''}">${level.toUpperCase()}</span>`;

        // Chokepoint
        document.getElementById('sb-chokepoint-wrap').innerHTML =
            nodeData.chokepoint
            ? `<div class="sb-chokepoint-badge">🎯 Structural Chokepoint — high blast-radius, many dependents</div>`
            : '';

        // Metrics
        const riskMax = 300;
        const riskPct = Math.min((nodeData.risk / riskMax) * 100, 100);
        document.getElementById('sb-risk-score').textContent = nodeData.risk?.toFixed(1) ?? '—';
        const bar = document.getElementById('sb-risk-bar');
        bar.style.width = riskPct + '%';
        bar.style.background = LEVEL_COLORS[level] || '#64748b';

        document.getElementById('sb-blast').textContent = (nodeData.blast ?? '—') + ' pkgs';
        document.getElementById('sb-depth').textContent = nodeData.depth ?? '—';
        document.getElementById('sb-fanout').textContent = nodeData.fanout ?? '—';
        document.getElementById('sb-type').textContent = nodeData.is_dev ? 'Dev dependency' : 'Production';

        // NPM data
        let hasNpm = false;
        if (nodeData.maintainer_count !== null && nodeData.maintainer_count !== undefined) {
            document.getElementById('sb-npm-maintainers-row').style.display = 'flex';
            document.getElementById('sb-maintainers').textContent =
                nodeData.maintainer_count + (nodeData.maintainer_count === 1 ? ' ⚠ Solo' : '');
            hasNpm = true;
        } else {
            document.getElementById('sb-npm-maintainers-row').style.display = 'none';
        }
        const staleDays = formatDays(nodeData.days_since_publish);
        if (staleDays) {
            document.getElementById('sb-npm-stale-row').style.display = 'flex';
            document.getElementById('sb-stale').textContent = staleDays + ' ago';
            hasNpm = true;
        } else {
            document.getElementById('sb-npm-stale-row').style.display = 'none';
        }
        const ageDays = formatDays(nodeData.package_age_days);
        if (ageDays) {
            document.getElementById('sb-npm-age-row').style.display = 'flex';
            document.getElementById('sb-age').textContent = ageDays;
            hasNpm = true;
        } else {
            document.getElementById('sb-npm-age-row').style.display = 'none';
        }
        document.getElementById('sb-npm-title').style.display = hasNpm ? 'block' : 'none';
    }

    function closeSidebar() {
        sidebar.classList.remove('open');
        selectedNodeId = null;
    }

    sbClose.addEventListener('click', closeSidebar);

    // ── Wait for vis network, then bind click ────────────────────────────────
    const _waitForNetwork = setInterval(() => {
        if (typeof network === 'undefined') return;
        clearInterval(_waitForNetwork);

        network.on('click', function(params) {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                // Pull stored node data from the dataset
                const nodeDataRaw = network.body.data.nodes.get(nodeId);
                // We'll fetch extra metadata via API
                fetch('/node_metadata?id=' + encodeURIComponent(nodeId))
                    .then(r => r.json())
                    .then(data => {
                        openSidebar(nodeId, data);
                    })
                    .catch(() => {
                        openSidebar(nodeId, {});
                    });
            } else {
                closeSidebar();
            }
        });

        network.once('stabilizationIterationsDone', function() {
            const lb = document.getElementById('loadingBar');
            if (lb) lb.style.pointerEvents = 'none';
        });
    }, 100);

    // ── Simulate ─────────────────────────────────────────────────────────────
    btnSim.addEventListener('click', () => {
        if (!selectedNodeId) return;
        btnSim.disabled = true;
        btnSim.textContent = 'Simulating…';

        // Reset previous highlight
        resetHighlight();

        fetch('/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ package: selectedNodeId })
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) { alert(data.error); return; }
            highlightBlastRadius(selectedNodeId, data.impacted_packages);
            simCount.textContent = data.impacted_count;
            simResult.classList.add('show');
        })
        .finally(() => {
            btnSim.disabled = false;
            btnSim.textContent = '☢ Simulate Compromise';
        });
    });

    btnReset.addEventListener('click', () => {
        resetHighlight();
        simResult.classList.remove('show');
    });

    function highlightBlastRadius(sourceId, impacted) {
        const nodes = network.body.data.nodes;
        const edges = network.body.data.edges;
        const impactedSet = new Set(impacted);
        const allIds = nodes.getIds();

        const nodeUpdates = allIds.map(id => {
            if (id === sourceId) {
                return { id, color: { background: '#ef4444', border: '#fbbf24' }, size: 30 };
            } else if (impactedSet.has(id)) {
                return { id, color: { background: '#f97316', border: '#ef4444' } };
            } else {
                return { id, color: { background: '#1e293b', border: '#1e293b' }, opacity: 0.35 };
            }
        });
        nodes.update(nodeUpdates);
    }

    function resetHighlight() {
        // Reload the page cleanly resets all node styles from pyvis
        // Instead we'll soft-reset by reloading the network data
        location.reload();
    }

    // ── Loading screen injection ─────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function() {
        document.body.style.overflow = 'hidden';
        const outer = document.querySelector('div.outerBorder');
        if (!outer) return;
        const header = document.createElement('div');
        header.className = 'db-loading-header';
        header.innerHTML = `
            <div class="db-spinner"></div>
            <div>
                <div class="db-loading-title">Building Graph</div>
                <div class="db-loading-sub">Simulating physics model…</div>
            </div>`;
        outer.insertBefore(header, outer.firstChild);
    });
    </script>
    """

    html = html.replace("</style>", custom_css + "</style>", 1)
    html = html.replace("</body>", sidebar_html + sidebar_js + "</body>")
    GRAPH_HTML.write_text(html, encoding="utf-8")


def _run_full_analysis(lock_json: dict, enrich_npm: bool = False) -> dict:
    """Run the full DepBlast analysis pipeline and return the dependency map."""
    import extract_dependencies as extractor
    from pathlib import Path as _Path
    import tempfile, os

    # Write to temp file and swap LOCK_FILE pointer
    tmp = ROOT_DIR / "_depblast_temp_lock.json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lock_json, f)

    orig = extractor.LOCK_FILE
    extractor.LOCK_FILE = tmp

    try:
        deps = extractor.extract_dependencies(enrich_npm=enrich_npm)
        extractor.compute_fanout(deps)
        extractor.compute_blast_radii(deps)
        extractor.detect_chokepoints(deps)
        extractor.compute_risk_scores(deps)
    finally:
        extractor.LOCK_FILE = orig
        tmp.unlink(missing_ok=True)

    return deps


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze_dependencies():
    global analysis_cache, health_cache

    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_file = request.files["file"]
    if not uploaded_file.filename:
        return jsonify({"error": "No file selected"}), 400

    enrich = request.form.get("enrich", "false").lower() == "true"

    try:
        raw = uploaded_file.read().decode("utf-8")
        lock_json = json.loads(raw)

        if "packages" not in lock_json:
            return jsonify({"error": "Invalid package-lock.json — 'packages' field missing"}), 400

        pkg_count = len(lock_json.get("packages", {}))

        # Hard cap — prevent hanging on giant monorepos
        if pkg_count > 5000:
            return jsonify({"error": f"Lockfile too large ({pkg_count} packages). Limit is 5000."}), 400

        # Auto-disable NPM enrichment for large files to keep response time reasonable
        if enrich and pkg_count > 500:
            enrich = False  # will be communicated back in response
            enrich_disabled_auto = True
        else:
            enrich_disabled_auto = False

        deps = _run_full_analysis(lock_json, enrich_npm=enrich)
        analysis_cache = deps

        health = compute_structural_health(deps)
        health_cache = health

        build_dependency_graph(deps)

        # Top risks
        top_risk = sorted(deps.items(), key=lambda x: x[1]["risk_score"], reverse=True)[:10]

        # Top blast-radius packages (most dangerous single points of failure)
        top_blast = sorted(deps.items(), key=lambda x: x[1]["blast_radius"], reverse=True)[:5]

        return jsonify({
            "success": True,
            "enrich_auto_disabled": enrich_disabled_auto,
            "health": health,
            "top_risks": [
                {
                    "name": pkg,
                    "version": meta["version"],
                    "risk_score": meta["risk_score"],
                    "risk_level": meta["risk_level"],
                    "blast_radius": meta["blast_radius"],
                    "is_chokepoint": meta["is_chokepoint"],
                    "is_dev": meta["is_dev"],
                    "maintainer_count": meta.get("maintainer_count"),
                    "days_since_publish": meta.get("days_since_publish"),
                }
                for pkg, meta in top_risk
            ],
            "top_blast": [
                {
                    "name": pkg,
                    "blast_radius": meta["blast_radius"],
                    "risk_level": meta["risk_level"],
                }
                for pkg, meta in top_blast
            ],
        })

    except Exception as err:
        import traceback
        return jsonify({"error": str(err), "trace": traceback.format_exc()}), 500


@app.route("/graph")
def show_graph():
    if not GRAPH_HTML.exists():
        return "<h2>No graph generated yet. Please analyze a project first.</h2>", 404
    return render_template("graph.html")


@app.route("/node_metadata")
def node_metadata():
    """Return cached metadata for a node to populate the sidebar."""
    pkg_id = request.args.get("id", "")
    if pkg_id not in analysis_cache:
        return jsonify({}), 404
    meta = analysis_cache[pkg_id]
    return jsonify({
        "risk": meta["risk_score"],
        "level": meta["risk_level"],
        "blast": meta["blast_radius"],
        "depth": meta["depth"],
        "fanout": meta["fanout"],
        "is_dev": meta["is_dev"],
        "chokepoint": meta["is_chokepoint"],
        "maintainer_count": meta.get("maintainer_count"),
        "days_since_publish": meta.get("days_since_publish"),
        "package_age_days": meta.get("package_age_days"),
    })


@app.route("/simulate", methods=["POST"])
def simulate_attack():
    if not analysis_cache:
        return jsonify({"error": "No analysis data available. Please upload a lock file first."}), 400

    payload = request.get_json(silent=True) or {}
    target = payload.get("package")

    if not target or target not in analysis_cache:
        return jsonify({"error": f"Package '{target}' not found in current analysis"}), 404

    affected = simulate_compromise(target, analysis_cache)

    return jsonify({
        "target": target,
        "impacted_count": len(affected),
        "impacted_packages": list(affected),
    })


@app.route("/api/v1/scan", methods=["POST"])
def ci_scan():
    """CI/CD-ready endpoint.
    POST multipart/form-data with 'file' = package-lock.json
    Returns pass/fail + summary JSON.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    uploaded_file = request.files["file"]
    threshold_score = float(request.form.get("threshold", 150))
    max_chokepoints = int(request.form.get("max_chokepoints", 3))

    try:
        raw = uploaded_file.read().decode("utf-8")
        lock_json = json.loads(raw)
        if "packages" not in lock_json:
            return jsonify({"error": "Invalid package-lock.json"}), 400

        deps = _run_full_analysis(lock_json, enrich_npm=False)
        health = compute_structural_health(deps)

        top_risk = sorted(deps.items(), key=lambda x: x[1]["risk_score"], reverse=True)[:5]
        max_score = top_risk[0][1]["risk_score"] if top_risk else 0
        chokepoint_count = health.get("chokepoint_count", 0)

        passed = max_score < threshold_score and chokepoint_count <= max_chokepoints

        return jsonify({
            "pass": passed,
            "summary": {
                "total_deps": health.get("total", 0),
                "prod_deps": health.get("prod_deps", 0),
                "max_risk_score": round(max_score, 2),
                "chokepoint_count": chokepoint_count,
                "critical_count": health.get("risk_distribution", {}).get("critical", 0),
                "high_count": health.get("risk_distribution", {}).get("high", 0),
            },
            "critical_chokepoints": [
                {"name": pkg, "risk_score": meta["risk_score"], "blast_radius": meta["blast_radius"]}
                for pkg, meta in top_risk if meta["is_chokepoint"]
            ],
            "thresholds": {
                "max_risk_score": threshold_score,
                "max_chokepoints": max_chokepoints
            }
        })

    except Exception as err:
        return jsonify({"error": str(err)}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
