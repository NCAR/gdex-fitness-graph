# gdex-fitness-graph

A graph-based model for organizing reusable dataset fitness information, connecting related characteristics, and applying them to datasets to help assess suitability for different uses.

This is a **local curator prototype**: a Django app with server-rendered pages, a little JavaScript, and an interactive D3 graph. All domain data lives in one reviewable JSON file; there is no database for domain data.

*Fitness* means suitability for a particular use, not a universal quality score. The app never computes a score.

## Quick start

Requires Python 3.10+ (developed on 3.11).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. The committed example graph (`data/fitness_graph.json`) is loaded by default. No `migrate` step is needed: no database is configured.

Run the tests and the Django checks:

```bash
python manage.py test fitness
python manage.py check
```

Tests use a temporary copy of the example data and assert that the committed file is never modified.

### Configuration

| Setting / env var | Default | Meaning |
|---|---|---|
| `FITNESS_GRAPH_PATH` | `data/fitness_graph.json` | Path of the JSON document. Set in the environment before `runserver`. |
| `DJANGO_DEBUG` | `1` | `0` disables debug mode. |

A missing file is treated as an empty graph and created on the first save. A malformed or invalid file is **never** overwritten or replaced with sample data; pages show an error until you fix or restore it (for example with `git checkout`).

## Concepts

- **Fitness node**: reusable guidance text (e.g. "Reanalysis", "Data assimilation", "Spatial resolution"). Categories, processes and limitations are all just nodes.
- **Relationship type + edge**: a *descriptive* link between two nodes (e.g. Reanalysis → *uses process* → Data assimilation). Cycles and self-loops are allowed.
- **Dataset**: a dataset (e.g. a GDEX ID such as `d633000`).
- **Attachment**: a curator's *explicit* decision that a node applies to a dataset, optionally with a scope ("precipitation variables", "before 1979"), a dataset-specific explanation and evidence URLs.

### Explicit attachment vs. descriptive relationships

Edges never imply applicability or inheritance. Attaching "Reanalysis" to a dataset does not attach "Data assimilation", and attaching a node does not attach its neighbours. The UI shows connected-but-unattached nodes ("◌ Connected, not attached") only as prompts for the curator to *consider*; each attachment is created by hand. "Connected" is a single hop and purely informational.

In the example data, ERA5 has three explicit attachments (one with a time-specific scope, one with a variable-specific scope) while "Data assimilation", directly linked to the attached "Reanalysis", stays unattached. All example attachments are illustrative curator examples, not authoritative assessments; the second dataset is fictional.

## JSON format (schema version 1)

```jsonc
{
  "schema_version": 1,
  "fitness_nodes": [
    { "id": "<uuid>", "slug": "reanalysis", "title": "...", "summary": "...",
      "guidance": "plain text", "considerations": ["..."], "tags": ["..."],
      "references": [{ "title": "...", "url": "https://..." }] }
  ],
  "relationship_types": [
    { "key": "uses_process", "label": "uses process", "description": "..." }
  ],
  "edges": [
    { "id": "<uuid>", "source": "<node uuid>", "relationship_type": "uses_process",
      "target": "<node uuid>", "note": "optional" }
  ],
  "datasets": [
    { "id": "<uuid>", "external_id": "d633000", "title": "...", "url": "optional http(s)", "description": "optional" }
  ],
  "attachments": [
    { "id": "<uuid>", "dataset": "<dataset uuid>", "node": "<node uuid>", "scope": "optional",
      "explanation": "optional", "evidence_urls": ["https://..."] }
  ]
}
```

Rules enforced on every save and import:

- IDs are lowercase UUID strings; node slugs (`a-b-c`) and relationship keys (`snake_case`) are unique; dataset `external_id`s are unique.
- Edges must reference existing nodes and relationship types. A duplicate edge is the same source, relationship type and target.
- Attachments must reference an existing dataset and node; one attachment per (dataset, node) pair.
- All URLs (references, dataset URL, evidence) must be `http` or `https`.
- Unknown fields are rejected; unsupported `schema_version` values are rejected with an explanation.
- Optional fields are written as `""` / `[]` so the shape is uniform.

Output is canonical: 2-space indent, fixed key order, UTF-8, trailing newline, collections sorted (nodes by slug, relationship types by key, edges by source slug / type / target slug, datasets by external ID, attachments by dataset external ID / node slug). Tags are sorted. IDs are never regenerated on edit or export. Hand edits in any order are accepted and normalized on the next save.

Deleting is blocked while something references the item: nodes with edges or attachments, relationship types used by edges, datasets with attachments. The message says what to remove first.

## Where data is saved, and reviewing in Git

Every change rewrites `data/fitness_graph.json` (or `FITNESS_GRAPH_PATH`) atomically (temp file + `fsync` + rename). Review and revert with ordinary Git:

```bash
git diff data/fitness_graph.json
git checkout -- data/fitness_graph.json   # discard uncontrolled edits
```

Operational files go to `.fitness-local/` next to the data file (lock file, import backups, staged imports) and are git-ignored.

### Concurrency

Writes take an exclusive file lock (POSIX `flock`; macOS/Linux). Each form carries a **revision** (a hash of the canonical document). Under the lock, the app re-reads the file, compares revisions, applies the change, validates, and saves. A stale form is rejected with a message, your entries are kept in the form, and it is refreshed with the current revision so you can review and resubmit deliberately. The revision is document-wide, so an unrelated save also makes an open form stale. Because the revision is content-based, edits made directly in Git or an editor are detected too.

## Import / export

- **Export JSON** (nav bar) downloads the canonical document.
- **Import**: upload a file to validate it. You get a clear list of errors, or a summary table (counts, added / removed / changed per collection). Nothing changes yet.
- After validation the normalized document is staged under its SHA-256 hash in `.fitness-local/imports/`. Confirming submits only that hash, so the applied document is exactly the validated one; the browser never supplies a file path.
- Confirming (explicit checkbox, POST, CSRF-protected) replaces the whole document using the same lock, revision check and validation as ordinary edits. If the graph changed after validation, the import is refused and you must re-validate. The previous file is copied to `.fitness-local/backups/` first.

## Graph explorer

D3 v7.9.0 is vendored at `fitness/static/fitness/vendor/` with its ISC license. The visualization module is `fitness/static/fitness/js/graph.js`; graph data arrives via Django's `json_script`. D3 receives clones of the data, so simulation state never reaches the canonical document.

Features: force layout with collision avoidance (settled before painting, so it does not animate continuously), drag (pins a node; double-click releases), pan/zoom, **Fit to view**, **Reset layout**, directed arrowheads, edge labels, bowed parallel/reciprocal edges and looped self-edges, search (title, summary, guidance, tags; non-matches fade), a *focus on selected node and neighbours* filter that keeps node positions, a details panel (guidance, references, relationships, links to edit forms), and a legend. Choose a dataset to highlight its explicit attachments: attached nodes have a solid fill and ✓; connected-but-unattached nodes have a dashed outline and ◌; the selected node has an extra ring. State is never conveyed by color alone. The list view (`/nodes/`) shows the same status text and uses the same server-side function as the graph.

Deep links: `/?dataset=<dataset id>&q=<search>&select=<node slug>`.

Editing is by ordinary forms (nodes, relationships, relationship types, datasets, attachments); there is no drag-to-connect editor.

## Verification status

- Automated: `python manage.py test fitness` (70 tests covering round-trip, validation, deletion rules, attachment semantics, concurrency, malformed storage, import/backup, CSRF, escaping, and all CRUD workflows through Django views).
- Graph in a browser: a one-off headless Chrome DOM check confirmed 8 nodes and 11 edges rendered, the dataset highlight classes (3 attached, 5 connected), search fading (2 of 8 matches), and preselected-node details. This was a manual check, not an automated test in the repo. Dragging, zooming, the focus filter, Fit/Reset and the visual look were **not** verified automatically. To check them by hand:
  1. Start the server and open `/`. The 8 example nodes and their labeled arrows should appear, fitted to view.
  2. Click a node: the details panel shows guidance and relationships; click a relationship in the panel to jump to the neighbour.
  3. Type `precip` in Search: non-matching nodes fade and the status line says how many match; type nonsense to see the "No nodes match" message.
  4. Pick *ERA5 Reanalysis* in the Dataset menu: Reanalysis, Changing observing systems and Gridded precipitation show ✓; Data assimilation shows a dashed outline and ◌. Compare with `/nodes/?dataset=<id>`.
  5. Select a node and tick *Focus on selected node and its neighbours*; positions should stay put. Drag a node, scroll to zoom, then use *Fit to view* and *Reset layout*.

## Prototype limitations

- Local single-user tool: **no authentication or authorization**, no deployment configuration, no integration with live GDEX services. Do not expose it to a network.
- Storage is one local JSON file with POSIX file locking; it is not designed for large graphs or many concurrent users (graphs of a few hundred nodes are fine).
- Revision checking is document-wide, so unrelated concurrent edits cause a conflict message rather than a merge.
- Backups are written only for imports; ordinary edits rely on Git history.
- Uploaded files are limited to 5 MiB.
- The repository's `requirements` file is the original task description and is left untouched; `requirements.txt` declares the Python dependency.

## License

See `LICENSE`. D3 is distributed under the ISC license (`fitness/static/fitness/vendor/d3.LICENSE.txt`).
