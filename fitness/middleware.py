"""Per-user routing: ``/u/<name>/...`` selects ``<FITNESS_USERS_DIR>/<name>.json``.

The prefix is stripped from ``path_info`` for URL resolution and added to the
script prefix, so ``reverse()`` and ``{% url %}`` keep producing links under
``/u/<name>/`` without any view or template knowing about users.
"""
import re
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from django.urls import get_script_prefix, set_script_prefix

from .forms import USERNAME_RE

USER_PATH_RE = re.compile(r"^/u/(?P<user>[^/]+)(?P<rest>/.*)?$")


class UserPrefixMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        base = request.META.get("SCRIPT_NAME", "").rstrip("/") + "/"
        request.site_root = base
        request.graph_user = None
        match = USER_PATH_RE.match(request.path_info)
        if not match:
            if request.path_info == "/":
                return HttpResponseRedirect(f"{base}users/")
            return self.get_response(request)

        user = match.group("user")
        if not USERNAME_RE.match(user):
            raise Http404("Unknown graph")
        if match.group("rest") is None:
            return HttpResponseRedirect(f"{base}u/{user}/")
        if not (Path(settings.FITNESS_USERS_DIR) / f"{user}.json").is_file():
            raise Http404("Unknown graph")

        request.graph_user = user
        request.path_info = match.group("rest")
        previous = get_script_prefix()
        set_script_prefix(f"{base}u/{user}/")
        try:
            return self.get_response(request)
        finally:
            set_script_prefix(previous)
