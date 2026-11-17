import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, override_settings
from django.urls import get_script_prefix, reverse

from fitness import gitsync, operations, schema

from .base import TempGraphCase


class UserRoutingTests(TempGraphCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_root_redirects_to_landing(self):
        resp = self.client.get("/")
        self.assertRedirects(resp, "/users/", fetch_redirect_response=False)

    def test_landing_lists_users_and_escapes(self):
        resp = self.client.get("/users/")
        self.assertContains(resp, 'href="/u/tester/"')

    def test_user_pages_and_links_carry_prefix(self):
        resp = self.client.get("/u/tester/nodes/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'href="/u/tester/nodes/new/"')
        self.assertEqual(get_script_prefix(), "/u/tester/")  # restored to the test's value

    def test_unknown_or_bad_user_is_404(self):
        self.assertEqual(self.client.get("/u/nobody/").status_code, 404)
        self.assertEqual(self.client.get("/u/..%2fetc/").status_code, 404)
        self.assertEqual(self.client.get("/u/Bad_Name/").status_code, 404)

    def test_missing_trailing_slash_redirects(self):
        self.assertRedirects(self.client.get("/u/tester"), "/u/tester/", fetch_redirect_response=False)

    def test_unscoped_user_page_is_404(self):
        self.assertEqual(self.client.get("/nodes/").status_code, 404)

    def test_create_user_starts_blank_and_is_isolated(self):
        resp = self.client.post("/users/new/", {"username": " New-User "})
        self.assertRedirects(resp, "/u/new-user/", fetch_redirect_response=False)
        new_path = self.users_dir / "new-user.json"
        self.assertTrue(new_path.exists())
        created = json.loads(new_path.read_text())
        self.assertEqual(created, schema.empty_document())
        before = self.path.read_bytes()
        node = self.client.get("/u/new-user/nodes/")
        self.assertEqual(node.status_code, 200)
        # edit the new user's graph; tester's file must not change
        from fitness.store import GraphStore

        store = GraphStore(new_path)
        snap = store.read()
        store.update(snap.revision, lambda d: operations.create_node(
            d, dict(title="N", summary="s", guidance="g", considerations=[], tags=[], references=[])
        ))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(json.loads(new_path.read_text())["fitness_nodes"]), 1)

    def test_create_rejects_duplicates_and_bad_names(self):
        self.assertEqual(self.client.post("/users/new/", {"username": "tester"}).status_code, 200)
        self.assertEqual(self.client.post("/users/new/", {"username": "../x"}).status_code, 200)
        self.assertEqual(self.client.post("/users/new/", {"username": "a b"}).status_code, 200)
        self.assertEqual(sorted(p.name for p in self.users_dir.glob("*.json")), ["tester.json"])

    def test_create_requires_post(self):
        self.assertEqual(self.client.get("/users/new/").status_code, 405)

    def test_reverse_includes_prefix(self):
        self.assertEqual(reverse("node_list"), "/u/tester/nodes/")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class GitSyncTests(TempGraphCase):
    def setUp(self):
        super().setUp()
        root = Path(self._tmp.name)
        self.bare = root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.bare)], check=True, capture_output=True)
        # seed the remote with an initial commit, then clone it as the "pod" checkout
        seed = root / "seed"
        subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
        (seed / "README").write_text("data\n")
        _git(seed, "add", ".")
        _git(seed, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-m", "init")
        _git(seed, "push", str(self.bare), "main")
        self.repo = root / "repo"
        subprocess.run(["git", "clone", str(self.bare), str(self.repo)], check=True, capture_output=True)
        (self.repo / "users").mkdir()
        self.graph = self.repo / "users" / "tester.json"
        shutil.copy(self.path, self.graph)

    def log(self, repo=None):
        out = subprocess.run(
            ["git", "-C", str(repo or self.bare), "log", "--format=%an|%s", "main"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout.strip().splitlines()

    def test_commit_and_push_then_noop(self):
        gitsync.sync_file(self.repo, self.graph, "tester")
        self.assertEqual(self.log()[0], "tester|Update graph for tester")
        count = len(self.log())
        gitsync.sync_file(self.repo, self.graph, "tester")
        self.assertEqual(len(self.log()), count)

    def test_rejected_push_is_rebased(self):
        other = Path(self._tmp.name) / "other"
        subprocess.run(["git", "clone", str(self.bare), str(other)], check=True, capture_output=True)
        (other / "o.json").write_text("{}\n")
        _git(other, "add", ".")
        _git(other, "-c", "user.name=o", "-c", "user.email=o@x", "commit", "-m", "other")
        _git(other, "push", "origin", "main")
        gitsync.sync_file(self.repo, self.graph, "tester")
        self.assertEqual({l.split("|")[0] for l in self.log()[:2]}, {"o", "tester"})

    def test_failure_raises(self):
        shutil.rmtree(self.bare)
        with self.assertRaises(gitsync.GitSyncError):
            gitsync.sync_file(self.repo, self.graph, "tester")

    def test_failed_push_does_not_fail_save(self):
        shutil.rmtree(self.bare)
        with override_settings(FITNESS_GIT_SYNC=True, FITNESS_GIT_REPO=self.repo, FITNESS_USERS_DIR=self.repo / "users"):
            client = Client()
            resp = client.post(
                "/u/tester/nodes/new/",
                dict(revision=self.store_for(self.graph).read().revision, slug="", title="X", summary="s",
                     guidance="g", considerations="", tags="", references=""),
                follow=True,
            )
        self.assertContains(resp, "pushing to GitHub failed")
        self.assertIn("X", self.graph.read_text())

    def store_for(self, path):
        from fitness.store import GraphStore

        return GraphStore(path)
