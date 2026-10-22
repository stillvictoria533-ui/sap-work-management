from django.urls import path
from . import views, connector

urlpatterns = [
    path('connector/status', connector.ConnectorStatus.as_view()),
    path('connector/pair-code', connector.PairCode.as_view()),
    path('connector/pair', connector.PairDevice.as_view()),
    path('connector/devices/<uuid:pk>/revoke', connector.RevokeDevice.as_view()),
    path('connector/queries', connector.Queries.as_view()),
    path('connector/queries/<uuid:pk>', connector.QueryDetail.as_view()),
    path('connector/agent/heartbeat', connector.Heartbeat.as_view()),
    path('connector/agent/claim', connector.Claim.as_view()),
    path('connector/agent/queries/<uuid:pk>/complete', connector.Complete.as_view()),
    path('status', views.Status.as_view()),
    path('conversations', views.Conversations.as_view()),
    path('conversations/<uuid:pk>', views.ConversationDetail.as_view()),
    path('conversations/<uuid:pk>/turns', views.Questions.as_view()),
    path('conversations/<uuid:pk>/turns/<uuid:turn_id>/retry', views.Retry.as_view()),
    path('conversations/<uuid:pk>/turns/<uuid:turn_id>/cancel', views.Cancel.as_view()),
    path('conversations/<uuid:pk>/turns/<uuid:turn_id>/stream', views.Stream.as_view()),
]
