import json
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from fitness import schema

from .base import TempGraphCase


class ViewCase(TempGraphCase):
    def setUp(self):
        super().setUp()
        self.client = Client(enforce_csrf_checks=False)

    def post(self, name, *args, data=None, follow=False):
        data = dict(data or {})
        if "revision" not in data:
            data["revision"] = self.revision()
        return self.client.post(reverse(name, args=args), data, follow=follow)

    def node_form(self, **over):
        data = dict(slug="", title="New node", summary="Short", guidance="Guidance text",
                    considerations="One\nTwo", tags="a, b, a", references="Ref | https://example.org/x")
        data.update(over)
        return data


class PageSmokeTests(ViewCase):
    def test_all_main_pages_render(self):
        era5 = self.dataset("d633000")["id"]
        node = self.node("reanalysis")["id"]
        edge = self.doc()["edges"][0]["id"]
        att = self.doc()["attachments"][0]["id"]
        pages = [
            reverse("explorer"), reverse("explorer") + f"?dataset={era5}", reverse("node_list"),
            reverse("node_list") + f"?dataset={era5}&q=assim",
            reverse("node_create"), reverse("node_detail", args=[node]), reverse("node_edit", args=[node]),
            reverse("node_delete", args=[node]), reverse("rtype_list"), reverse("rtype_create"),
            reverse("rtype_edit", args=["uses_process"]), reverse("rtype_delete", args=["uses_process"]),
            reverse("edge_list"), reverse("edge_create"), reverse("edge_edit", args=[edge]),
            reverse("edge_delete", args=[edge]), reverse("dataset_list"), reverse("dataset_create"),
            reverse("dataset_detail", args=[era5]), reverse("dataset_edit", args=[era5]),
            reverse("dataset_delete", args=[era5]), reverse("attachment_create", args=[era5]),
            reverse("attachment_edit", args=[att]), reverse("attachment_delete", args=[att]),
            reverse("import_upload"),
        ]
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_explorer_embeds_json_script_with_attachment_status(self):
        era5 = self.dataset("d633000")["id"]
        html = self.client.get(reverse("explorer") + f"?dataset={era5}").content.decode()
        payload = json.loads(re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', html, re.S).group(1))
        status = {n["slug"]: n["status"] for n in payload["nodes"]}
        self.assertEqual(status["reanalysis"], "attached")
        self.assertEqual(status["data-assimilation"], "connected")
        self.assertEqual(status["spatial-resolution"], "connected")
        self.assertEqual(status["ensemble-uncertainty"], "connected")
        self.assertNotIn("x", payload["nodes"][0])  # no simulation state
        self.assertIn("d3.v7.9.0.min.js", html)

    def test_list_and_graph_agree_on_attached_nodes(self):
        era5 = self.dataset("d633000")["id"]
        html = self.client.get(reverse("explorer") + f"?dataset={era5}").content.decode()
        payload = json.loads(re.search(r'id="graph-data" type="application/json">(.*?)</script>', html, re.S).group(1))
        graph_attached = {n["slug"] for n in payload["nodes"] if n["status"] == "attached"}
        resp = self.client.get(reverse("node_list") + f"?dataset={era5}")
        list_attached = {r["node"]["slug"] for r in resp.context["rows"] if r["status"] == "attached"}
        self.assertEqual(graph_attached, list_attached)
        self.assertEqual(graph_attached, {"reanalysis", "changing-observing-systems", "gridded-precipitation"})

    def test_unknown_dataset_param_is_ignored(self):
        self.assertEqual(self.client.get(reverse("explorer") + "?dataset=bogus").status_code, 200)

    def test_empty_graph_renders(self):
        self.path.write_text(schema.dumps(schema.empty_document()), encoding="utf-8")
        for name in ("explorer", "node_list", "edge_list", "rtype_list", "dataset_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_content_is_escaped(self):
        raw = self.raw()
        raw["fitness_nodes"][0]["title"] = '<script>alert("x")</script>'
        raw["fitness_nodes"][0]["summary"] = "</script><img src=x onerror=alert(1)>"
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        for url in (reverse("node_list"), reverse("explorer"), reverse("node_detail", args=[raw["fitness_nodes"][0]["id"]])):
            body = self.client.get(url).content.decode()
            self.assertNotIn('<script>alert("x")</script>', body)
            self.assertNotIn("<img src=x", body)

    def test_malformed_storage_shows_error_and_is_untouched(self):
        self.path.write_text("{ broken", encoding="utf-8")
        for name in ("explorer", "node_list", "dataset_list"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 500)
            self.assertContains(resp, "has not been modified", status_code=500)
        resp = self.post("node_create", data=self.node_form(revision="abc"))
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ broken")


class NodeWorkflowTests(ViewCase):
    def test_create_edit_delete_node(self):
        resp = self.post("node_create", data=self.node_form())
        self.assertEqual(resp.status_code, 302)
        node = self.node("new-node")
        self.assertEqual(node["tags"], ["a", "b"])
        self.assertEqual(node["references"], [{"title": "Ref", "url": "https://example.org/x"}])
        self.assertEqual(node["considerations"], ["One", "Two"])

        resp = self.post("node_edit", node["id"], data=self.node_form(slug="new-node", title="Renamed"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.node("new-node")["title"], "Renamed")
        self.assertEqual(self.node("new-node")["id"], node["id"])

        self.assertEqual(self.client.get(reverse("node_delete", args=[node["id"]])).status_code, 200)
        self.assertEqual(self.post("node_delete", node["id"]).status_code, 302)
        self.assertEqual([n for n in self.doc()["fitness_nodes"] if n["id"] == node["id"]], [])

    def test_delete_requires_post(self):
        node = self.node("spatial-resolution")
        self.client.get(reverse("node_delete", args=[node["id"]]))
        self.assertEqual(self.node("spatial-resolution")["id"], node["id"])

    def test_validation_messages_and_input_preserved(self):
        resp = self.post("node_create", data=self.node_form(slug="Bad Slug", title="Keep me", references="no-pipe-here"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Use lowercase letters")
        self.assertContains(resp, "Keep me")
        resp = self.post("node_create", data=self.node_form(references="X | javascript:alert(1)"))
        self.assertContains(resp, "not a valid http(s) URL")

    def test_duplicate_slug_rejected(self):
        resp = self.post("node_create", data=self.node_form(slug="reanalysis"))
        self.assertContains(resp, "already used")
        self.assertEqual(len(self.doc()["fitness_nodes"]), 8)

    def test_slug_derived_and_made_unique(self):
        self.post("node_create", data=self.node_form(title="Reanalysis"))
        self.assertEqual(sorted(n["slug"] for n in self.doc()["fitness_nodes"] if n["slug"].startswith("reanalysis")), ["reanalysis", "reanalysis-2"])

    def test_referenced_node_deletion_blocked_in_ui(self):
        node = self.node("reanalysis")
        page = self.client.get(reverse("node_delete", args=[node["id"]]))
        self.assertContains(page, "Delete or edit those edges first")
        self.assertNotContains(page, 'name="revision"')
        resp = self.post("node_delete", node["id"], follow=True)
        self.assertContains(resp, "Cannot delete")
        self.assertEqual(self.node("reanalysis")["id"], node["id"])


class ConflictTests(ViewCase):
    def test_stale_form_does_not_overwrite_and_keeps_input(self):
        node = self.node("spatial-resolution")
        stale = self.revision()
        self.post("node_edit", node["id"], data=self.node_form(slug="spatial-resolution", title="Winner"))
        resp = self.client.post(
            reverse("node_edit", args=[node["id"]]),
            self.node_form(slug="spatial-resolution", title="Loser typed this", revision=stale),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Someone else saved a change")
        self.assertContains(resp, "Loser typed this")
        self.assertContains(resp, self.revision())  # form now carries the current revision
        self.assertEqual(self.node("spatial-resolution")["title"], "Winner")

    def test_stale_delete_not_applied(self):
        edge = self.doc()["edges"][0]
        stale = self.revision()
        self.post("rtype_create", data=dict(key="newkey", label="New", description=""))
        resp = self.client.post(reverse("edge_delete", args=[edge["id"]]), {"revision": stale}, follow=True)
        self.assertContains(resp, "data changed")
        self.assertIn(edge["id"], [e["id"] for e in self.doc()["edges"]])


class RelationshipTypeWorkflowTests(ViewCase):
    def test_crud_and_immutable_key(self):
        self.post("rtype_create", data=dict(key="supports", label="supports", description="d"))
        self.assertIn("supports", [r["key"] for r in self.doc()["relationship_types"]])
        self.post("rtype_edit", "supports", data=dict(key="hacked", label="Supports!", description="new"))
        keys = {r["key"]: r for r in self.doc()["relationship_types"]}
        self.assertNotIn("hacked", keys)
        self.assertEqual(keys["supports"]["label"], "Supports!")
        self.assertEqual(self.post("rtype_delete", "supports").status_code, 302)
        self.assertNotIn("supports", [r["key"] for r in self.doc()["relationship_types"]])

    def test_duplicate_and_bad_keys_rejected(self):
        self.assertContains(self.post("rtype_create", data=dict(key="uses_process", label="x", description="")), "already exists")
        self.assertContains(self.post("rtype_create", data=dict(key="Bad Key", label="x", description="")), "lowercase")

    def test_in_use_type_cannot_be_deleted(self):
        resp = self.post("rtype_delete", "uses_process", follow=True)
        self.assertContains(resp, "Cannot delete")
        self.assertIn("uses_process", [r["key"] for r in self.doc()["relationship_types"]])


class EdgeWorkflowTests(ViewCase):
    def data(self, source, rtype, target, note=""):
        return dict(source=source, relationship_type=rtype, target=target, note=note)

    def test_create_edit_delete_edge_including_self_loop(self):
        a, b = self.node("spatial-resolution")["id"], self.node("temporal-resolution")["id"]
        self.assertEqual(self.post("edge_create", data=self.data(a, "related_to", b, "n")).status_code, 302)
        edge = next(e for e in self.doc()["edges"] if e["source"] == a and e["target"] == b)
        self.assertEqual(edge["note"], "n")
        self.post("edge_edit", edge["id"], data=self.data(a, "related_to", a, "loop now"))
        edge = next(e for e in self.doc()["edges"] if e["id"] == edge["id"])
        self.assertEqual((edge["source"], edge["target"], edge["note"]), (a, a, "loop now"))
        self.post("edge_delete", edge["id"])
        self.assertNotIn(edge["id"], [e["id"] for e in self.doc()["edges"]])

    def test_duplicate_edge_rejected(self):
        e = self.doc()["edges"][0]
        resp = self.post("edge_create", data=self.data(e["source"], e["relationship_type"], e["target"]))
        self.assertContains(resp, "already exists")
        self.assertEqual(len(self.doc()["edges"]), 11)

    def test_invalid_choices_rejected(self):
        resp = self.post("edge_create", data=self.data(self.new_uuid(), "related_to", self.node("reanalysis")["id"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Select a valid choice")

    def test_edge_does_not_attach_anything(self):
        before = self.doc()["attachments"]
        self.post("edge_create", data=self.data(self.node("reanalysis")["id"], "related_to", self.node("gridded-precipitation")["id"]))
        self.assertEqual(self.doc()["attachments"], before)


class DatasetWorkflowTests(ViewCase):
    def ds_data(self, **over):
        data = dict(external_id="d999", title="Test DS", url="https://example.org/d", description="desc")
        data.update(over)
        return data

    def test_dataset_crud(self):
        self.assertEqual(self.post("dataset_create", data=self.ds_data()).status_code, 302)
        ds = self.dataset("d999")
        self.post("dataset_edit", ds["id"], data=self.ds_data(title="Renamed"))
        self.assertEqual(self.dataset("d999")["title"], "Renamed")
        self.assertEqual(self.post("dataset_delete", ds["id"]).status_code, 302)
        self.assertEqual(len(self.doc()["datasets"]), 2)

    def test_validation(self):
        self.assertContains(self.post("dataset_create", data=self.ds_data(external_id="d633000")), "already used")
        self.assertEqual(self.post("dataset_create", data=self.ds_data(url="ftp://x.org/a")).status_code, 200)
        self.assertEqual(len(self.doc()["datasets"]), 2)

    def test_dataset_with_attachments_cannot_be_deleted(self):
        era5 = self.dataset("d633000")
        self.assertContains(self.client.get(reverse("dataset_delete", args=[era5["id"]])), "Detach them first")
        resp = self.post("dataset_delete", era5["id"], follow=True)
        self.assertContains(resp, "Cannot delete")
        self.assertEqual(len(self.doc()["datasets"]), 2)

    def test_attach_edit_detach(self):
        ds = self.post("dataset_create", data=self.ds_data()) and self.dataset("d999")
        node = self.node("model-dependence")
        resp = self.post("attachment_create", ds["id"], data=dict(
            node=node["id"], scope="before 1979", explanation="Because", evidence_urls="https://example.org/e1\nhttps://example.org/e2"))
        self.assertEqual(resp.status_code, 302)
        att = next(a for a in self.doc()["attachments"] if a["dataset"] == ds["id"])
        self.assertEqual((att["scope"], att["evidence_urls"]), ("before 1979", ["https://example.org/e1", "https://example.org/e2"]))

        page = self.client.get(reverse("dataset_detail", args=[ds["id"]])).content.decode()
        self.assertIn("Because", page)
        self.assertIn("before 1979", page)

        self.post("attachment_edit", att["id"], data=dict(node=self.new_uuid(), scope="after", explanation="Changed", evidence_urls=""))
        att2 = next(a for a in self.doc()["attachments"] if a["id"] == att["id"])
        self.assertEqual((att2["node"], att2["scope"], att2["evidence_urls"]), (node["id"], "after", []))

        self.assertEqual(self.post("attachment_delete", att["id"]).status_code, 302)
        self.assertEqual([a for a in self.doc()["attachments"] if a["dataset"] == ds["id"]], [])

    def test_duplicate_attachment_not_offered_or_accepted(self):
        era5 = self.dataset("d633000")
        node = self.node("reanalysis")
        resp = self.post("attachment_create", era5["id"], data=dict(node=node["id"], scope="", explanation="", evidence_urls=""))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Select a valid choice")

    def test_bad_evidence_url_rejected(self):
        era5 = self.dataset("d633000")
        resp = self.post("attachment_create", era5["id"], data=dict(
            node=self.node("model-dependence")["id"], scope="", explanation="", evidence_urls="javascript:x"))
        self.assertContains(resp, "not a valid http(s) URL")

    def test_detail_shows_unattached_suggestions_without_attaching(self):
        era5 = self.dataset("d633000")
        before = self.doc()["attachments"]
        resp = self.client.get(reverse("dataset_detail", args=[era5["id"]]))
        self.assertContains(resp, "Connected nodes to consider")
        self.assertContains(resp, "not attached")
        self.assertEqual(self.doc()["attachments"], before)


class ImportExportTests(ViewCase):
    def upload(self, content, name="g.json"):
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        if isinstance(content, str):
            content = content.encode()
        return self.client.post(reverse("import_upload"), {"file": SimpleUploadedFile(name, content, "application/json")})

    def test_export_is_exact_canonical_document(self):
        resp = self.client.get(reverse("export_json"))
        self.assertEqual(resp["Content-Type"].split(";")[0], "application/json")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(resp.content.decode(), self.path.read_text(encoding="utf-8"))

    def test_export_then_import_round_trip(self):
        exported = self.client.get(reverse("export_json")).content
        resp = self.upload(exported)
        self.assertContains(resp, "Validation passed")
        token = resp.context["preview"]["token"]
        apply = self.client.post(reverse("import_apply"), {"token": token, "revision": resp.context["preview"]["revision"], "confirm": "yes"})
        self.assertEqual(apply.status_code, 302)
        self.assertEqual(self.path.read_bytes(), exported)

    def test_invalid_uploads_change_nothing(self):
        before = self.path.read_bytes()
        bad = self.raw()
        bad["edges"][0]["target"] = self.new_uuid()
        for content in (b"not json", b"\xff\xfe", bad, dict(self.raw(), schema_version=2), [1, 2]):
            resp = self.upload(content)
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.context["errors"])
            self.assertIsNone(resp.context["preview"])
        self.assertContains(self.upload(dict(self.raw(), schema_version=2)), "Unsupported schema_version")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.client.post(reverse("import_upload"), {}).context["errors"], ["Choose a JSON file to validate."])

    def test_confirmed_import_replaces_document_keeps_ids_and_backs_up(self):
        original = self.path.read_bytes()
        incoming = self.raw()
        incoming["datasets"][1]["title"] = "Imported"
        resp = self.upload(incoming)
        preview = resp.context["preview"]
        self.assertTrue(any(r["changed"] for r in preview["summary"]))
        self.assertEqual(self.path.read_bytes(), original, "validation alone must not change data")
        self.client.post(reverse("import_apply"), {"token": preview["token"], "revision": preview["revision"], "confirm": "yes"})
        self.assertEqual(self.dataset("example-ens-0001")["title"], "Imported")
        self.assertEqual(self.dataset("example-ens-0001")["id"], incoming["datasets"][1]["id"])
        backups = self.store.backups()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_import_requires_confirmation_and_post(self):
        preview = self.upload(self.raw()).context["preview"]
        before = self.path.read_bytes()
        self.client.post(reverse("import_apply"), {"token": preview["token"], "revision": preview["revision"]})
        self.assertEqual(self.client.get(reverse("import_apply")).status_code, 405)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.backups(), [])

    def test_import_conflict_when_data_changed_after_validation(self):
        preview = self.upload(self.raw()).context["preview"]
        self.post("rtype_create", data=dict(key="later", label="Later", description=""))
        after_edit = self.path.read_bytes()
        resp = self.client.post(reverse("import_apply"), {"token": preview["token"], "revision": preview["revision"], "confirm": "yes"}, follow=True)
        self.assertContains(resp, "graph changed after you validated")
        self.assertEqual(self.path.read_bytes(), after_edit)

    def test_tampered_token_cannot_reach_filesystem(self):
        before = self.path.read_bytes()
        resp = self.client.post(reverse("import_apply"), {"token": "../../../etc/passwd", "revision": self.revision(), "confirm": "yes"})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(self.path.read_bytes(), before)


class CsrfTests(TempGraphCase):
    def test_mutations_require_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        before = self.path.read_bytes()
        node = self.node("spatial-resolution")["id"]
        for name, args in (("node_create", []), ("node_delete", [node]), ("import_apply", []), ("dataset_create", [])):
            resp = client.post(reverse(name, args=args), {"revision": self.revision()})
            self.assertEqual(resp.status_code, 403, name)
        self.assertEqual(self.path.read_bytes(), before)
