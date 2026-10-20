from django.urls import path

from . import views

urlpatterns = [
    path("", views.explorer, name="explorer"),
    path("nodes/", views.node_list, name="node_list"),
    path("nodes/new/", views.node_create, name="node_create"),
    path("nodes/<uuid:node_id>/", views.node_detail, name="node_detail"),
    path("nodes/<uuid:node_id>/edit/", views.node_edit, name="node_edit"),
    path("nodes/<uuid:node_id>/delete/", views.node_delete, name="node_delete"),
    path("relationship-types/", views.rtype_list, name="rtype_list"),
    path("relationship-types/new/", views.rtype_create, name="rtype_create"),
    path("relationship-types/<slug:key>/edit/", views.rtype_edit, name="rtype_edit"),
    path("relationship-types/<slug:key>/delete/", views.rtype_delete, name="rtype_delete"),
    path("edges/", views.edge_list, name="edge_list"),
    path("edges/new/", views.edge_create, name="edge_create"),
    path("edges/<uuid:edge_id>/edit/", views.edge_edit, name="edge_edit"),
    path("edges/<uuid:edge_id>/delete/", views.edge_delete, name="edge_delete"),
    path("datasets/", views.dataset_list, name="dataset_list"),
    path("datasets/new/", views.dataset_create, name="dataset_create"),
    path("datasets/<uuid:dataset_id>/", views.dataset_detail, name="dataset_detail"),
    path("datasets/<uuid:dataset_id>/edit/", views.dataset_edit, name="dataset_edit"),
    path("datasets/<uuid:dataset_id>/delete/", views.dataset_delete, name="dataset_delete"),
    path("datasets/<uuid:dataset_id>/attach/", views.attachment_create, name="attachment_create"),
    path("attachments/<uuid:attachment_id>/edit/", views.attachment_edit, name="attachment_edit"),
    path("attachments/<uuid:attachment_id>/delete/", views.attachment_delete, name="attachment_delete"),
    path("export/", views.export_json, name="export_json"),
    path("import/", views.import_upload, name="import_upload"),
    path("import/apply/", views.import_apply, name="import_apply"),
]
