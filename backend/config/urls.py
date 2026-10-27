from django.urls import include, path
from desk import views as v
from desk import scheduling_views as sv

urlpatterns = [
    path('api/assistant/', include('assistant.urls')),
    path('api/health', v.health), path('api/auth/session', v.session), path('api/auth/login', v.sign_in),
    path('api/auth/logout', v.SignOut.as_view()), path('api/auth/password', v.Password.as_view()),
    path('api/tickets', v.TicketList.as_view()), path('api/tickets/export', v.Export.as_view()),
    path('api/scheduling/queue', sv.DevelopmentQueue.as_view()),
    path('api/scheduling/preview', sv.BatchPreview.as_view()),
    path('api/scheduling/commit', sv.BatchCommit.as_view()),
    path('api/expedite-requests', sv.ExpediteRequests.as_view()),
    path('api/expedite-requests/<int:pk>', sv.ExpediteDetail.as_view()),
    path('api/expedite-requests/<int:pk>/decision', sv.ExpediteDecision.as_view()),
    path('api/tickets/<int:pk>/expedite', sv.TicketExpedite.as_view()),
    path('api/calendar', v.DevelopmentCalendar.as_view()),
    path('api/tickets/<int:pk>', v.TicketDetail.as_view()), path('api/tickets/<int:pk>/reply', v.TicketReply.as_view()),
    path('api/tickets/<int:pk>/status', v.TicketStatus.as_view()),
    path('api/tickets/<int:pk>/schedule', v.TicketSchedule.as_view()),
    path('api/tickets/<int:pk>/version', v.TicketVersion.as_view()),
    path('api/tickets/<int:pk>/read', v.TicketRead.as_view()), path('api/attachments/<int:pk>/download', v.Download.as_view()),
    path('api/stats', v.Stats.as_view()), path('api/notifications', v.Notices.as_view()),
    path('api/accounts', v.Accounts.as_view()), path('api/accounts/<int:pk>', v.AccountDetail.as_view()),
    path('api/audit', v.AuditList.as_view()), path('api/deliveries', v.Deliveries.as_view()),
    path('api/deliveries/<int:pk>/retry', v.RetryDelivery.as_view()),
]
