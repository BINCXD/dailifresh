import datetime

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, reverse

# Create your views here.
from django.views import View
from django_redis import get_redis_connection

from apps.goods.models import GoodsSKU
from apps.order.models import OrderInfo, OrderGoods
from apps.user.models import Address
from utils.mixin import LoginRequiredMixin
from alipay import AliPay


class OrderPlaceView(LoginRequiredMixin, View):
    def post(self, request):
        sku_ids = request.POST.getlist('sku_ids')

        if not sku_ids:
            return redirect(reverse('cart:cart'))

        user = request.user
        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id

        skus = []
        # 保存用户购物车中商品总数目和总价格
        total_count = 0
        total_price = 0
        # 遍历获取商品信息
        for sku_id in sku_ids:
            sku = GoodsSKU.objects.get(id=sku_id)

            # 查询商品在购物车中的数量
            count = conn.hget(cart_key, sku_id)
            count = int(count)
            # 计算每种商品的小计,redis 中的值是字符串类型，需要转化类型
            amount = sku.price * count
            print(count, amount)
            # 动态的给sku对象添加数量和小计
            sku.count = count
            sku.amount = amount
            # 计算总的数量和总价
            total_count += count
            total_price += amount
            # 最后将sku对象添加到列表中
            skus.append(sku)

        # 运费：运费子系统
        transit_price = 10
        # 实付款
        total_pay = total_price + transit_price

        # 获取用户的全部地址
        addrs = Address.objects.filter(user=user)

        # 将sku_id 以逗号间隔拼接成字符串
        sku_ids = ','.join(sku_ids)

        # 构建context上下文
        context = {'addrs': addrs,
                   'total_count': total_count,
                   'total_price': total_price,
                   'transit_price': transit_price,
                   'total_pay': total_pay,
                   'skus': skus,
                   'sku_ids': sku_ids}

        return render(request, 'df_order/place_order.html', context)


class OrderCommitView(View):
    def post(self, request):
        """订单创建"""
        user = request.user
        if not user:
            return JsonResponse({'res': 0, 'errmsg': '请先登录'})
        # 接收参数
        sku_ids = request.POST.get('sku_ids')
        addr_id = request.POST.get('addr_id')
        pay_method = request.POST.get('pay_method')
        # 校验参数
        if not all([sku_ids, addr_id, pay_method]):
            return JsonResponse({'res': 1, 'errmsg': '数据不完整'})
        # 校验支付方式
        if pay_method not in OrderInfo.PAY_METHODS.keys():
            return JsonResponse({'res': 2, 'errmsg': '支付方式不存在'})
        # 校验地址
        try:
            addr = Address.objects.get(id=addr_id)
        except Address.DoesNotExist:
            return JsonResponse({'res': 3, 'errmsg': '收货地址不正确'})
        # todo: 创建订单核心业务
        order_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S") + str(user.id)
        transf_price = 10
        total_count = 0
        total_price = 0
        # 设置事务保存点
        save_id = transaction.savepoint()
        # todo: 保存订单信息表: 向df_order_info表中添加一条记录
        order = OrderInfo.objects.create(order_id=order_id,
                                         user=user,
                                         addr=addr,
                                         pay_method=pay_method,
                                         total_count=total_count,
                                         total_price=total_price,
                                         transit_price=transf_price)

        # 获取商品
        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id

        sku_ids = sku_ids.split(',')
        for sku_id in sku_ids:
            for i in range(3):
                try:
                    sku = GoodsSKU.objects.get(id=sku_id)
                except GoodsSKU.DoesNotExist:
                    transaction.savepoint_rollback(save_id)
                    return JsonResponse({'res': 4, 'errmsg': '商品信息不存在'})

                count = conn.hget(cart_key, sku_id)

                # todo: 判断商品的库存
                if int(count) > sku.stock:
                    transaction.savepoint_rollback(save_id)
                    return JsonResponse({'res': 6, 'errmsg': '商品库存不足'})

                # 修改商品表中的数据
                orgin_stock = sku.stock
                new_stock = orgin_stock - int(count)
                new_sales = sku.sales + int(count)

                res = GoodsSKU.objects.filter(id=sku_id, stock=orgin_stock).update(stock=new_stock, sales=new_sales)
                if res == 0:
                    if i == 2:
                        transaction.savepoint_rollback(save_id)
                        return JsonResponse({'res': 7, 'errmsg': '下单失败2'})
                    continue
                # todo: 向订单商品表中添加信息
                OrderGoods.objects.create(order=order,
                                          sku=sku,
                                          count=count,
                                          price=sku.price)

                total_count += int(count)
                total_price += int(count) * sku.price

                break

        # 更新订单表中的总金额和总件数
        order.total_count = total_count
        order.total_price = total_price
        order.save()

        # 订单的相关信息写入完成之后,删除购物车中的记录信息
        conn.hdel(cart_key, *sku_ids)  # sku_ids 列表需要拆包
        # 返回应答
        return JsonResponse({'res': 5, 'message': '订单创建成功'})


class OrderPayView(View):
    """订单支付"""

    def post(self, request):

        # 用户是否登录
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '用户未登录'})

        # 接收参数
        order_id = request.POST.get('order_id')

        # 校验参数
        if not order_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的订单'})

        try:
            order = OrderInfo.objects.get(order_id=order_id,
                                          user=user,
                                          pay_method=3,
                                          order_status=1)
        except OrderInfo.DoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '订单错误'})

        # 业务处理：使用python sdk调用支付宝的支付接口
        # alipay初始化
        app_private_key_string = open("apps/order/app_private_key.pem").read()
        alipay_public_key_string = open("apps/order/alipay_public_key.pem").read()

        # app_private_key_string == """
        #     -----BEGIN RSA PRIVATE KEY-----
        #     base64 encoded content
        #     -----END RSA PRIVATE KEY-----
        # """
        #
        # alipay_public_key_string == """
        #     -----BEGIN PUBLIC KEY-----
        #     base64 encoded content
        #     -----END PUBLIC KEY-----
        # """

        # app_private_key_string = os.path.join(settings.BASE_DIR, 'apps/order/app_private_key.pem')
        # alipay_public_key_string = os.path.join(settings.BASE_DIR, 'apps/order/alipay_public_key.pem')

        alipay = AliPay(
            appid="2016101600696625",  # 应用id
            app_notify_url=None,  # 默认回调url
            app_private_key_string=app_private_key_string,
            # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
            alipay_public_key_string=alipay_public_key_string,
            sign_type="RSA2",  # RSA 或者 RSA2
            debug=True  # 默认False, 此处沙箱模拟True
        )


        # 调用支付接口
        # 电脑网站支付，需要跳转到https://openapi.alipaydev.com/gateway.do? + order_string
        total_pay = order.total_price + order.transit_price
        order_string = alipay.api_alipay_trade_page_pay(
            out_trade_no=order_id,  # 订单id
            total_amount=str(total_pay),  # 支付总金额
            subject='天天生鲜%s 用户' % order_id,
            return_url=None,
            notify_url=None  # 可选, 不填则使用默认notify url
        )

        # 返回应答
        pay_url = 'https://openapi.alipaydev.com/gateway.do?' + order_string

        return JsonResponse({'res': 3, 'pay_url': pay_url})


class CheckPayView(View):
    """查看订单支付结果"""

    def post(self, request):

        # 用户是否登录
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '用户未登录'})

        # 接收参数
        order_id = request.POST.get('order_id')

        # 校验参数
        if not order_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的订单'})

        try:
            order = OrderInfo.objects.get(order_id=order_id,
                                          user=user,
                                          pay_method=3,
                                          order_status=1)
        except OrderInfo.DoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '订单错误'})

        # 业务处理：使用python sdk调用支付宝的支付接口
        # alipay初始化
        app_private_key_string = open("apps/order/app_private_key.pem").read()
        alipay_public_key_string = open("apps/order/alipay_public_key.pem").read()

        # app_private_key_string == """
        #     -----BEGIN RSA PRIVATE KEY-----
        #     base64 encoded content
        #     -----END RSA PRIVATE KEY-----
        # """
        #
        # alipay_public_key_string == """
        #     -----BEGIN PUBLIC KEY-----
        #     base64 encoded content
        #     -----END PUBLIC KEY-----
        # """

        # app_private_key_string = os.path.join(settings.BASE_DIR, 'apps/order/app_private_key.pem')
        # alipay_public_key_string = os.path.join(settings.BASE_DIR, 'apps/order/alipay_public_key.pem')

        alipay = AliPay(
            appid="2016092700608687",  # 应用id
            app_notify_url=None,  # 默认回调url
            app_private_key_string=app_private_key_string,
            # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
            alipay_public_key_string=alipay_public_key_string,
            sign_type="RSA2",  # RSA 或者 RSA2
            debug=True  # 默认False, 此处沙箱模拟True
        )

        # 调用支付宝的交易查询接口
        while True:
            response = alipay.api_alipay_trade_query(order_id)

            # response = {
            # "trade_no": "2017032121001004070200176844",  # 支付宝交易号
            # "code": "10000",  # 接口调用成功
            # "invoice_amount": "20.00",
            # "open_id": "20880072506750308812798160715407",
            # "fund_bill_list": [
            #     {
            #         "amount": "20.00",
            #         "fund_channel": "ALIPAYACCOUNT"
            #     }
            # ],
            # "buyer_logon_id": "csq***@sandbox.com",
            # "send_pay_date": "2017-03-21 13:29:17",
            # "receipt_amount": "20.00",
            # "out_trade_no": "out_trade_no15",
            # "buyer_pay_amount": "20.00",
            # "buyer_user_id": "2088102169481075",
            # "msg": "Success",
            # "point_amount": "0.00",
            # "trade_status": "TRADE_SUCCESS",  # 支付结果
            # "total_amount": "20.00"
            # }

            code = response.get('code')

            if code == '10000' and response.get('trade_status') == 'TRADE_SUCCESS':
                # 支付成功
                # 获取支付宝交易号
                trade_no = response.get('trade_no')
                # 更新订单状态
                order.trade_no = trade_no
                order.order_status = 4  # 待评价
                order.save()
                # 返回应答
                return JsonResponse({'res': 3, 'message': '支付成功'})
            elif code == '40004' or (code == '10000' and response.get('trade_status') == 'WAIT_BUYER_PAY'):
                # 等待买家付款
                import time
                time.sleep(5)
                continue
            else:
                # 支付出错
                return JsonResponse({'res': 4, 'errmsg': '支付失败'})
