"""Schema, validation and canonical serialization of the fitness-graph document.

The document is plain JSON (see README for the format). ``load_document`` is the
single entry point: it validates a raw parsed document, fills optional fields,
and returns a normalized copy with deterministic ordering. Anything saved to
disk has passed through it.
"""
import hashlib
import json
import re
import uuid
from urllib.parse import urlparse

SCHEMA_VERSION = 1

TOP_LEVEL_KEYS = (
    "schema_version",
    "fitness_nodes",
    "relationship_types",
    "edges",
    "datasets",
    "attachments",
)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class SchemaError(Exception):
    """The document is invalid. ``errors`` is a list of human-readable strings."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def empty_document():
    return {
        "schema_version": SCHEMA_VERSION,
        "fitness_nodes": [],
        "relationship_types": [],
        "edges": [],
        "datasets": [],
        "attachments": [],
    }


def is_http_url(value):
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(c.isspace() for c in value):
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def is_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


# --- field specs -----------------------------------------------------------
# Each entity: ordered (field, kind, required). Order here is the on-disk key order.

NODE_FIELDS = (
    ("id", "uuid", True),
    ("slug", "slug", True),
    ("title", "text", True),
    ("summary", "text", True),
    ("guidance", "text", True),
    ("considerations", "strlist", False),
    ("tags", "strlist", False),
    ("references", "references", False),
)
RTYPE_FIELDS = (
    ("key", "key", True),
    ("label", "text", True),
    ("description", "text", False),
)
EDGE_FIELDS = (
    ("id", "uuid", True),
    ("source", "uuid", True),
    ("relationship_type", "key", True),
    ("target", "uuid", True),
    ("note", "text", False),
)
DATASET_FIELDS = (
    ("id", "uuid", True),
    ("external_id", "text", True),
    ("title", "text", True),
    ("url", "url", False),
    ("description", "text", False),
)
ATTACHMENT_FIELDS = (
    ("id", "uuid", True),
    ("dataset", "uuid", True),
    ("node", "uuid", True),
    ("scope", "text", False),
    ("explanation", "text", False),
    ("evidence_urls", "urllist", False),
)

COLLECTIONS = (
    ("fitness_nodes", "fitness node", NODE_FIELDS),
    ("relationship_types", "relationship type", RTYPE_FIELDS),
    ("edges", "edge", EDGE_FIELDS),
    ("datasets", "dataset", DATASET_FIELDS),
    ("attachments", "attachment", ATTACHMENT_FIELDS),
)


def _default(kind):
    return [] if kind in ("strlist", "urllist", "references") else ""


def _check_field(item, name, kind, required, where, errors):
    """Validate one field; append problems to ``errors``."""
    if name not in item:
        if required:
            errors.append(f"{where}: missing required field '{name}'.")
        return
    value = item[name]
    if kind == "uuid":
        if not is_uuid(value):
            errors.append(f"{where}: '{name}' must be a lowercase UUID string.")
    elif kind == "slug":
        if not isinstance(value, str) or not SLUG_RE.match(value):
            errors.append(
                f"{where}: '{name}' must be lowercase letters, digits and single hyphens."
            )
    elif kind == "key":
        if not isinstance(value, str) or not KEY_RE.match(value):
            errors.append(
                f"{where}: '{name}' must start with a lowercase letter and contain "
                "only lowercase letters, digits and underscores."
            )
    elif kind == "text":
        if not isinstance(value, str):
            errors.append(f"{where}: '{name}' must be a string.")
        elif required and not value.strip():
            errors.append(f"{where}: '{name}' must not be empty.")
    elif kind == "url":
        if not isinstance(value, str) or (value and not is_http_url(value)):
            errors.append(f"{where}: '{name}' must be empty or an http(s) URL.")
    elif kind == "strlist":
        if not isinstance(value, list) or not all(
            isinstance(v, str) and v.strip() for v in value
        ):
            errors.append(f"{where}: '{name}' must be a list of non-empty strings.")
    elif kind == "urllist":
        if not isinstance(value, list):
            errors.append(f"{where}: '{name}' must be a list of http(s) URLs.")
        else:
            for url in value:
                if not is_http_url(url):
                    errors.append(f"{where}: '{name}' contains an invalid http(s) URL: {url!r}.")
    elif kind == "references":
        if not isinstance(value, list):
            errors.append(f"{where}: '{name}' must be a list of {{title, url}} objects.")
            return
        for ref in value:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"title", "url"}
                or not isinstance(ref["title"], str)
                or not ref["title"].strip()
            ):
                errors.append(f"{where}: each reference needs exactly a non-empty 'title' and a 'url'.")
            elif not is_http_url(ref["url"]):
                errors.append(f"{where}: reference {ref['title']!r} has an invalid http(s) URL.")


def _label(singular, item, index):
    ident = ""
    if isinstance(item, dict):
        for k in ("slug", "key", "external_id", "id"):
            if isinstance(item.get(k), str):
                ident = f" '{item[k]}'"
                break
    return f"{singular} #{index + 1}{ident}"


def validate(doc):
    """Return a list of error strings (empty when ``doc`` is valid)."""
    if not isinstance(doc, dict):
        return ["The document must be a JSON object."]
    version = doc.get("schema_version")
    if version != SCHEMA_VERSION or isinstance(version, bool):
        return [
            f"Unsupported schema_version {version!r}: this application reads and "
            f"writes only version {SCHEMA_VERSION}. Migrate the file to version "
            f"{SCHEMA_VERSION} before importing."
        ]
    errors = []
    for key in doc:
        if key not in TOP_LEVEL_KEYS:
            errors.append(f"Unknown top-level key '{key}'.")
    for coll, singular, fields in COLLECTIONS:
        items = doc.get(coll)
        if not isinstance(items, list):
            errors.append(f"'{coll}' must be a list.")
            continue
        known = {f for f, _, _ in fields}
        for i, item in enumerate(items):
            where = _label(singular, item, i)
            if not isinstance(item, dict):
                errors.append(f"{where}: must be an object.")
                continue
            for extra in sorted(set(item) - known):
                errors.append(f"{where}: unknown field '{extra}'.")
            for name, kind, required in fields:
                _check_field(item, name, kind, required, where, errors)
    if errors:
        return errors
    return _check_integrity(doc)


def _check_integrity(doc):
    """Uniqueness and referential checks; assumes field-level checks passed."""
    errors = []

    def unique(coll, field, label):
        seen = {}
        for item in doc[coll]:
            value = item[field]
            if value in seen:
                errors.append(f"Duplicate {label} {value!r} in '{coll}'.")
            seen[value] = item
        return seen

    nodes = unique("fitness_nodes", "id", "ID")
    unique("fitness_nodes", "slug", "slug")
    rtypes = unique("relationship_types", "key", "relationship type key")
    unique("edges", "id", "ID")
    datasets = unique("datasets", "id", "ID")
    unique("datasets", "external_id", "dataset external identifier")
    unique("attachments", "id", "ID")

    seen_edges = {}
    for e in doc["edges"]:
        where = f"Edge {e['id']}"
        if e["source"] not in nodes:
            errors.append(f"{where}: source {e['source']} is not a fitness node.")
        if e["target"] not in nodes:
            errors.append(f"{where}: target {e['target']} is not a fitness node.")
        if e["relationship_type"] not in rtypes:
            errors.append(
                f"{where}: unknown relationship type '{e['relationship_type']}'."
            )
        triple = (e["source"], e["relationship_type"], e["target"])
        if triple in seen_edges:
            errors.append(
                f"Duplicate edge: {e['id']} repeats {seen_edges[triple]} "
                "(same source, relationship type and target)."
            )
        seen_edges.setdefault(triple, e["id"])

    seen_pairs = {}
    for a in doc["attachments"]:
        where = f"Attachment {a['id']}"
        if a["dataset"] not in datasets:
            errors.append(f"{where}: dataset {a['dataset']} does not exist.")
        if a["node"] not in nodes:
            errors.append(f"{where}: fitness node {a['node']} does not exist.")
        pair = (a["dataset"], a["node"])
        if pair in seen_pairs:
            errors.append(
                f"Duplicate attachment: {a['id']} repeats {seen_pairs[pair]} "
                "(same dataset and fitness node)."
            )
        seen_pairs.setdefault(pair, a["id"])
    return errors


# --- normalization / serialization ----------------------------------------


def _normalize_item(item, fields):
    out = {}
    for name, kind, _ in fields:
        value = item.get(name, _default(kind))
        if kind == "references":
            value = [{"title": r["title"].strip(), "url": r["url"]} for r in value]
        elif kind in ("strlist", "urllist"):
            value = [v.strip() for v in value]
            if name == "tags":
                value = sorted(set(value), key=lambda t: (t.lower(), t))
        elif kind in ("text", "slug", "key", "uuid", "url"):
            value = value.strip()
        out[name] = value
    return out


def normalize(doc):
    """Return a deterministically ordered copy. ``doc`` must already be valid."""
    nodes = [_normalize_item(n, NODE_FIELDS) for n in doc["fitness_nodes"]]
    slug_of = {n["id"]: n["slug"] for n in nodes}
    datasets = [_normalize_item(d, DATASET_FIELDS) for d in doc["datasets"]]
    ext_of = {d["id"]: d["external_id"] for d in datasets}
    return {
        "schema_version": SCHEMA_VERSION,
        "fitness_nodes": sorted(nodes, key=lambda n: (n["slug"], n["id"])),
        "relationship_types": sorted(
            (_normalize_item(r, RTYPE_FIELDS) for r in doc["relationship_types"]),
            key=lambda r: r["key"],
        ),
        "edges": sorted(
            (_normalize_item(e, EDGE_FIELDS) for e in doc["edges"]),
            key=lambda e: (
                slug_of[e["source"]],
                e["relationship_type"],
                slug_of[e["target"]],
                e["id"],
            ),
        ),
        "datasets": sorted(datasets, key=lambda d: (d["external_id"], d["id"])),
        "attachments": sorted(
            (_normalize_item(a, ATTACHMENT_FIELDS) for a in doc["attachments"]),
            key=lambda a: (ext_of[a["dataset"]], slug_of[a["node"]], a["id"]),
        ),
    }


def load_document(raw):
    """Validate ``raw`` and return the normalized document, or raise SchemaError."""
    errors = validate(raw)
    if errors:
        raise SchemaError(errors)
    return normalize(raw)


def dumps(doc):
    """Canonical text form (stable formatting for reviewable Git diffs)."""
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def content_hash(doc):
    return hashlib.sha256(dumps(doc).encode("utf-8")).hexdigest()


def revision_of(doc):
    """Revision token: derived from content, so hand edits in Git change it too."""
    return content_hash(doc)[:16]
