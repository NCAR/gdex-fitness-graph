"""Views. They talk to ``GraphStore`` and ``operations``, never to the JSON file."""
import functools
import json

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import forms, operations as ops, queries, schema
from .store import ConflictError, GraphStore, StorageError


def get_store():
    return GraphStore(settings.FITNESS_GRAPH_PATH)


def storage_guard(view):
    """Show a clear error page (and never touch the file) if storage is broken."""

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except StorageError as exc:
            return render(
                request, "fitness/storage_error.html", {"error": str(exc), "details": exc.details}, status=500
            )

    return wrapper


def _url(name, *args):
    return reverse(name, args=args)


def _get(items, key, value):
    for item in items:
        if item[key] == value:
            return item
    raise Http404("Not found")


def _submit(form, mutate):
    """Run ``mutate(doc, cleaned_data)`` under the store lock.

    Returns ``(True, result)`` on success. On failure the problem is attached to
    ``form`` (so the user's input is preserved) and ``(False, None)`` is returned.
    """
    out = []

    def apply(doc):
        out.append(mutate(doc, form.cleaned_data))

    try:
        get_store().update(form.cleaned_data["revision"], apply)
    except ConflictError as exc:
        data = form.data.copy()
        data["revision"] = exc.current
        form.data = data
        form.add_error(
            None,
            "Someone else saved a change after you opened this form, so nothing was saved. "
            "Your entries are kept below. Review the current data (open it in another tab), "
            "then submit again to apply your entries on top of the latest version.",
        )
    except ops.DomainError as exc:
        form.add_error(exc.field if exc.field in form.fields else None, exc.message)
    except schema.SchemaError as exc:
        form.add_error(None, "The result failed validation and was not saved: " + " ".join(exc.errors))
    else:
        return True, (out[0] if out else None)
    return False, None


def _form_page(request, snap, build, mutate, *, title, on_success, success_message, back, **extra):
    """Shared GET/POST handling for create and edit forms."""
    if request.method == "POST":
        form = build(request.POST, snap.doc)
        if form.is_valid():
            ok, result = _submit(form, mutate)
            if ok:
                messages.success(request, success_message)
                return redirect(on_success(result))
    else:
        form = build(None, snap.doc)
    return render(
        request, "fitness/form.html", {"form": form, "title": title, "back": back, **extra}
    )


def _delete_page(request, snap, *, subject, blockers, mutate, back, after, success_message):
    """Confirmation page (GET) and POST-only deletion."""
    if request.method == "POST":
        form = forms.RevisionForm(request.POST)
        if form.is_valid():
            try:
                get_store().update(form.cleaned_data["revision"], mutate)
            except ConflictError:
                messages.error(
                    request, "The data changed while you were reviewing this deletion. Check it again."
                )
                return redirect(request.path)
            except ops.DomainError as exc:
                messages.error(request, exc.message)
                return redirect(back)
            messages.success(request, success_message)
            return redirect(after)
    return render(
        request,
        "fitness/confirm_delete.html",
        {"subject": subject, "blockers": blockers, "revision": snap.revision, "back": back},
    )


# -- explorer ----------------------------------------------------------------


def _selected_dataset(request, doc):
    dataset_id = request.GET.get("dataset", "")
    return next((d for d in doc["datasets"] if d["id"] == dataset_id), None)


@storage_guard
def explorer(request):
    snap = get_store().read()
    dataset = _selected_dataset(request, snap.doc)
    payload = queries.graph_payload(snap.doc, dataset["id"] if dataset else None, _url)
    return render(
        request,
        "fitness/explorer.html",
        {
            "graph": payload,
            "datasets": snap.doc["datasets"],
            "dataset": dataset,
            "has_nodes": bool(snap.doc["fitness_nodes"]),
            "has_rtypes": bool(snap.doc["relationship_types"]),
        },
    )


def _matches(node, q):
    q = q.lower()
    hay = [node["title"], node["summary"], node["guidance"], *node["tags"]]
    return any(q in text.lower() for text in hay)


@storage_guard
def node_list(request):
    snap = get_store().read()
    dataset = _selected_dataset(request, snap.doc)
    status = queries.node_status_map(snap.doc, dataset["id"] if dataset else None)
    attachments = queries.attachments_for_dataset(snap.doc, dataset["id"]) if dataset else {}
    q = request.GET.get("q", "").strip()
    rows = []
    for node in sorted(snap.doc["fitness_nodes"], key=lambda n: n["title"].lower()):
        if q and not _matches(node, q):
            continue
        rows.append(
            {
                "node": node,
                "status": status[node["id"]],
                "attachment": attachments.get(node["id"]),
                "edge_count": len(queries.node_edges(snap.doc, node["id"])),
            }
        )
    return render(
        request,
        "fitness/node_list.html",
        {
            "rows": rows,
            "q": q,
            "dataset": dataset,
            "datasets": snap.doc["datasets"],
            "total": len(snap.doc["fitness_nodes"]),
        },
    )


# -- fitness nodes -----------------------------------------------------------


@storage_guard
def node_detail(request, node_id):
    snap = get_store().read()
    doc = snap.doc
    node = _get(doc["fitness_nodes"], "id", str(node_id))
    nodes = queries.by_id(doc, "fitness_nodes")
    rtypes = queries.by_id(doc, "relationship_types", "key")
    datasets = queries.by_id(doc, "datasets")
    edges = [
        {"direction": d, "edge": e, "label": rtypes[e["relationship_type"]]["label"],
         "source": nodes[e["source"]], "target": nodes[e["target"]]}
        for d, e in queries.node_edges(doc, node["id"])
    ]
    attachments = [
        {"attachment": a, "dataset": datasets[a["dataset"]]}
        for a in doc["attachments"] if a["node"] == node["id"]
    ]
    return render(
        request,
        "fitness/node_detail.html",
        {"node": node, "edges": edges, "attachments": attachments, "blockers": ops.node_blockers(doc, node["id"])},
    )


@storage_guard
def node_create(request):
    snap = get_store().read()
    return _form_page(
        request, snap,
        lambda data, doc: forms.NodeForm(data, initial={"revision": snap.revision}),
        ops.create_node,
        title="New fitness node", back=_url("node_list"),
        on_success=lambda node_id: _url("node_detail", node_id),
        success_message="Fitness node created.",
    )


@storage_guard
def node_edit(request, node_id):
    snap = get_store().read()
    node = _get(snap.doc["fitness_nodes"], "id", str(node_id))
    return _form_page(
        request, snap,
        lambda data, doc: forms.NodeForm(
            data, initial={**forms.NodeForm.initial_from(node), "revision": snap.revision}
        ),
        lambda doc, d: ops.update_node(doc, node["id"], d),
        title=f"Edit “{node['title']}”", back=_url("node_detail", node["id"]),
        on_success=lambda _r: _url("node_detail", node["id"]),
        success_message="Fitness node updated.",
        delete_url=_url("node_delete", node["id"]),
    )


@storage_guard
def node_delete(request, node_id):
    snap = get_store().read()
    node = _get(snap.doc["fitness_nodes"], "id", str(node_id))
    return _delete_page(
        request, snap, subject=f"fitness node “{node['title']}”",
        blockers=ops.node_blockers(snap.doc, node["id"]),
        mutate=lambda doc: ops.delete_node(doc, node["id"]),
        back=_url("node_detail", node["id"]), after=_url("node_list"),
        success_message="Fitness node deleted.",
    )


# -- relationship types ------------------------------------------------------


@storage_guard
def rtype_list(request):
    snap = get_store().read()
    uses = {}
    for e in snap.doc["edges"]:
        uses[e["relationship_type"]] = uses.get(e["relationship_type"], 0) + 1
    rows = [{"rtype": r, "uses": uses.get(r["key"], 0)} for r in snap.doc["relationship_types"]]
    return render(request, "fitness/rtype_list.html", {"rows": rows})


@storage_guard
def rtype_create(request):
    snap = get_store().read()
    return _form_page(
        request, snap,
        lambda data, doc: forms.RelationshipTypeForm(data, initial={"revision": snap.revision}),
        ops.create_rtype,
        title="New relationship type", back=_url("rtype_list"),
        on_success=lambda _r: _url("rtype_list"), success_message="Relationship type created.",
    )


@storage_guard
def rtype_edit(request, key):
    snap = get_store().read()
    rtype = _get(snap.doc["relationship_types"], "key", key)
    return _form_page(
        request, snap,
        lambda data, doc: forms.RelationshipTypeForm(
            data, editing=True, initial={**rtype, "revision": snap.revision}
        ),
        lambda doc, d: ops.update_rtype(doc, key, d),
        title=f"Edit relationship type “{rtype['label']}”", back=_url("rtype_list"),
        on_success=lambda _r: _url("rtype_list"), success_message="Relationship type updated.",
        delete_url=_url("rtype_delete", key),
    )


@storage_guard
def rtype_delete(request, key):
    snap = get_store().read()
    rtype = _get(snap.doc["relationship_types"], "key", key)
    return _delete_page(
        request, snap, subject=f"relationship type “{rtype['label']}”",
        blockers=ops.rtype_blockers(snap.doc, key),
        mutate=lambda doc: ops.delete_rtype(doc, key),
        back=_url("rtype_list"), after=_url("rtype_list"),
        success_message="Relationship type deleted.",
    )


# -- edges -------------------------------------------------------------------


@storage_guard
def edge_list(request):
    snap = get_store().read()
    nodes = queries.by_id(snap.doc, "fitness_nodes")
    rtypes = queries.by_id(snap.doc, "relationship_types", "key")
    rows = [
        {"edge": e, "source": nodes[e["source"]], "target": nodes[e["target"]],
         "label": rtypes[e["relationship_type"]]["label"]}
        for e in snap.doc["edges"]
    ]
    rows.sort(key=lambda r: (r["source"]["title"].lower(), r["label"].lower(), r["target"]["title"].lower()))
    return render(request, "fitness/edge_list.html", {"rows": rows})


@storage_guard
def edge_create(request):
    snap = get_store().read()
    initial = {"revision": snap.revision}
    for field in ("source", "target", "relationship_type"):
        if request.GET.get(field):
            initial[field] = request.GET[field]
    return _form_page(
        request, snap,
        lambda data, doc: forms.EdgeForm(data, doc=doc, initial=initial),
        ops.create_edge,
        title="New relationship", back=_url("edge_list"),
        on_success=lambda _r: _url("edge_list"), success_message="Relationship created.",
        note="Relationships are descriptive only. They never make the target apply to a dataset.",
    )


@storage_guard
def edge_edit(request, edge_id):
    snap = get_store().read()
    edge = _get(snap.doc["edges"], "id", str(edge_id))
    return _form_page(
        request, snap,
        lambda data, doc: forms.EdgeForm(data, doc=doc, initial={**edge, "revision": snap.revision}),
        lambda doc, d: ops.update_edge(doc, edge["id"], d),
        title="Edit relationship", back=_url("edge_list"),
        on_success=lambda _r: _url("edge_list"), success_message="Relationship updated.",
        delete_url=_url("edge_delete", edge["id"]),
    )


@storage_guard
def edge_delete(request, edge_id):
    snap = get_store().read()
    edge = _get(snap.doc["edges"], "id", str(edge_id))
    nodes = queries.by_id(snap.doc, "fitness_nodes")
    label = queries.by_id(snap.doc, "relationship_types", "key")[edge["relationship_type"]]["label"]
    return _delete_page(
        request, snap,
        subject=f"relationship “{nodes[edge['source']]['title']} → {label} → {nodes[edge['target']]['title']}”",
        blockers=[], mutate=lambda doc: ops.delete_edge(doc, edge["id"]),
        back=_url("edge_list"), after=_url("edge_list"), success_message="Relationship deleted.",
    )


# -- datasets and attachments -----------------------------------------------


@storage_guard
def dataset_list(request):
    snap = get_store().read()
    counts = {}
    for a in snap.doc["attachments"]:
        counts[a["dataset"]] = counts.get(a["dataset"], 0) + 1
    rows = [{"dataset": d, "count": counts.get(d["id"], 0)} for d in snap.doc["datasets"]]
    return render(request, "fitness/dataset_list.html", {"rows": rows})


@storage_guard
def dataset_detail(request, dataset_id):
    snap = get_store().read()
    doc = snap.doc
    dataset = _get(doc["datasets"], "id", str(dataset_id))
    nodes = queries.by_id(doc, "fitness_nodes")
    attached = sorted(
        ({"attachment": a, "node": nodes[a["node"]]} for a in doc["attachments"] if a["dataset"] == dataset["id"]),
        key=lambda r: r["node"]["title"].lower(),
    )
    return render(
        request,
        "fitness/dataset_detail.html",
        {
            "dataset": dataset,
            "attached": attached,
            "suggestions": queries.suggestions(doc, dataset["id"]),
            "revision": snap.revision,
            "can_attach": len(attached) < len(nodes),
        },
    )


@storage_guard
def dataset_create(request):
    snap = get_store().read()
    return _form_page(
        request, snap,
        lambda data, doc: forms.DatasetForm(data, initial={"revision": snap.revision}),
        ops.create_dataset,
        title="New dataset", back=_url("dataset_list"),
        on_success=lambda dataset_id: _url("dataset_detail", dataset_id),
        success_message="Dataset created.",
    )


@storage_guard
def dataset_edit(request, dataset_id):
    snap = get_store().read()
    dataset = _get(snap.doc["datasets"], "id", str(dataset_id))
    return _form_page(
        request, snap,
        lambda data, doc: forms.DatasetForm(data, initial={**dataset, "revision": snap.revision}),
        lambda doc, d: ops.update_dataset(doc, dataset["id"], d),
        title=f"Edit dataset “{dataset['title']}”", back=_url("dataset_detail", dataset["id"]),
        on_success=lambda _r: _url("dataset_detail", dataset["id"]),
        success_message="Dataset updated.",
        delete_url=_url("dataset_delete", dataset["id"]),
    )


@storage_guard
def dataset_delete(request, dataset_id):
    snap = get_store().read()
    dataset = _get(snap.doc["datasets"], "id", str(dataset_id))
    return _delete_page(
        request, snap, subject=f"dataset “{dataset['title']}”",
        blockers=ops.dataset_blockers(snap.doc, dataset["id"]),
        mutate=lambda doc: ops.delete_dataset(doc, dataset["id"]),
        back=_url("dataset_detail", dataset["id"]), after=_url("dataset_list"),
        success_message="Dataset deleted.",
    )


@storage_guard
def attachment_create(request, dataset_id):
    snap = get_store().read()
    dataset = _get(snap.doc["datasets"], "id", str(dataset_id))
    initial = {"revision": snap.revision, "node": request.GET.get("node", "")}
    return _form_page(
        request, snap,
        lambda data, doc: forms.AttachmentForm(data, doc=doc, dataset_id=dataset["id"], initial=initial),
        lambda doc, d: ops.create_attachment(doc, dataset["id"], d),
        title=f"Attach a fitness node to “{dataset['title']}”",
        back=_url("dataset_detail", dataset["id"]),
        on_success=lambda _r: _url("dataset_detail", dataset["id"]),
        success_message="Fitness node attached.",
        note="Attaching is an explicit curator decision. Connected nodes are never attached automatically.",
    )


@storage_guard
def attachment_edit(request, attachment_id):
    snap = get_store().read()
    att = _get(snap.doc["attachments"], "id", str(attachment_id))
    node = queries.by_id(snap.doc, "fitness_nodes")[att["node"]]
    dataset = queries.by_id(snap.doc, "datasets")[att["dataset"]]
    initial = {
        "revision": snap.revision, "node": att["node"], "scope": att["scope"],
        "explanation": att["explanation"], "evidence_urls": forms.lines_to_text(att["evidence_urls"]),
    }
    return _form_page(
        request, snap,
        lambda data, doc: forms.AttachmentForm(
            data, doc=doc, dataset_id=dataset["id"], editing=True, initial=initial
        ),
        lambda doc, d: ops.update_attachment(doc, att["id"], d),
        title=f"Edit “{node['title']}” on “{dataset['title']}”",
        back=_url("dataset_detail", dataset["id"]),
        on_success=lambda _r: _url("dataset_detail", dataset["id"]),
        success_message="Attachment updated.",
        delete_url=_url("attachment_delete", att["id"]),
    )


@storage_guard
def attachment_delete(request, attachment_id):
    snap = get_store().read()
    att = _get(snap.doc["attachments"], "id", str(attachment_id))
    node = queries.by_id(snap.doc, "fitness_nodes")[att["node"]]
    dataset = queries.by_id(snap.doc, "datasets")[att["dataset"]]
    return _delete_page(
        request, snap, subject=f"the attachment of “{node['title']}” to “{dataset['title']}” (detach)",
        blockers=[], mutate=lambda doc: ops.delete_attachment(doc, att["id"]),
        back=_url("dataset_detail", dataset["id"]), after=_url("dataset_detail", dataset["id"]),
        success_message="Fitness node detached.",
    )


# -- import / export ---------------------------------------------------------


@storage_guard
def export_json(request):
    snap = get_store().read()
    response = HttpResponse(schema.dumps(snap.doc), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="fitness_graph.json"'
    return response


def _import_summary(current, new):
    rows = []
    for coll, singular, _ in schema.COLLECTIONS:
        key = "key" if coll == "relationship_types" else "id"
        old = {i[key]: i for i in current[coll]}
        incoming = {i[key]: i for i in new[coll]}
        rows.append(
            {
                "name": coll.replace("_", " "),
                "current": len(old),
                "incoming": len(incoming),
                "added": len(incoming.keys() - old.keys()),
                "removed": len(old.keys() - incoming.keys()),
                "changed": sum(1 for k in incoming.keys() & old.keys() if incoming[k] != old[k]),
            }
        )
    return rows


@storage_guard
def import_upload(request):
    store = get_store()
    snap = store.read()
    context = {"errors": [], "preview": None}
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            context["errors"] = ["Choose a JSON file to validate."]
        elif upload.size > settings.FITNESS_IMPORT_MAX_BYTES:
            context["errors"] = [f"The file is larger than {settings.FITNESS_IMPORT_MAX_BYTES // 1024} KiB."]
        else:
            try:
                raw = json.loads(upload.read().decode("utf-8"))
                new_doc = schema.load_document(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                context["errors"] = [f"The file is not valid UTF-8 JSON: {exc}"]
            except schema.SchemaError as exc:
                context["errors"] = exc.errors
            else:
                context["preview"] = {
                    "token": store.stage_import(new_doc),
                    "revision": snap.revision,
                    "filename": upload.name,
                    "summary": _import_summary(snap.doc, new_doc),
                }
    return render(request, "fitness/import.html", context)


@require_POST
@storage_guard
def import_apply(request):
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Import not applied: it was not confirmed.")
        return redirect("import_upload")
    try:
        get_store().apply_import(request.POST.get("revision", ""), request.POST.get("token", ""))
    except ConflictError:
        messages.error(
            request,
            "The graph changed after you validated this file, so the import was not applied. "
            "Upload the file again to validate it against the current data.",
        )
        return redirect("import_upload")
    except schema.SchemaError as exc:
        messages.error(request, "The import failed validation and was not applied: " + " ".join(exc.errors))
        return redirect("import_upload")
    messages.success(request, "Import applied. The previous document was kept as a backup in .fitness-local/backups/.")
    return redirect("explorer")
