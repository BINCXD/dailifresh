from django.urls import path

from apps.order import views

app_name = 'order'
urlpatterns = [
    path('place/', views.OrderPlaceView.as_view(), name='place'),
    path('commit/', views.OrderCommitView.as_view(), name='commit'),
    path('pay/', views.OrderPayView.as_view(), name='pay'),
    path('check/', views.CheckPayView.as_view(), name='check'),
]
