import json
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from fitness import schema
from fitness.store import GraphStore

EXAMPLE = Path(settings.BASE_DIR) / "data" / "fitness_graph.json"


class TempGraphCase(SimpleTestCase):
    """Runs every test against a private copy of the example data."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "graph.json"
        shutil.copy(EXAMPLE, self.path)
        self._example_before = EXAMPLE.read_bytes()
        override = override_settings(FITNESS_GRAPH_PATH=self.path)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(self._example_untouched)
        self.store = GraphStore(self.path)

    def _example_untouched(self):
        self.assertEqual(EXAMPLE.read_bytes(), self._example_before, "committed example data was modified")

    # helpers
    def doc(self):
        return self.store.read().doc

    def revision(self):
        return self.store.read().revision

    def node(self, slug):
        return next(n for n in self.doc()["fitness_nodes"] if n["slug"] == slug)

    def dataset(self, ext):
        return next(d for d in self.doc()["datasets"] if d["external_id"] == ext)

    def raw(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def new_uuid():
        import uuid
        return str(uuid.uuid4())
