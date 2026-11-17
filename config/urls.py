from django.urls import include, path

from fitness import views

# User-scoped pages live in fitness.urls and are reached as /u/<name>/... (see
# fitness.middleware); the site root only offers choosing or creating a graph.
urlpatterns = [
    path("users/", views.landing, name="landing"),
    path("users/new/", views.user_create, name="user_create"),
    path("", include("fitness.urls")),
]
