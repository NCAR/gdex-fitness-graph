import copy
import json
import threading

from fitness import operations as ops, queries, schema
from fitness.store import ConflictError, GraphStore, StorageError

from .base import EXAMPLE, TempGraphCase


class ExampleDataTests(TempGraphCase):
    def test_example_is_valid_and_canonical(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        doc = schema.load_document(json.loads(text))
        self.assertEqual(schema.dumps(doc), text, "example file is not in canonical form")
        self.assertEqual(len(doc["fitness_nodes"]), 8)
        self.assertGreaterEqual(len(doc["edges"]), 8)
        self.assertEqual(len(doc["datasets"]), 2)

    def test_example_demonstrates_unattached_connected_node(self):
        doc = self.doc()
        era5 = self.dataset("d633000")
        status = queries.node_status_map(doc, era5["id"])
        self.assertEqual(status[self.node("reanalysis")["id"]], queries.ATTACHED)
        self.assertEqual(status[self.node("data-assimilation")["id"]], queries.CONNECTED)
        scopes = [a["scope"] for a in doc["attachments"] if a["dataset"] == era5["id"]]
        self.assertGreaterEqual(len([s for s in scopes if s]), 2)


class RoundTripTests(TempGraphCase):
    def test_round_trip_preserves_ids_and_content(self):
        before = self.doc()
        self.store.update(self.revision(), lambda d: None)  # rewrite unchanged
        self.assertEqual(self.doc(), before)
        reloaded = schema.load_document(json.loads(schema.dumps(before)))
        self.assertEqual(reloaded, before)

    def test_edit_keeps_ids_and_output_is_deterministic(self):
        node = self.node("reanalysis")
        data = {k: node[k] for k in ("title", "summary", "guidance", "considerations", "tags", "references")}
        data["title"] = "Reanalysis (edited)"
        self.store.update(self.revision(), lambda d: ops.update_node(d, node["id"], data))
        after = self.node("reanalysis")
        self.assertEqual(after["id"], node["id"])
        self.assertEqual(after["title"], "Reanalysis (edited)")
        text = self.path.read_text(encoding="utf-8")
        shuffled = self.raw()
        shuffled["fitness_nodes"].reverse()
        shuffled["edges"].reverse()
        self.assertEqual(schema.dumps(schema.load_document(shuffled)), text)

    def test_revision_is_content_based(self):
        doc = self.doc()
        self.assertEqual(schema.revision_of(doc), self.revision())
        other = copy.deepcopy(doc)
        other["datasets"][0]["title"] += "!"
        self.assertNotEqual(schema.revision_of(other), self.revision())


class ValidationTests(TempGraphCase):
    def errors(self, mutate):
        doc = self.raw()
        mutate(doc)
        return schema.validate(doc)

    def assertRejected(self, mutate, fragment):
        errs = self.errors(mutate)
        self.assertTrue(any(fragment in e for e in errs), errs)

    def test_dangling_references(self):
        self.assertRejected(lambda d: d["edges"][0].update(target=self.new_uuid()), "is not a fitness node")
        self.assertRejected(lambda d: d["edges"][0].update(relationship_type="nope"), "unknown relationship type")
        self.assertRejected(lambda d: d["attachments"][0].update(dataset=self.new_uuid()), "dataset")
        self.assertRejected(lambda d: d["attachments"][0].update(node=self.new_uuid()), "fitness node")

    def test_duplicates(self):
        self.assertRejected(lambda d: d["fitness_nodes"][1].update(slug=d["fitness_nodes"][0]["slug"]), "Duplicate slug")
        self.assertRejected(lambda d: d["fitness_nodes"][1].update(id=d["fitness_nodes"][0]["id"]), "Duplicate ID")
        self.assertRejected(lambda d: d["edges"].append(dict(d["edges"][0], id=self.new_uuid(), note="x")), "Duplicate edge")
        self.assertRejected(lambda d: d["attachments"].append(dict(d["attachments"][0], id=self.new_uuid())), "Duplicate attachment")
        self.assertRejected(lambda d: d["relationship_types"].append(dict(d["relationship_types"][0])), "relationship type key")

    def test_same_endpoints_with_different_type_is_not_duplicate(self):
        def add(d):
            e = d["edges"][0]
            d["edges"].append(dict(e, id=self.new_uuid(), relationship_type="related_to"))
        self.assertEqual(self.errors(add), [])

    def test_cycles_and_self_loops_allowed_and_traversal_terminates(self):
        doc = self.raw()
        a = self.node("reanalysis")["id"]
        b = self.node("data-assimilation")["id"]
        doc["edges"] += [
            dict(id=self.new_uuid(), source=a, relationship_type="related_to", target=a, note=""),
            dict(id=self.new_uuid(), source=b, relationship_type="related_to", target=a, note=""),
        ]
        loaded = schema.load_document(doc)
        self.assertIn(b, queries.neighborhood(loaded, a, depth=10))
        self.assertNotIn(a, queries.neighbor_ids(loaded, a))

    def test_urls_must_be_http_or_https(self):
        for bad in ("javascript:alert(1)", "ftp://x.org/f", "file:///etc/passwd", "http://", "not a url"):
            self.assertRejected(lambda d, bad=bad: d["datasets"][0].update(url=bad), "http(s) URL")
            self.assertRejected(lambda d, bad=bad: d["fitness_nodes"][0].update(references=[{"title": "t", "url": bad}]), "http(s) URL")
            self.assertRejected(lambda d, bad=bad: d["attachments"][0].update(evidence_urls=[bad]), "http(s) URL")

    def test_unsupported_schema_version(self):
        for version in (2, 0, "1", None):
            errs = self.errors(lambda d, v=version: d.update(schema_version=v))
            self.assertEqual(len(errs), 1)
            self.assertIn("Unsupported schema_version", errs[0])

    def test_unknown_fields_and_wrong_types_rejected(self):
        self.assertRejected(lambda d: d.update(extra=1), "Unknown top-level key")
        self.assertRejected(lambda d: d["fitness_nodes"][0].update(sneaky="x"), "unknown field")
        self.assertRejected(lambda d: d["fitness_nodes"][0].update(tags="a,b"), "list")
        self.assertRejected(lambda d: d.pop("edges"), "'edges' must be a list")

    def test_invalid_update_is_not_saved(self):
        before = self.path.read_bytes()
        def bad(doc):
            doc["edges"][0]["target"] = self.new_uuid()
        with self.assertRaises(schema.SchemaError):
            self.store.update(self.revision(), bad)
        self.assertEqual(self.path.read_bytes(), before)


class DeletionTests(TempGraphCase):
    def run_op(self, fn):
        return self.store.update(self.revision(), fn)

    def test_referenced_node_cannot_be_deleted(self):
        node = self.node("reanalysis")
        with self.assertRaisesMessage(ops.DomainError, "relationship"):
            self.run_op(lambda d: ops.delete_node(d, node["id"]))
        self.assertEqual(self.node("reanalysis")["id"], node["id"])

    def test_attached_node_cannot_be_deleted_with_explanation(self):
        doc = self.raw()
        node = self.node("ensemble-uncertainty")
        doc["edges"] = [e for e in doc["edges"] if node["id"] not in (e["source"], e["target"])]
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesMessage(ops.DomainError, "Detach it from those datasets first"):
            self.run_op(lambda d: ops.delete_node(d, node["id"]))

    def test_referenced_relationship_type_cannot_be_deleted(self):
        with self.assertRaisesMessage(ops.DomainError, "Delete or change those edges first"):
            self.run_op(lambda d: ops.delete_rtype(d, "uses_process"))

    def test_unreferenced_relationship_type_can_be_deleted(self):
        self.run_op(lambda d: ops.delete_rtype(d, "is_a_type_of"))
        self.assertNotIn("is_a_type_of", [r["key"] for r in self.doc()["relationship_types"]])

    def test_dataset_with_attachments_cannot_be_deleted(self):
        era5 = self.dataset("d633000")
        with self.assertRaisesMessage(ops.DomainError, "Detach them first"):
            self.run_op(lambda d: ops.delete_dataset(d, era5["id"]))

    def test_dataset_deletable_after_detaching(self):
        era5 = self.dataset("d633000")
        ids = [a["id"] for a in self.doc()["attachments"] if a["dataset"] == era5["id"]]
        for att_id in ids:
            self.run_op(lambda d, i=att_id: ops.delete_attachment(d, i))
        self.run_op(lambda d: ops.delete_dataset(d, era5["id"]))
        self.assertEqual(len(self.doc()["datasets"]), 1)


class AttachmentSemanticsTests(TempGraphCase):
    def test_attaching_does_not_attach_or_inherit_neighbours(self):
        era5 = self.dataset("d633000")
        da = self.node("data-assimilation")
        before = {a["node"] for a in self.doc()["attachments"] if a["dataset"] == era5["id"]}
        self.assertNotIn(da["id"], before)
        # attach the unattached neighbour only
        self.store.update(self.revision(), lambda d: ops.create_attachment(
            d, era5["id"], {"node": da["id"], "scope": "", "explanation": "", "evidence_urls": []}))
        after = {a["node"] for a in self.doc()["attachments"] if a["dataset"] == era5["id"]}
        self.assertEqual(after, before | {da["id"]})
        # nodes connected to data-assimilation (e.g. changing observing systems) were already
        # attached explicitly; an unattached neighbour of data-assimilation stays unattached
        status = queries.node_status_map(self.doc(), era5["id"])
        for slug in ("model-dependence", "spatial-resolution", "temporal-resolution"):
            self.assertNotEqual(status[self.node(slug)["id"]], queries.ATTACHED)

    def test_status_is_per_dataset_and_connected_is_one_hop(self):
        doc = self.doc()
        ens = self.dataset("example-ens-0001")
        status = queries.node_status_map(doc, ens["id"])
        self.assertEqual(status[self.node("ensemble-uncertainty")["id"]], queries.ATTACHED)
        self.assertEqual(status[self.node("reanalysis")["id"]], queries.CONNECTED)  # linked to ensemble-uncertainty
        self.assertEqual(status[self.node("data-assimilation")["id"]], queries.OTHER)  # two hops away
        self.assertTrue(all(v == queries.OTHER for v in queries.node_status_map(doc, None).values()))

    def test_duplicate_attachment_rejected(self):
        era5 = self.dataset("d633000")
        node = self.node("reanalysis")
        with self.assertRaisesMessage(ops.DomainError, "already attached"):
            self.store.update(self.revision(), lambda d: ops.create_attachment(
                d, era5["id"], {"node": node["id"], "scope": "", "explanation": "", "evidence_urls": []}))

    def test_suggestions_exclude_attached_nodes(self):
        era5 = self.dataset("d633000")
        suggested = {s["node"]["slug"] for s in queries.suggestions(self.doc(), era5["id"])}
        self.assertIn("data-assimilation", suggested)
        self.assertNotIn("reanalysis", suggested)


class ConcurrencyAndSafetyTests(TempGraphCase):
    def test_stale_revision_cannot_overwrite(self):
        stale = self.revision()
        era5 = self.dataset("d633000")
        data = {"external_id": "d633000", "title": "First edit", "url": "", "description": ""}
        self.store.update(stale, lambda d: ops.update_dataset(d, era5["id"], data))
        data2 = dict(data, title="Second (stale) edit")
        with self.assertRaises(ConflictError):
            self.store.update(stale, lambda d: ops.update_dataset(d, era5["id"], data2))
        self.assertEqual(self.dataset("d633000")["title"], "First edit")

    def test_concurrent_writers_with_same_revision_only_one_wins(self):
        rev = self.revision()
        results = []

        def attempt(i):
            try:
                self.store.update(rev, lambda d: ops.create_rtype(
                    d, {"key": f"k{i}", "label": f"K{i}", "description": ""}))
                results.append("ok")
            except ConflictError:
                results.append("conflict")

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), ["conflict"] * 5 + ["ok"])

    def test_malformed_json_is_not_overwritten(self):
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.read()
        with self.assertRaises(StorageError):
            self.store.update("anything", lambda d: None)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ not json")

    def test_invalid_document_is_not_overwritten(self):
        bad = self.raw()
        bad["edges"][0]["target"] = self.new_uuid()
        self.path.write_text(json.dumps(bad), encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(StorageError) as ctx:
            self.store.update("x", lambda d: None)
        self.assertTrue(ctx.exception.details)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_file_is_empty_document_and_created_on_first_write(self):
        path = self.path.parent / "new" / "g.json"
        store = GraphStore(path)
        snap = store.read()
        self.assertEqual(snap.doc["fitness_nodes"], [])
        store.update(snap.revision, lambda d: ops.create_rtype(d, {"key": "a", "label": "A", "description": ""}))
        self.assertTrue(path.exists())

    def test_failed_mutation_leaves_no_temp_files(self):
        with self.assertRaises(ops.DomainError):
            self.store.update(self.revision(), lambda d: ops.delete_rtype(d, "uses_process"))
        leftovers = [p.name for p in self.path.parent.iterdir() if p.name.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_state_files_live_in_gitignored_directory(self):
        self.store.update(self.revision(), lambda d: None)
        self.assertEqual(self.store.state_dir.name, ".fitness-local")
        from django.conf import settings
        ignore = (settings.BASE_DIR / ".gitignore").read_text()
        self.assertIn(".fitness-local/", ignore)


class ImportStoreTests(TempGraphCase):
    def test_confirmed_import_preserves_ids_and_backs_up_previous(self):
        original = self.path.read_bytes()
        incoming = self.raw()
        incoming["datasets"][0]["title"] = "Imported title"
        new_doc = schema.load_document(incoming)
        token = self.store.stage_import(new_doc)
        snap = self.store.apply_import(self.revision(), token)
        self.assertEqual(snap.doc, new_doc)
        self.assertEqual(self.doc(), new_doc)
        self.assertEqual({d["id"] for d in self.doc()["datasets"]}, {d["id"] for d in new_doc["datasets"]})
        backups = self.store.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_import_with_stale_revision_conflicts(self):
        token = self.store.stage_import(self.doc())
        stale = self.revision()
        self.store.update(stale, lambda d: ops.create_rtype(d, {"key": "zz", "label": "ZZ", "description": ""}))
        with self.assertRaises(ConflictError):
            self.store.apply_import(stale, token)
        self.assertEqual(self.store.backups(), [])

    def test_staged_import_must_match_its_hash_and_token_is_not_a_path(self):
        token = self.store.stage_import(self.doc())
        (self.store.state_dir / "imports" / f"{token}.json").write_text(
            json.dumps(dict(self.raw(), datasets=[])), encoding="utf-8")
        with self.assertRaises(StorageError):
            self.store.load_staged(token)
        for bad in ("../../etc/passwd", "x" * 64, "", None):
            with self.assertRaises(StorageError):
                self.store.load_staged(bad)
