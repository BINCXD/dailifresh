from django.conf.urls import url
from django.contrib.auth.decorators import login_required
from django.urls import path, re_path

from apps.user import views

app_name = 'user'
urlpatterns = [
    path('register/', views.RegisterView.as_view(), name='register'),
    path('active/<str:token>/', views.ActiveView.as_view(), name='active'),  # 用户激活
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('', views.UserInfoView.as_view(), name='user'),
    path('order/<int:page>/', views.UserOrderView.as_view(), name='order'),
    path('address/', views.AddressView.as_view(), name='address'),
]
