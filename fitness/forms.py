"""Django forms for curator workflows.

Forms only parse and validate input; persistence goes through operations.py and
GraphStore. Every form carries the document ``revision`` it was rendered from.
"""
from django import forms

from . import schema


def _lines(text):
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def lines_to_text(items):
    return "\n".join(items)


def references_to_text(refs):
    return "\n".join(f"{r['title']} | {r['url']}" for r in refs)


class RevisionForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)


class NodeForm(RevisionForm):
    slug = forms.CharField(
        required=False,
        max_length=80,
        help_text="Lowercase letters, digits and hyphens. Leave blank on creation to derive it from the title.",
    )
    title = forms.CharField(max_length=200)
    summary = forms.CharField(max_length=500, help_text="One or two sentences.")
    guidance = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 10}),
        help_text="Plain text. Describe how this characteristic affects suitability for particular uses.",
    )
    considerations = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="One practical limitation, strength or caution per line.",
    )
    tags = forms.CharField(required=False, help_text="Comma-separated.")
    references = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="One per line: Title | https://example.org/...",
    )

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip()
        if slug and not schema.SLUG_RE.match(slug):
            raise forms.ValidationError(
                "Use lowercase letters, digits and single hyphens (e.g. 'data-assimilation')."
            )
        return slug

    def clean_considerations(self):
        return _lines(self.cleaned_data["considerations"])

    def clean_tags(self):
        tags = [t.strip() for t in self.cleaned_data["tags"].split(",") if t.strip()]
        return list(dict.fromkeys(tags))

    def clean_references(self):
        refs = []
        for number, line in enumerate(_lines(self.cleaned_data["references"]), 1):
            title, sep, url = line.rpartition("|")
            title, url = title.strip(), url.strip()
            if not sep or not title:
                raise forms.ValidationError(f"Line {number}: use the form 'Title | URL'.")
            if not schema.is_http_url(url):
                raise forms.ValidationError(f"Line {number}: '{url}' is not a valid http(s) URL.")
            refs.append({"title": title, "url": url})
        return refs

    @classmethod
    def initial_from(cls, node):
        return {
            "slug": node["slug"],
            "title": node["title"],
            "summary": node["summary"],
            "guidance": node["guidance"],
            "considerations": lines_to_text(node["considerations"]),
            "tags": ", ".join(node["tags"]),
            "references": references_to_text(node["references"]),
        }


class RelationshipTypeForm(RevisionForm):
    key = forms.CharField(
        max_length=64,
        help_text="Machine-readable, e.g. 'uses_process'. Cannot be changed after creation.",
    )
    label = forms.CharField(max_length=100, help_text="Shown in the UI, e.g. 'uses process'.")
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="What this relationship means. It is descriptive only: it never implies applicability.",
    )

    def __init__(self, *args, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if editing:
            # Disabled fields ignore posted data, so the key cannot be altered.
            self.fields["key"].disabled = True

    def clean_key(self):
        key = self.cleaned_data["key"].strip()
        if not schema.KEY_RE.match(key):
            raise forms.ValidationError(
                "Start with a lowercase letter; use only lowercase letters, digits and underscores."
            )
        return key


class EdgeForm(RevisionForm):
    source = forms.ChoiceField()
    relationship_type = forms.ChoiceField(label="Relationship")
    target = forms.ChoiceField()
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Explanatory note"
    )

    def __init__(self, *args, doc, **kwargs):
        super().__init__(*args, **kwargs)
        nodes = [("", "---------")] + [(n["id"], n["title"]) for n in sorted(doc["fitness_nodes"], key=lambda n: n["title"].lower())]
        self.fields["source"].choices = nodes
        self.fields["target"].choices = nodes
        self.fields["relationship_type"].choices = [("", "---------")] + [
            (r["key"], r["label"]) for r in sorted(doc["relationship_types"], key=lambda r: r["label"].lower())
        ]
        for name in ("source", "target", "relationship_type"):
            self.fields[name].required = True


class DatasetForm(RevisionForm):
    external_id = forms.CharField(
        max_length=100, label="External identifier", help_text="e.g. a GDEX dataset ID such as 'd633000'."
    )
    title = forms.CharField(max_length=300)
    url = forms.URLField(required=False, assume_scheme="https")
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))

    def clean_url(self):
        url = self.cleaned_data["url"]
        if url and not schema.is_http_url(url):
            raise forms.ValidationError("Enter an http or https URL.")
        return url


class AttachmentForm(RevisionForm):
    node = forms.ChoiceField(label="Fitness node")
    scope = forms.CharField(
        required=False, max_length=300,
        help_text="Optional, e.g. 'precipitation variables' or 'before 1979'. Blank means the dataset as a whole.",
    )
    explanation = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}),
        label="Dataset-specific explanation",
        help_text="How this guidance applies (or is qualified) for this dataset.",
    )
    evidence_urls = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Evidence / reference URLs",
        help_text="One http(s) URL per line.",
    )

    def __init__(self, *args, doc, dataset_id=None, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        taken = {a["node"] for a in doc["attachments"] if a["dataset"] == dataset_id}
        self.fields["node"].choices = [("", "---------")] + [
            (n["id"], n["title"])
            for n in sorted(doc["fitness_nodes"], key=lambda n: n["title"].lower())
            if editing or n["id"] not in taken
        ]
        if editing:
            self.fields["node"].disabled = True

    def clean_evidence_urls(self):
        urls = _lines(self.cleaned_data["evidence_urls"])
        for number, url in enumerate(urls, 1):
            if not schema.is_http_url(url):
                raise forms.ValidationError(f"Line {number}: '{url}' is not a valid http(s) URL.")
        return urls
