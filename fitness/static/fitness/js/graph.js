// Interactive D3 view of the fitness graph.
//
// Data comes from the page's #graph-data element (Django json_script). The
// canonical copy in `state.data` is never handed to D3: every rebuild clones the
// visible nodes and edges, because d3-force mutates the objects it is given
// (x/y/vx/vy, and it replaces link source/target ids with node objects). Only
// ids and positions (keyed by id) survive a rebuild; nothing here is ever sent
// back to the server.
//
// All text from the data is inserted with textContent / createTextNode, so
// imported content is never interpreted as HTML.

const d3 = globalThis.d3;
const NODE_R = 20;
const LABEL_MAX = 44; // total characters shown, across at most two lines
const LABEL_LINE = 22; // wrap width in characters
const LINK_DISTANCE = 240;
const COLLIDE_R = 80; // wide enough that neighbouring two-line labels do not overlap
const PARALLEL_SPACING = 56; // control-point offset between parallel edges
const MAX_SYNC_NODES = 300; // above this, let the layout animate instead of pre-settling

const STATUS_TEXT = {
  attached: "Attached to the selected dataset",
  connected: "Connected to an attached node, but not attached",
  other: "",
};

// ---------------------------------------------------------------- pure helpers

export function matchesQuery(node, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [node.title, node.summary, node.guidance, ...node.tags].some((text) =>
    text.toLowerCase().includes(q),
  );
}

/** Ids of `id` plus its direct neighbours. One hop, so cycles and loops are harmless. */
export function neighborhoodIds(edges, id) {
  const ids = new Set([id]);
  for (const e of edges) {
    if (e.source === id) ids.add(e.target);
    else if (e.target === id) ids.add(e.source);
  }
  return ids;
}

/** Give each link a groupIndex/groupSize among links joining the same node pair (or loops on one node). */
export function groupLinks(links) {
  const groups = new Map();
  for (const link of links) {
    const key = link.from === link.to ? `loop|${link.from}` : [link.from, link.to].sort().join("|");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(link);
  }
  for (const members of groups.values()) {
    members.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    members.forEach((link, i) => {
      link.groupIndex = i;
      link.groupSize = members.length;
    });
  }
}

function pointAlong(node, towardX, towardY, distance) {
  const dx = towardX - node.x;
  const dy = towardY - node.y;
  const len = Math.hypot(dx, dy) || 1;
  return [node.x + (dx / len) * distance, node.y + (dy / len) * distance];
}

function loopGeometry(link, r) {
  const n = link.source;
  const angle = -Math.PI / 2 + link.groupIndex * 1.1;
  const a1 = angle - 0.45;
  const a2 = angle + 0.45;
  const reach = r + 60 + link.groupIndex * 12;
  const P = (a, d) => [n.x + d * Math.cos(a), n.y + d * Math.sin(a)];
  const [sx, sy] = P(a1, r);
  const [c1x, c1y] = P(a1, reach);
  const [c2x, c2y] = P(a2, reach);
  const [ex, ey] = P(a2, r + 3);
  return {
    d: `M${sx},${sy} C${c1x},${c1y} ${c2x},${c2y} ${ex},${ey}`,
    lx: 0.125 * sx + 0.375 * c1x + 0.375 * c2x + 0.125 * ex,
    ly: 0.125 * sy + 0.375 * c1y + 0.375 * c2y + 0.125 * ey,
  };
}

/**
 * Path and label position for a link whose source/target are positioned node objects.
 * Parallel and reciprocal edges bow to different sides of the line between the nodes.
 */
export function linkGeometry(link, r = NODE_R) {
  const s = link.source;
  const t = link.target;
  if (s === t) return loopGeometry(link, r);
  const dx = t.x - s.x;
  const dy = t.y - s.y;
  const dist = Math.hypot(dx, dy) || 1;
  // Use a direction-independent frame so A->B and B->A get opposite offsets.
  const sign = s.id < t.id ? 1 : -1;
  const offset = (link.groupIndex - (link.groupSize - 1) / 2) * PARALLEL_SPACING;
  const cx = (s.x + t.x) / 2 + ((-dy / dist) * sign) * offset;
  const cy = (s.y + t.y) / 2 + ((dx / dist) * sign) * offset;
  const [sx, sy] = pointAlong(s, cx, cy, r);
  const [ex, ey] = pointAlong(t, cx, cy, r + 3);
  return {
    d: `M${sx},${sy} Q${cx},${cy} ${ex},${ey}`,
    lx: 0.25 * sx + 0.5 * cx + 0.25 * ex,
    ly: 0.25 * sy + 0.5 * cy + 0.25 * ey,
  };
}

function truncate(text, max) {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Split a title into at most two lines, breaking at spaces where possible. */
export function wrapLabel(text, lineMax = LABEL_LINE, total = LABEL_MAX) {
  const t = truncate(text, total);
  if (t.length <= lineMax) return [t];
  let cut = t.lastIndexOf(" ", lineMax);
  if (cut < lineMax / 2) cut = lineMax;
  return [t.slice(0, cut).trimEnd(), t.slice(cut).trimStart()];
}

function safeUrl(url) {
  return /^https?:\/\//i.test(url) ? url : null;
}

/** Tiny DOM builder; strings become text nodes, never HTML. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

// ------------------------------------------------------------------- the view

export function init(root = document) {
  const dataEl = root.getElementById("graph-data");
  const svgEl = root.getElementById("graph-svg");
  if (!dataEl || !svgEl || !d3) return null;

  const state = {
    data: JSON.parse(dataEl.textContent), // canonical copy for the page; never given to D3
    selectedId: null,
    query: "",
    focusOnly: false,
    positions: new Map(), // id -> {x, y, fx, fy}
    nodes: [], // current simulation copies
    links: [],
    simulation: null,
    userZoomed: false,
  };
  const nodeById = new Map(state.data.nodes.map((n) => [n.id, n]));

  const els = {
    details: root.getElementById("details"),
    empty: root.getElementById("graph-empty"),
    search: root.getElementById("graph-search"),
    searchStatus: root.getElementById("search-status"),
    focus: root.getElementById("focus-toggle"),
    fit: root.getElementById("fit-btn"),
    reset: root.getElementById("reset-btn"),
  };

  const svg = d3.select(svgEl);
  const viewport = svg.append("g").attr("class", "viewport");
  const linkLayer = viewport.append("g").attr("class", "links");
  const nodeLayer = viewport.append("g").attr("class", "nodes");
  let linkSel = linkLayer.selectAll("g.link");
  let nodeSel = nodeLayer.selectAll("g.node");

  if (state.data.nodes.length === 0) {
    els.empty.hidden = false;
    renderDetails();
    return state;
  }

  addArrowMarker(svg);

  const zoom = d3
    .zoom()
    .scaleExtent([0.15, 4])
    .on("zoom", (event) => {
      viewport.attr("transform", event.transform);
      if (event.sourceEvent) state.userZoomed = true;
    });
  svg.call(zoom).on("dblclick.zoom", null);
  svg.on("click", () => select(null));

  // ---- building

  function visibleIds() {
    if (state.focusOnly && state.selectedId) return neighborhoodIds(state.data.edges, state.selectedId);
    return new Set(state.data.nodes.map((n) => n.id));
  }

  function snapshotPositions() {
    for (const n of state.nodes) {
      state.positions.set(n.id, { x: n.x, y: n.y, fx: n.fx ?? null, fy: n.fy ?? null });
    }
  }

  function stopSimulation() {
    if (state.simulation) {
      state.simulation.on("tick", null).on("end", null).stop();
      state.simulation = null;
    }
  }

  /** Clone the visible slice of the canonical data into fresh objects for D3. */
  function cloneVisible(ids) {
    const nodes = state.data.nodes
      .filter((n) => ids.has(n.id))
      .map((n) => {
        const saved = state.positions.get(n.id);
        return { id: n.id, title: n.title, status: n.status, ...(saved || {}) };
      });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const links = state.data.edges
      .filter((e) => byId.has(e.source) && byId.has(e.target))
      .map((e) => ({
        id: e.id,
        from: e.source,
        to: e.target,
        label: e.label,
        note: e.note,
        source: byId.get(e.source),
        target: byId.get(e.target),
      }));
    groupLinks(links);
    return { nodes, links };
  }

  function rebuild({ refit = false } = {}) {
    snapshotPositions();
    stopSimulation();
    const { nodes, links } = cloneVisible(visibleIds());
    state.nodes = nodes;
    state.links = links;
    const known = nodes.filter((n) => Number.isFinite(n.x)).length;

    renderElements();

    const sim = d3
      .forceSimulation(nodes)
      .force(
        "link",
        // Self-loops are drawn but exert no force.
        d3.forceLink(links.filter((l) => l.source !== l.target)).distance(LINK_DISTANCE).strength(0.35),
      )
      .force("charge", d3.forceManyBody().strength(-1400).distanceMax(700))
      .force("center", d3.forceCenter(0, 0))
      .force("x", d3.forceX(0).strength(0.03))
      .force("y", d3.forceY(0).strength(0.03))
      .force("collide", d3.forceCollide(COLLIDE_R))
      .alphaDecay(0.05)
      .on("tick", draw)
      .stop();
    state.simulation = sim;

    const alpha = known === nodes.length ? 0.3 : 1; // gentle if everything already has a place
    sim.alpha(alpha);
    if (nodes.length <= MAX_SYNC_NODES) {
      // Settle before first paint instead of animating on screen.
      const ticks = Math.ceil(Math.log(sim.alphaMin() / alpha) / Math.log(1 - sim.alphaDecay()));
      for (let i = 0; i < ticks; i += 1) sim.tick();
      draw();
      snapshotPositions();
    } else {
      sim.restart();
    }
    if (refit || !state.userZoomed) fitToView(refit);
    refreshStates();
  }

  function renderElements() {
    linkSel = linkLayer
      .selectAll("g.link")
      .data(state.links, (d) => d.id)
      .join((enter) => {
        const g = enter.append("g").attr("class", "link");
        g.append("title");
        g.append("path").attr("class", "link-path").attr("marker-end", "url(#arrow)");
        g.append("path").attr("class", "link-hit");
        g.append("text").attr("class", "link-label").attr("text-anchor", "middle");
        return g;
      });
    linkSel.select("title").text((d) => (d.note ? `${d.label}: ${d.note}` : d.label));
    linkSel.select("text").text((d) => d.label);

    nodeSel = nodeLayer
      .selectAll("g.node")
      .data(state.nodes, (d) => d.id)
      .join((enter) => {
        const g = enter.append("g").attr("tabindex", 0).attr("role", "button");
        g.append("title");
        g.append("circle").attr("class", "node-ring").attr("r", NODE_R + 6);
        g.append("circle").attr("class", "node-circle").attr("r", NODE_R);
        g.append("text").attr("class", "node-mark").attr("text-anchor", "middle").attr("dy", "0.35em");
        g.append("text")
          .attr("class", "node-title")
          .attr("text-anchor", "middle")
          .attr("y", NODE_R + 16);
        return g;
      });
    nodeSel.select("title").text((d) => nodeById.get(d.id).title);
    nodeSel.select(".node-mark").text((d) => (d.status === "attached" ? "✓" : d.status === "connected" ? "◌" : ""));
    nodeSel.select(".node-title").each(function (d) {
      const lines = wrapLabel(d.title);
      d3.select(this)
        .selectAll("tspan")
        .data(lines)
        .join("tspan")
        .attr("x", 0)
        .attr("dy", (_, i) => (i === 0 ? 0 : "1.15em"))
        .text((line) => line);
    });
    nodeSel
      .on("click", (event, d) => {
        event.stopPropagation();
        select(d.id);
      })
      .on("keydown", (event, d) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select(d.id);
        }
      })
      .on("dblclick", (event, d) => {
        d.fx = null;
        d.fy = null;
        state.simulation?.alpha(0.3).restart();
      })
      .call(dragBehavior());
  }

  function dragBehavior() {
    return d3
      .drag()
      .on("start", (event, d) => {
        if (!event.active) state.simulation?.alphaTarget(0.25).restart();
        d.fx = d.x;
        d.fy = d.y;
      })
      .on("drag", (event, d) => {
        d.fx = event.x;
        d.fy = event.y;
      })
      .on("end", (event) => {
        if (!event.active) state.simulation?.alphaTarget(0); // stays pinned; double-click to release
        snapshotPositions();
      });
  }

  function draw() {
    nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
    linkSel.each((d) => {
      d.geometry = linkGeometry(d);
    });
    linkSel.selectAll("path").attr("d", (d) => d.geometry.d);
    linkSel
      .select("text")
      .attr("x", (d) => d.geometry.lx)
      .attr("y", (d) => d.geometry.ly - 4);
  }

  // ---- state-dependent styling

  function matchSet() {
    return new Set(state.data.nodes.filter((n) => matchesQuery(n, state.query)).map((n) => n.id));
  }

  function refreshStates() {
    const searching = state.query.trim() !== "";
    const matches = matchSet();
    nodeSel
      .attr("class", (d) => {
        const classes = ["node", `status-${d.status}`];
        if (d.id === state.selectedId) classes.push("selected");
        if (d.fx !== null && d.fx !== undefined) classes.push("pinned");
        if (searching && !matches.has(d.id)) classes.push("dim");
        if (searching && matches.has(d.id)) classes.push("match");
        return classes.join(" ");
      })
      .attr("aria-pressed", (d) => String(d.id === state.selectedId))
      .attr("aria-label", (d) => {
        const status = STATUS_TEXT[d.status];
        return status ? `${nodeById.get(d.id).title}. ${status}.` : nodeById.get(d.id).title;
      });
    linkSel.attr("class", (d) => {
      const incident = state.selectedId && (d.from === state.selectedId || d.to === state.selectedId);
      const classes = ["link"];
      if (incident) classes.push("incident");
      if (searching && !(matches.has(d.from) || matches.has(d.to))) classes.push("dim");
      return classes.join(" ");
    });

    els.focus.disabled = !state.selectedId;
    els.focus.checked = state.focusOnly && Boolean(state.selectedId);
    if (!searching) els.searchStatus.textContent = "";
    else if (matches.size === 0) els.searchStatus.textContent = `No nodes match “${state.query.trim()}”. Clear the search to see all nodes.`;
    else els.searchStatus.textContent = `${matches.size} of ${state.data.nodes.length} nodes match.`;
  }

  // ---- interactions

  function select(id) {
    const changed = id !== state.selectedId;
    state.selectedId = id;
    if (!id && state.focusOnly) {
      state.focusOnly = false;
      rebuild({ refit: true });
    } else if (state.focusOnly && changed) {
      rebuild({ refit: true });
    } else {
      refreshStates();
    }
    renderDetails();
  }

  function fitToView(animate = true) {
    if (state.nodes.length === 0) return;
    const pad = 80;
    const xs = state.nodes.map((n) => n.x);
    const ys = state.nodes.map((n) => n.y);
    const x0 = Math.min(...xs) - pad;
    const x1 = Math.max(...xs) + pad;
    const y0 = Math.min(...ys) - pad;
    const y1 = Math.max(...ys) + pad;
    const width = svgEl.clientWidth || 900;
    const height = svgEl.clientHeight || 600;
    const k = Math.min(width / (x1 - x0), height / (y1 - y0), 1.4);
    const transform = d3.zoomIdentity
      .translate(width / 2 - (k * (x0 + x1)) / 2, height / 2 - (k * (y0 + y1)) / 2)
      .scale(k);
    (animate ? svg.transition().duration(300) : svg).call(zoom.transform, transform);
  }

  els.fit.addEventListener("click", () => {
    state.userZoomed = false;
    fitToView(true);
  });
  els.reset.addEventListener("click", () => {
    state.positions.clear();
    state.nodes = []; // otherwise rebuild() would re-snapshot the old positions
    state.userZoomed = false;
    rebuild({ refit: true });
  });
  els.search.addEventListener("input", () => {
    state.query = els.search.value;
    refreshStates();
  });
  els.focus.addEventListener("change", () => {
    state.focusOnly = els.focus.checked && Boolean(state.selectedId);
    rebuild({ refit: true });
  });
  // Prevent the search form's Enter key from submitting/reloading the page.
  els.search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });

  // ---- details panel

  function externalLink(url, text) {
    const href = safeUrl(url);
    return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, text) : h("span", {}, text);
  }

  function renderDetails() {
    const panel = els.details;
    panel.replaceChildren();
    const node = state.selectedId ? nodeById.get(state.selectedId) : null;
    if (!node) {
      panel.append(
        h("h2", {}, "Details"),
        h("p", { class: "hint" }, "Select a node to read its fitness guidance, references and relationships."),
        h(
          "p",
          { class: "hint" },
          "Relationships are descriptive. They never make a connected node apply to a dataset; only explicit attachments do.",
        ),
      );
      return;
    }
    const parts = [h("h2", {}, node.title)];
    const status = STATUS_TEXT[node.status];
    if (status) parts.push(h("p", { class: `badge badge-${node.status}` }, (node.status === "attached" ? "✓ " : "◌ ") + status));
    parts.push(h("p", { class: "lead" }, node.summary));

    if (node.attachment) {
      const a = node.attachment;
      parts.push(
        h(
          "section",
          { class: "qualification" },
          h("h3", {}, "For the selected dataset"),
          a.scope ? h("p", {}, h("strong", {}, "Scope: "), a.scope) : null,
          a.explanation ? h("p", { class: "pre" }, a.explanation) : h("p", { class: "hint" }, "No dataset-specific explanation."),
          a.evidence_urls.length ? h("ul", {}, a.evidence_urls.map((u) => h("li", {}, externalLink(u, u)))) : null,
          h("a", { href: a.edit_url }, "Edit attachment"),
        ),
      );
    } else if (node.status === "connected") {
      parts.push(
        h("p", { class: "hint" }, "This node is related to an attached node but has not been attached to the dataset. Whether it applies is a curator decision."),
      );
    }

    parts.push(h("h3", {}, "Fitness guidance"), h("p", { class: "pre" }, node.guidance));
    if (node.considerations.length) {
      parts.push(h("h3", {}, "Considerations"), h("ul", {}, node.considerations.map((c) => h("li", {}, c))));
    }
    if (node.tags.length) {
      parts.push(h("p", {}, node.tags.map((t) => h("span", { class: "tag" }, t))));
    }
    if (node.references.length) {
      parts.push(h("h3", {}, "References"), h("ul", {}, node.references.map((r) => h("li", {}, externalLink(r.url, r.title)))));
    }

    parts.push(h("h3", {}, "Relationships"), relationshipList(node));
    parts.push(
      h(
        "p",
        { class: "actions" },
        h("a", { class: "btn", href: node.urls.detail }, "Open page"),
        h("a", { class: "btn", href: node.urls.edit }, "Edit node"),
        h("a", { class: "btn", href: node.urls.add_edge }, "Add relationship"),
        node.urls.attach ? h("a", { class: "btn primary", href: node.urls.attach }, "Review & attach to dataset…") : null,
      ),
    );
    panel.append(...parts);
    panel.scrollTop = 0;
  }

  function relationshipList(node) {
    const mine = state.data.edges.filter((e) => e.source === node.id || e.target === node.id);
    if (mine.length === 0) return h("p", { class: "hint" }, "No relationships.");
    return h(
      "ul",
      { class: "rel-list" },
      mine.map((e) => {
        const other = nodeById.get(e.source === node.id ? e.target : e.source);
        const text =
          e.source === e.target
            ? `${node.title} — ${e.label} → itself`
            : e.source === node.id
              ? `${node.title} — ${e.label} → ${other.title}`
              : `${other.title} — ${e.label} → ${node.title}`;
        return h(
          "li",
          {},
          h("button", { type: "button", class: "link-btn", "data-select": other.id }, text),
          e.note ? h("div", { class: "hint" }, e.note) : null,
          " ",
          h("a", { class: "small", href: e.edit_url }, "edit"),
        );
      }),
    );
  }

  els.details.addEventListener("click", (event) => {
    const target = event.target.closest("[data-select]");
    if (target) select(target.getAttribute("data-select"));
  });

  // ---- start

  const params = new URLSearchParams(globalThis.location?.search ?? "");
  if (params.get("q")) {
    state.query = params.get("q");
    els.search.value = state.query;
  }
  const preselect = state.data.nodes.find((n) => n.slug === params.get("select"));
  if (preselect) state.selectedId = preselect.id;
  rebuild({ refit: true });
  renderDetails();
  return state;
}

function addArrowMarker(svg) {
  svg
    .append("defs")
    .append("marker")
    .attr("id", "arrow")
    .attr("viewBox", "0 0 10 10")
    .attr("refX", 9)
    .attr("refY", 5)
    .attr("markerWidth", 8)
    .attr("markerHeight", 8)
    .attr("orient", "auto")
    .append("path")
    .attr("class", "arrow")
    .attr("d", "M0,0 L10,5 L0,10 z");
}
