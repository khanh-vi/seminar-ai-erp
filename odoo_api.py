"""Kết nối Odoo qua XML-RPC, dùng chung cho các script demo."""
import sys
import xmlrpc.client
from pathlib import Path

# Terminal Windows mặc định không in được tiếng Việt
sys.stdout.reconfigure(encoding="utf-8")


def doc_env():
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        sys.exit("Chưa có file .env.")
    env = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


class Odoo:
    def __init__(self, env):
        self.url = env["ODOO_URL"].rstrip("/")
        self.db = env["ODOO_DB"]
        self.key = env["ODOO_API_KEY"]
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, env["ODOO_USER"], self.key, {})
        if not self.uid:
            sys.exit("Không đăng nhập được Odoo, kiểm tra file .env.")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    def call(self, model, method, *args, **kwargs):
        kwargs.setdefault("context", {"lang": "vi_VN"})
        try:
            return self.models.execute_kw(self.db, self.uid, self.key, model, method, list(args), kwargs)
        except xmlrpc.client.Fault as e:
            # Hàm chạy xong nhưng trả về None, XML-RPC phía Odoo không gửi được giá trị rỗng
            if "cannot marshal None" in e.faultString:
                return None
            raise

    def search_read(self, model, domain, fields, **kwargs):
        return self.call(model, "search_read", domain, fields=fields, **kwargs)

    def find_or_create(self, model, domain, values):
        """Trả về id bản ghi khớp domain; chưa có thì tạo mới. Có rồi thì cập nhật values."""
        ids = self.call(model, "search", domain, limit=1)
        if ids:
            self.call(model, "write", ids, values)
            return ids[0]
        return self.call(model, "create", values)
