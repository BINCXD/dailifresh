from django.urls import path

from apps.cart import views

app_name = 'cart'
urlpatterns = [
    path('', views.CartInfoView.as_view(), name='cart'),
    path('add/', views.CartAddView.as_view(), name='add'),
    path('update/', views.CartUpdateView.as_view(), name='update'),
    path('delete/', views.CartDeleteView.as_view(), name='delete'),
]
