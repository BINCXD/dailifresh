import re

from django.contrib.auth import authenticate, login, logout
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.core.signing import SignatureExpired
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.template import loader
from django.views.decorators.csrf import csrf_exempt
from django_redis import get_redis_connection
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer

# Create your views here.
from django.urls import reverse
from django.views import View
from redis import StrictRedis

from apps.goods.models import GoodsSKU
from apps.order.models import OrderInfo, OrderGoods
from apps.user.models import User, Address
from celery_tasks.tasks import send_register_active_email
from dailyfresh import settings
from utils.mixin import LoginRequiredMixin


class RegisterView(View):
    def get(self, request):
        """显示注册页面"""
        return render(request, 'df_user/register.html')

    def post(self, request):
        """进行注册处理"""
        # 接收数据
        username = request.POST.get('user_name')
        password = request.POST.get('pwd')
        email = request.POST.get('email')
        allow = request.POST.get('allow')

        if not all([username, password, email]):
            # 数据不完整
            return render(request, 'df_user/register.html', {'errmsg': '数据不完整'})

        # 校验邮箱
        if not re.match(r'^[a-z0-9][\w.\-]*@[a-z0-9\-]+(\.[a-z]{2,5}){1,2}$', email):
            return render(request, 'df_user/register.html', {'errmsg': '邮箱格式不正确'})

        if allow != 'on':
            return render(request, 'df_user/register.html', {'errmsg': '请同意协议'})

        # 校验用户名是否重复
        try:
            User.objects.get(username=username)
            return render(request, 'df_user/register.html', {'errmsg': '用户名已存在'})
        except User.DoesNotExist:
            # 用户名不存在
            user = None

        # 进行业务处理：进行用户注册
        user = User.objects.create_user(username, email, password)
        user.is_active = 0
        user.save()

        # 发送激活邮件，包含激活链接: http://127.0.0.1:8000/user/active/3
        # 激活链接中需要包含用户的身份信息，并且要把身份信息进行加密
        # 加密用户的身份信息，生成教活token
        serializer = Serializer(settings.SECRET_KEY, 3600)
        info = {'confirm': user.id}
        token = serializer.dumps(info)
        token = token.decode('utf8')

        # 发邮箱
        send_register_active_email.delay(email, username, token)

        # 返回应答
        return redirect(reverse('goods:index'))


class ActiveView(View):
    """用户激活"""

    def get(self, request, token):
        """进行用户激活"""
        # 进行解密，获取要激活的信息
        serializer = Serializer(settings.SECRET_KEY, 3600)
        try:
            info = serializer.loads(token)
            # 获取id
            user_id = info['confirm']

            # 根据id获取用户信息
            user = User.objects.get(id=user_id)
            user.is_active = 1
            user.save()

            # 跳转至登录页面
            return redirect(reverse('user:login'))

        except SignatureExpired as e:
            # 激活链接已过期
            return HttpResponse('激活链接已过期')


class LoginView(View):
    """登录"""

    def get(self, request):
        """显示登录页面"""
        if 'username' in request.COOKIES:
            username = request.COOKIES.get('username')
            checked = 'checked'
        else:
            username = ''
            checked = ''
        # 使用模板
        return render(request, 'df_user/login.html', {'username': username, 'checked': checked})

    def post(self, request):
        """处理登录操作"""
        username = request.POST.get('username')
        password = request.POST.get('pwd')

        # 校验数据
        if not all([username, password]):
            return render(request, 'df_user/login.html', {'errmsg': '数据不完整'})

        # 业务处理：登录校验
        user = authenticate(username=username, password=password)
        if user is not None:
            if user.is_active:
                # 用户已激活
                # 记录登录状态
                login(request, user)

                # 获取登录后所要跳转到的地址
                next_url = request.GET.get('next', reverse('goods:index'))  # None

                # 跳转至next_url
                response = redirect(next_url)

                # 判断是否需要记住用户名
                remember = request.POST.get('remember')

                if remember == 'on':
                    # 记住用户名
                    response.set_cookie('username', username, max_age=7 * 24 * 3600)
                else:
                    response.delete_cookie('username')

                # 返回response
                return response

            else:
                # 用户未激活
                return render(request, 'df_user/login.html', {'errmsg': '账号未激活'})
        else:
            # 用户名或密码错误
            return render(request, 'df_user/login.html', {'errmsg': '用户名或密码错误'})
        # 返回应答


# /user
class UserInfoView(LoginRequiredMixin, View):
    """用户信息-信息页"""

    def get(self, request):
        """显示"""
        # 获取用户信息
        user = request.user
        address = Address.objects.get_default_address(user)

        # 获取用户浏览记录
        # StrictRedis(host='127.0.0.1', port='6379', db=3)
        con = get_redis_connection('default')
        history_key = 'history_%d' % user.id

        # 获取用户最新浏览的5个商品的id
        sku_ids = con.lrange(history_key, 0, 4)

        # 遍历获取用户浏览的历史商品信息
        goods_list = []
        for id in sku_ids:
            goods = GoodsSKU.objects.get(id=id)
            goods_list.append(goods)

        # 编写context
        context = {
            'page': 'user',
            'address': address,
            'goods_list': goods_list
        }

        return render(request, 'df_user/user_center_info.html',
                      context)


# /order
class UserOrderView(LoginRequiredMixin, View):
    """用户中心-订单页"""
    def get(self, request, page):
        # 获取用户的订单信息
        user = request.user
        orders = OrderInfo.objects.filter(user=user).order_by('-create_time')

        # 遍历获取订单商品信息
        for order in orders:
            # 根据order_id查询订单商品信息
            order_skus = OrderGoods.objects.filter(order_id=order.order_id)

            # 遍历Order_skus计算商品的小计
            for order_sku in order_skus:
                amount = order_sku.count * order_sku.price
                # 动态给order_sku增加属性amount,保存订单商品小计
                order_sku.amount = amount

            # 动态给order增加属性, 保存订单状态标题
            order.status_name = OrderInfo.ORDER_STATUS[order.order_status]
            order.order_skus = order_skus

        # 分页
        paginator = Paginator(orders, 2)  # 单页显示数目2

        try:
            page = int(page)
        except Exception as e:
            page = 1

        if page > paginator.num_pages or page <= 0:
            page = 1

        # 获取第page页的Page实例对象
        order_page = paginator.page(page)

        # todo: 进行页码的控制，页面上最多显示5个页码
        # 1. 总数不足5页，显示全部
        # 2. 如当前页是前3页，显示1-5页
        # 3. 如当前页是后3页，显示后5页
        # 4. 其他情况，显示当前页的前2页，当前页，当前页的后2页
        num_pages = paginator.num_pages
        if num_pages < 5:
            pages = range(1, num_pages)
        elif page <= 3:
            pages = range(1, 6)
        elif num_pages - page <= 2:
            pages = range(num_pages-4, num_pages+1)
        else:
            pages = range(page-2, page+3)

        # 组织上下文
        context = {'order_page': order_page,
                   'pages': pages,  # 页面范围控制
                   'page': 'order'}

        return render(request, 'df_user/user_center_order.html', context)


# /address
class AddressView(LoginRequiredMixin, View):
    """用户中心-地址页"""

    def get(self, request):
        """显示"""
        # 获取登录用户的User对象
        user = request.user
        # 获取用户的默认收货地址
        # try:
        #     address = Address.objects.get(user=user, is_default=True)
        # except Address.DoesNotExist:
        #     # 不存在默认收货地址
        #     address = None
        address = Address.objects.get_default_address(user)
        return render(request, 'df_user/user_center_site.html',
                      {'page': 'address', 'address': address})

    def post(self, request):
        """地址的添加"""
        # 接收数据
        receiver = request.POST.get('receiver')
        addr = request.POST.get('addr')
        zip_code = request.POST.get('zip_code')
        phone = request.POST.get('phone')

        # 校验数据
        if not all([receiver, addr, phone]):
            return render(request, 'df_user/user_center_site.html', {'errmsg': '数据不完整'})

        # 校验手机号
        if not re.match(r'^1[3|4|5|6|7|8][0-9]{9}$', phone):
            return render(request, 'df_user/user_center_site.html', {'errmsg': '手机格式不正确'})

        # 业务处理：地址添加
        # 如果用户已存在默认收货地址，添加的地址不作为默认收货地址，否则作为默认收货地址
        # 获取登录用户对象对应User对象
        user = request.user
        # try:
        #     address = Address.objects.get(user=user, is_default=True)
        # except Address.DoesNotExist:
        #     # 不存在默认收货地址
        #     address = None
        address = Address.objects.get_default_address(user)

        if address:
            is_default = False
        else:
            is_default = True

        # 添加地址
        Address.objects.create(user=user, receiver=receiver, addr=addr,
                               zip_code=zip_code, phone=phone, is_default=is_default)

        # 返回应答，刷新地址页面
        return redirect(reverse('user:address'))


class LogoutView(View):
    """退出登录"""

    def get(self, request):
        # 清除用户的session信息
        logout(request)

        # 跳转至登录页面
        return redirect(reverse('user:login'))
