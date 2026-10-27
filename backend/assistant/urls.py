from django.urls import path
from . import views

urlpatterns = [
    path('status', views.Status.as_view()),
    path('conversations', views.Conversations.as_view()),
    path('conversations/<uuid:pk>', views.ConversationDetail.as_view()),
    path('conversations/<uuid:pk>/turns', views.Questions.as_view()),
    path('conversations/<uuid:pk>/turns/<uuid:turn_id>/retry', views.Retry.as_view()),
    path('conversations/<uuid:pk>/turns/<uuid:turn_id>/cancel', views.Cancel.as_view()),
    path('conversations/<uuid:pk>/turns/<uuid:turn_id>/stream', views.Stream.as_view()),
]
