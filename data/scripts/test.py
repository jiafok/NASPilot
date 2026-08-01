import requests

# ====================== 【请在这里修改你的信息】 ======================
QB_URL = "http://192.168.0.114:8080"  # 你的 qBittorrent 网页地址
QB_USER = "jeffrey"                     # 你的用户名
QB_PASS = "shasha66"              # 你的密码
# ======================================================================

def test_qb_login():
    print("=" * 50)
    print("开始测试 qBittorrent 登录...")
    print(f"连接地址: {QB_URL}")
    print(f"用户名: {QB_USER}")
    print("=" * 50)

    session = requests.Session()

    # 登录接口
    login_url = f"{QB_URL.rstrip('/')}/api/v2/auth/login"
    print(f"请求地址: {login_url}")

    # 发送登录请求
    try:
        response = session.post(
            login_url,
            data={
                "username": QB_USER,
                "password": QB_PASS
            },
            timeout=10
        )

        # 打印完整返回信息（关键！）
        print("\n===== 测试结果 =====")
        print(f"状态码: {response.status_code}")
        print(f"返回内容: {repr(response.text)}")
        print(f"Cookies: {session.cookies.get_dict()}")
        print("=" * 50)

        # 判断结果
        if response.status_code == 200 and response.text.strip() == "Ok.":
            print("✅ 登录成功！")
        else:
            print("❌ 登录失败！")

    except Exception as e:
        print(f"\n❌ 请求出错: {e}")
        print("=" * 50)

if __name__ == "__main__":
    test_qb_login()
