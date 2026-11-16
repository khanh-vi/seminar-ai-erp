"""Đưa demo về trạng thái ban đầu. Chạy trước mỗi buổi tập và trước giờ thuyết trình.

Chạy:  python chuan_bi_demo.py

- Hủy và xóa các đơn mua do AI tạo (kể cả đơn đã xác nhận trong lúc tập)
- Xóa các việc "Cần kiểm tra kỹ" do AI tạo
- Gỡ BOM của "Nail ombre" (nếu lúc tập đã bổ sung) để tình huống ngoại lệ A chạy lại được
- Bổ sung lịch sử tiêu hao mô phỏng cho những ngày còn thiếu (đến hết hôm qua)
- Đặt tồn kho về mức ban đầu (gel base 3 chai...)
- Đặt mốc demo: AI chỉ xét thiếu BOM với đơn POS bán sau lúc chạy script này

Lưu ý: các đơn POS đã bán lúc tập vẫn còn (Odoo không cho xóa đơn đã thanh toán).
"""
from datetime import datetime, timezone

from ai_layer import MOC_DEMO
from cau_hinh_du_lieu import DICH_VU, dat_ton_kho_ban_dau
from odoo_api import Odoo, doc_env
from tao_lich_su import bo_sung_lich_su


def main():
    odoo = Odoo(doc_env())

    don = odoo.call("purchase.order", "search", [["origin", "=", "AI đề xuất"]])
    if don:
        odoo.call("purchase.order", "button_cancel", don)
        odoo.call("purchase.order", "unlink", don)
    print(f"Đã xóa {len(don)} đơn mua do AI tạo")

    viec = odoo.call("mail.activity", "search", [["summary", "like", "Cần kiểm tra kỹ"]])
    if viec:
        odoo.call("mail.activity", "unlink", viec)
    print(f"Đã xóa {len(viec)} việc 'Cần kiểm tra kỹ'")

    for ten, (_, bom, *_) in DICH_VU.items():
        if bom is None:
            bom_ids = odoo.call("mrp.bom", "search", [["product_tmpl_id.name", "=", ten]])
            if bom_ids:
                odoo.call("mrp.bom", "unlink", bom_ids)
                print(f"Đã gỡ BOM của '{ten}' (để demo tình huống thiếu BOM)")

    so_phieu = bo_sung_lich_su(odoo, in_chi_tiet=False)
    print(f"Đã bổ sung lịch sử mô phỏng cho {so_phieu} ngày-dịch vụ còn thiếu" if so_phieu
          else "Lịch sử mô phỏng đã đủ đến hôm qua")

    print("Đặt tồn kho ban đầu:")
    dat_ton_kho_ban_dau(odoo)

    # Mốc bắt đầu buổi demo: AI chỉ kiểm tra BOM của các đơn bán SAU mốc này,
    # để đơn Nail ombre bán lúc tập không làm hỏng luồng chính
    MOC_DEMO.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    print("Đã đặt mốc demo: các đơn POS bán trước lúc này không còn bị xét thiếu BOM.")
    print("\nSẴN SÀNG DEMO.")


if __name__ == "__main__":
    main()
