"""Kiểm tra 2 kết nối cần cho demo: Odoo Online (API) và OpenRouter.

Chạy:  python kiem_tra_ket_noi.py
Không cần cài thêm thư viện.
"""
import json
import sys
import urllib.request
import xmlrpc.client
from pathlib import Path

# Terminal Windows mặc định không in được tiếng Việt
sys.stdout.reconfigure(encoding="utf-8")


def doc_env():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        sys.exit("Chưa có file .env. Hãy sao chép .env.example thành .env và điền thông tin.")
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def kiem_tra_odoo(env):
    print("1) Kết nối Odoo...")
    url = env["ODOO_URL"].rstrip("/")
    try:
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
        version = common.version()
        print(f"   Odoo phiên bản: {version.get('server_version')}")
        uid = common.authenticate(env["ODOO_DB"], env["ODOO_USER"], env["ODOO_API_KEY"], {})
    except Exception as e:  # noqa: BLE001
        print(f"   LỖI: {e}")
        print("   Gợi ý: kiểm tra ODOO_URL, ODOO_DB; gói Odoo có thể không cho dùng API.")
        return False
    if not uid:
        print("   LỖI: đăng nhập thất bại. Kiểm tra ODOO_USER, ODOO_API_KEY, ODOO_DB.")
        return False
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    try:
        n = models.execute_kw(env["ODOO_DB"], uid, env["ODOO_API_KEY"],
                              "res.partner", "search_count", [[]])
    except Exception as e:  # noqa: BLE001
        print(f"   LỖI khi đọc dữ liệu: {e}")
        print("   Gợi ý: gói Odoo hiện tại có thể không cho truy cập API (cần gói Custom).")
        return False
    print(f"   OK: đăng nhập được (uid={uid}), đọc được {n} liên hệ.")
    return True


def kiem_tra_openrouter(env):
    print("2) Kết nối OpenRouter...")
    body = json.dumps({
        "model": env.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        "messages": [{"role": "user", "content": "Trả lời đúng một chữ: OK"}],
        "max_tokens": 5,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {env['OPENROUTER_API_KEY']}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except Exception as e:  # noqa: BLE001
        print(f"   LỖI: {e}")
        print("   Gợi ý: kiểm tra OPENROUTER_API_KEY, số dư tài khoản, tên model.")
        return False
    print(f"   OK: model trả lời \"{data['choices'][0]['message']['content'].strip()}\"")
    return True


if __name__ == "__main__":
    env = doc_env()
    ok_odoo = kiem_tra_odoo(env)
    ok_ai = kiem_tra_openrouter(env)
    print()
    print("KẾT QUẢ:", "Sẵn sàng làm demo." if ok_odoo and ok_ai else "Còn lỗi, gửi phần LỖI ở trên cho Claude.")
