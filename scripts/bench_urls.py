"""A fixed URL target for the builtin url tag benchmark."""

from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path("example/", lambda request: HttpResponse(""), name="benchmark-example")
]
