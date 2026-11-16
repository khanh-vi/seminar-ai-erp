# Demo IS336: AI đề xuất đơn mua vật tư cho Tiệm Nail X (Odoo 19 + OpenRouter)

Phần 6 (Live Demo) và Phần 7 (Exception & Human-in-the-Loop) của seminar **Odoo Scale-Up: AI in ERP**.

AI Layer đọc tồn kho, lịch sử xuất kho theo BOM và nhà cung cấp từ Odoo, gọi LLM qua OpenRouter để đề xuất
số lượng và nhà cung cấp, rồi tạo **đơn mua nháp** trên Odoo. Chủ tiệm duyệt, AI không bao giờ tự xác nhận đơn.

> Toàn bộ số liệu (vật tư, giá, nhà cung cấp, định mức, lịch sử tiêu hao) là **giả định / mô phỏng**.

## Thành phần
| File | Vai trò |
|---|---|
| `docker-compose.yml` | Odoo 19 Community + PostgreSQL 16 chạy trên máy |
| `.env.example` | Mẫu cấu hình (sao chép thành `.env` rồi điền key) |
| `odoo_api.py` | Kết nối Odoo qua XML-RPC |
| `kiem_tra_ket_noi.py` | Kiểm tra kết nối Odoo và OpenRouter |
| `cau_hinh_du_lieu.py` | Tạo dữ liệu nền: vật tư, dịch vụ, BOM, nhà cung cấp, POS, quy tắc đặt hàng lại, tồn kho |
| `tao_lich_su.py` | Tạo 28 ngày lịch sử tiêu hao mô phỏng |
| `ai_layer.py` | AI Layer (`--trinh-chieu` khi thuyết trình, `--bao-cao` cho Human Override Rate) |
| `chuan_bi_demo.py` | Đưa demo về trạng thái ban đầu trước mỗi lần chạy |

## Cài đặt (máy mới)
Cần: Docker Desktop, Python 3.10+ (không cần cài thêm thư viện).

1. Bật Odoo:
   ```
   docker compose up -d
   ```
   Mở `http://localhost:8069`.
2. **Cách A, dùng bản sao lưu nhóm gửi (khuyên dùng):** ở trang `http://localhost:8069/web/database/manager`
   bấm **Restore Database**, chọn file `.zip`, đặt tên database `tiemnail`, chọn **This database was moved**.
   Đăng nhập bằng tài khoản người gửi bản sao lưu cung cấp.

   **Cách B, tạo mới từ đầu:** tạo database tên `tiemnail` (không tick Demo data), cài 4 app
   Điểm bán lẻ, Tồn kho, Mua hàng, Sản xuất, rồi chạy bước 4 và:
   ```
   python cau_hinh_du_lieu.py
   python tao_lich_su.py
   ```
   (Cách B cần nạp hệ thống tài khoản Việt Nam trước: Kế toán/Hóa đơn → Cài đặt → gói kế toán Việt Nam.)
3. Trong Odoo: ảnh đại diện → **Hồ sơ của tôi** → **Bảo mật tài khoản** → **Khóa API mới**.
4. Sao chép `.env.example` thành `.env`, điền email đăng nhập, API key Odoo, API key OpenRouter **của mình**.
5. Kiểm tra:
   ```
   python kiem_tra_ket_noi.py
   ```

## Chạy demo
```
python chuan_bi_demo.py
python ai_layer.py --trinh-chieu
python ai_layer.py --bao-cao
```
