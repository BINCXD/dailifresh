from django.http import JsonResponse
from django.shortcuts import render

# Create your views here.
from django.views import View
from django_redis import get_redis_connection

from apps.goods.models import GoodsSKU
from utils.mixin import LoginRequiredMixin


class CartAddView(View):
    def post(self, request):
        # 获取user
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '请先登录'})

        sku_id = request.POST.get('sku_id')
        count = request.POST.get('count')

        if not all([sku_id, count]):
            return JsonResponse({'res': 1, 'errmsg': '数据不完整'})

        try:
            count = int(count)
        except Exception:
            return JsonResponse({'res': 2, 'errmsg': '商品数量不对'})

        try:
            sku = GoodsSKU.objects.get(id=sku_id)
        except GoodsSKU.DoesNotExist:
            return JsonResponse({'res': 3, 'errmsg': '商品不存在'})

        # 业务处理添加购物车记录
        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id

        cart_count = conn.hget(cart_key, sku_id)
        if cart_count:
            count += int(cart_count)

        if count > sku.stock:
            return JsonResponse({'res': 4, 'errmsg': '商品库存不足'})

        conn.hset(cart_key, sku_id, count)
        # 购物车条目数
        total_count = conn.hlen(cart_key)
        return JsonResponse({'res': 5, 'total_count': total_count, 'message': '添加成功'})


class CartInfoView(LoginRequiredMixin, View):
    """购物车显示"""

    def get(self, request):
        user = request.user
        # 获取用户购物车的商品信息
        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id
        # {'商品id': 商品数量}
        cart_dict = conn.hgetall(cart_key)

        skus = []
        # 保存用户购物车中商品总数目和总价格
        total_count = 0
        total_price = 0
        # 遍历获取商品信息
        for sku_id, count in cart_dict.items():
            # 根据商品id获取商品信息
            sku = GoodsSKU.objects.get(id=sku_id)
            # 计算商品的小计
            amount = sku.price * int(count)
            # 动态给sku对象添加一个属性amount,保存商品小计
            sku.amount = amount
            # 动态给sku对象添加一个属性,保存购物车中对应商品的数量
            sku.count = int(count)

            # 添加
            skus.append(sku)

            # 累加计算商品的总数目和总价格
            total_count += int(count)
            total_price += amount

        # 组织上下文
        context = {'total_count': total_count,
                   'total_price': total_price,
                   'skus': skus}

        return render(request, 'df_cart/cart.html', context)


class CartUpdateView(View):
    def post(self, request):
        # 获取user
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '请先登录'})

        sku_id = request.POST.get('sku_id')
        count = request.POST.get('count')

        if not all([sku_id, count]):
            return JsonResponse({'res': 1, 'errmsg': '数据不完整'})

        try:
            count = int(count)
        except Exception:
            return JsonResponse({'res': 2, 'errmsg': '商品数量不对'})

        try:
            sku = GoodsSKU.objects.get(id=sku_id)
        except GoodsSKU.DoesNotExist:
            return JsonResponse({'res': 3, 'errmsg': '商品不存在'})

        # 业务处理添加购物车记录
        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id

        if count > sku.stock:
            return JsonResponse({'res': 4, 'errmsg': '商品库存不足'})

        # 更新
        conn.hset(cart_key, sku_id, count)

        # 计算用户购物车中商品的总件数{'1':5,'2':3}
        total_count = 0
        vals = conn.hvals(cart_key)
        for val in vals:
            total_count += int(val)

        # 返回应答
        return JsonResponse({'res': 5, 'total_count': total_count, 'message': '更新成功'})


class CartDeleteView(View):
    def post(self, request):
        # 获取user
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '请先登录'})

        sku_id = request.POST.get('sku_id')

        if not sku_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的商品ID'})

        try:
            sku = GoodsSKU.objects.get(id=sku_id)
        except GoodsSKU.DoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '商品不存在'})

        # 业务处理添加购物车记录
        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id

        conn.hdel(cart_key, sku_id)

        # 计算用户购物车中商品的总件数{'1':5,'2':3}
        total_count = 0
        vals = conn.hvals(cart_key)
        for val in vals:
            total_count += int(val)

        # 返回应答
        return JsonResponse({'res': 3, 'total_count':total_count, 'errmsg': '删除成功！'})

