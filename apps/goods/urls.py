from django.urls import path

from apps.goods import views

app_name = 'goods'
urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('detail/<int:goods_id>/', views.DetailView.as_view(), name='detail'),
    path('list/<int:type_id>/<int:page>/', views.ListView.as_view(), name='list'),  # 列表页
]
