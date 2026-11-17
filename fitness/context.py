def user(request):
    return {
        "graph_user": getattr(request, "graph_user", None),
        "site_root": getattr(request, "site_root", "/"),
    }
