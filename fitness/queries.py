"""Read-only helpers over a document: lookups, attachment status, graph payload.

Nothing here computes a fitness score or infers applicability. A node is
"attached" only if an attachment row names it for the dataset. "connected" is a
purely informational hint: the node shares an edge with an attached node.
"""
from collections import deque

ATTACHED, CONNECTED, OTHER = "attached", "connected", "other"


def by_id(doc, coll, field="id"):
    return {item[field]: item for item in doc[coll]}


def node_edges(doc, node_id):
    """Edges touching a node, as (direction, edge) with direction 'out'/'in'/'loop'."""
    result = []
    for e in doc["edges"]:
        if e["source"] == node_id and e["target"] == node_id:
            result.append(("loop", e))
        elif e["source"] == node_id:
            result.append(("out", e))
        elif e["target"] == node_id:
            result.append(("in", e))
    return result


def neighbor_ids(doc, node_id):
    ids = set()
    for e in doc["edges"]:
        if e["source"] == node_id:
            ids.add(e["target"])
        elif e["target"] == node_id:
            ids.add(e["source"])
    ids.discard(node_id)
    return ids


def neighborhood(doc, node_id, depth=1):
    """Node ids within ``depth`` undirected hops (breadth-first; cycle-safe)."""
    seen = {node_id}
    queue = deque([(node_id, 0)])
    while queue:
        current, d = queue.popleft()
        if d == depth:
            continue
        for nxt in neighbor_ids(doc, current):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, d + 1))
    return seen


def attachments_for_dataset(doc, dataset_id):
    return {a["node"]: a for a in doc["attachments"] if a["dataset"] == dataset_id}


def node_status_map(doc, dataset_id=None):
    """Map every node id to attached/connected/other for the given dataset."""
    attached = set(attachments_for_dataset(doc, dataset_id)) if dataset_id else set()
    status = {}
    for n in doc["fitness_nodes"]:
        status[n["id"]] = ATTACHED if n["id"] in attached else OTHER
    for node_id in attached:
        for nb in neighbor_ids(doc, node_id):
            if status.get(nb) == OTHER:
                status[nb] = CONNECTED
    return status


def suggestions(doc, dataset_id):
    """Unattached nodes linked to attached ones, for the curator to consider."""
    attached = attachments_for_dataset(doc, dataset_id)
    nodes = by_id(doc, "fitness_nodes")
    rtypes = by_id(doc, "relationship_types", "key")
    found = {}
    for e in doc["edges"]:
        for near, far in ((e["source"], e["target"]), (e["target"], e["source"])):
            if near in attached and far not in attached:
                found.setdefault(far, []).append(
                    {
                        "attached_node": nodes[near],
                        "edge": e,
                        "label": rtypes[e["relationship_type"]]["label"],
                        "outgoing": near == e["source"],
                    }
                )
    return [
        {"node": nodes[nid], "links": links}
        for nid, links in sorted(found.items(), key=lambda kv: nodes[kv[0]]["title"].lower())
    ]


def graph_payload(doc, dataset_id, urls):
    """Plain data for D3. ``urls`` is a callable(name, *args) -> URL string.

    This is a fresh copy for the browser; D3 may mutate it freely and it is never
    written back to the canonical document.
    """
    status = node_status_map(doc, dataset_id)
    attachments = attachments_for_dataset(doc, dataset_id) if dataset_id else {}
    rtypes = by_id(doc, "relationship_types", "key")
    nodes = []
    for n in doc["fitness_nodes"]:
        att = attachments.get(n["id"])
        nodes.append(
            {
                "id": n["id"],
                "slug": n["slug"],
                "title": n["title"],
                "summary": n["summary"],
                "guidance": n["guidance"],
                "considerations": n["considerations"],
                "tags": n["tags"],
                "references": n["references"],
                "status": status[n["id"]],
                "attachment": None
                if att is None
                else {
                    "scope": att["scope"],
                    "explanation": att["explanation"],
                    "evidence_urls": att["evidence_urls"],
                    "edit_url": urls("attachment_edit", att["id"]),
                },
                "urls": {
                    "detail": urls("node_detail", n["id"]),
                    "edit": urls("node_edit", n["id"]),
                    "add_edge": urls("edge_create") + f"?source={n['id']}",
                    "attach": urls("attachment_create", dataset_id) + f"?node={n['id']}"
                    if dataset_id and att is None
                    else None,
                },
            }
        )
    edges = [
        {
            "id": e["id"],
            "source": e["source"],
            "target": e["target"],
            "type": e["relationship_type"],
            "label": rtypes[e["relationship_type"]]["label"],
            "note": e["note"],
            "edit_url": urls("edge_edit", e["id"]),
        }
        for e in doc["edges"]
    ]
    return {"nodes": nodes, "edges": edges, "dataset_id": dataset_id}
