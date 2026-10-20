"""Domain mutations on an in-memory document.

Each function receives the document handed to ``GraphStore.update`` (a private
copy), changes it, and raises ``DomainError``/``NotFound`` on problems. Nothing
here touches the filesystem. The store re-validates the result before saving, so
these checks mainly exist to give friendly messages.
"""
import uuid

from django.utils.text import slugify


class DomainError(Exception):
    def __init__(self, message, field=None):
        self.message = message
        self.field = field
        super().__init__(message)


class NotFound(DomainError):
    pass


def _find(doc, coll, field, value, label):
    for item in doc[coll]:
        if item[field] == value:
            return item
    raise NotFound(f"That {label} no longer exists.")


def _new_id():
    return str(uuid.uuid4())


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


# -- fitness nodes ----------------------------------------------------------


def _unique_slug(doc, wanted, exclude_id=None):
    taken = {n["slug"] for n in doc["fitness_nodes"] if n["id"] != exclude_id}
    return wanted in taken


def create_node(doc, data):
    slug = data.get("slug") or slugify(data["title"])
    if not slug:
        raise DomainError("Enter a slug; one could not be derived from the title.", "slug")
    if not data.get("slug"):
        base, n = slug, 2
        while _unique_slug(doc, slug):
            slug, n = f"{base}-{n}", n + 1
    elif _unique_slug(doc, slug):
        raise DomainError(f"The slug '{slug}' is already used by another node.", "slug")
    node = {"id": _new_id(), **_node_fields(data, slug)}
    doc["fitness_nodes"].append(node)
    return node["id"]


def _node_fields(data, slug):
    return {
        "slug": slug,
        "title": data["title"],
        "summary": data["summary"],
        "guidance": data["guidance"],
        "considerations": data["considerations"],
        "tags": data["tags"],
        "references": data["references"],
    }


def update_node(doc, node_id, data):
    node = _find(doc, "fitness_nodes", "id", node_id, "fitness node")
    slug = data.get("slug") or node["slug"]
    if _unique_slug(doc, slug, exclude_id=node_id):
        raise DomainError(f"The slug '{slug}' is already used by another node.", "slug")
    node.update(_node_fields(data, slug))


def node_blockers(doc, node_id):
    blockers = []
    edges = [e for e in doc["edges"] if node_id in (e["source"], e["target"])]
    if edges:
        blockers.append(
            f"It is part of {_plural(len(edges), 'relationship')} (edge). Delete or edit those edges first."
        )
    atts = [a for a in doc["attachments"] if a["node"] == node_id]
    if atts:
        blockers.append(
            f"It is attached to {_plural(len(atts), 'dataset')}. Detach it from those datasets first."
        )
    return blockers


def delete_node(doc, node_id):
    node = _find(doc, "fitness_nodes", "id", node_id, "fitness node")
    blockers = node_blockers(doc, node_id)
    if blockers:
        raise DomainError(f"Cannot delete '{node['title']}'. " + " ".join(blockers))
    doc["fitness_nodes"].remove(node)


# -- relationship types -----------------------------------------------------


def create_rtype(doc, data):
    if any(r["key"] == data["key"] for r in doc["relationship_types"]):
        raise DomainError(f"The key '{data['key']}' already exists.", "key")
    doc["relationship_types"].append(
        {"key": data["key"], "label": data["label"], "description": data["description"]}
    )


def update_rtype(doc, key, data):
    rtype = _find(doc, "relationship_types", "key", key, "relationship type")
    rtype["label"] = data["label"]
    rtype["description"] = data["description"]


def rtype_blockers(doc, key):
    n = sum(1 for e in doc["edges"] if e["relationship_type"] == key)
    if n:
        return [f"It is used by {_plural(n, 'edge')}. Delete or change those edges first."]
    return []


def delete_rtype(doc, key):
    rtype = _find(doc, "relationship_types", "key", key, "relationship type")
    blockers = rtype_blockers(doc, key)
    if blockers:
        raise DomainError(f"Cannot delete '{rtype['label']}'. " + " ".join(blockers))
    doc["relationship_types"].remove(rtype)


# -- edges ------------------------------------------------------------------


def _check_edge(doc, data, exclude_id=None):
    _find(doc, "fitness_nodes", "id", data["source"], "source node")
    _find(doc, "fitness_nodes", "id", data["target"], "target node")
    _find(doc, "relationship_types", "key", data["relationship_type"], "relationship type")
    for e in doc["edges"]:
        if (
            e["id"] != exclude_id
            and e["source"] == data["source"]
            and e["target"] == data["target"]
            and e["relationship_type"] == data["relationship_type"]
        ):
            raise DomainError(
                "This relationship already exists (same source, type and target).",
                "relationship_type",
            )


def create_edge(doc, data):
    _check_edge(doc, data)
    edge = {
        "id": _new_id(),
        "source": data["source"],
        "relationship_type": data["relationship_type"],
        "target": data["target"],
        "note": data["note"],
    }
    doc["edges"].append(edge)
    return edge["id"]


def update_edge(doc, edge_id, data):
    edge = _find(doc, "edges", "id", edge_id, "edge")
    _check_edge(doc, data, exclude_id=edge_id)
    edge.update(
        source=data["source"],
        relationship_type=data["relationship_type"],
        target=data["target"],
        note=data["note"],
    )


def delete_edge(doc, edge_id):
    doc["edges"].remove(_find(doc, "edges", "id", edge_id, "edge"))


# -- datasets and attachments -----------------------------------------------


def _check_dataset_ext(doc, ext, exclude_id=None):
    if any(d["external_id"] == ext and d["id"] != exclude_id for d in doc["datasets"]):
        raise DomainError(f"The identifier '{ext}' is already used by another dataset.", "external_id")


def create_dataset(doc, data):
    _check_dataset_ext(doc, data["external_id"])
    dataset = {"id": _new_id(), **{k: data[k] for k in ("external_id", "title", "url", "description")}}
    doc["datasets"].append(dataset)
    return dataset["id"]


def update_dataset(doc, dataset_id, data):
    dataset = _find(doc, "datasets", "id", dataset_id, "dataset")
    _check_dataset_ext(doc, data["external_id"], exclude_id=dataset_id)
    dataset.update({k: data[k] for k in ("external_id", "title", "url", "description")})


def dataset_blockers(doc, dataset_id):
    n = sum(1 for a in doc["attachments"] if a["dataset"] == dataset_id)
    if n:
        return [f"It has {_plural(n, 'attached fitness node')}. Detach them first."]
    return []


def delete_dataset(doc, dataset_id):
    dataset = _find(doc, "datasets", "id", dataset_id, "dataset")
    blockers = dataset_blockers(doc, dataset_id)
    if blockers:
        raise DomainError(f"Cannot delete '{dataset['title']}'. " + " ".join(blockers))
    doc["datasets"].remove(dataset)


def create_attachment(doc, dataset_id, data):
    _find(doc, "datasets", "id", dataset_id, "dataset")
    _find(doc, "fitness_nodes", "id", data["node"], "fitness node")
    if any(a["dataset"] == dataset_id and a["node"] == data["node"] for a in doc["attachments"]):
        raise DomainError("That fitness node is already attached to this dataset.", "node")
    att = {
        "id": _new_id(),
        "dataset": dataset_id,
        "node": data["node"],
        "scope": data["scope"],
        "explanation": data["explanation"],
        "evidence_urls": data["evidence_urls"],
    }
    doc["attachments"].append(att)
    return att["id"]


def update_attachment(doc, attachment_id, data):
    att = _find(doc, "attachments", "id", attachment_id, "attachment")
    att.update(scope=data["scope"], explanation=data["explanation"], evidence_urls=data["evidence_urls"])


def delete_attachment(doc, attachment_id):
    doc["attachments"].remove(_find(doc, "attachments", "id", attachment_id, "attachment"))
