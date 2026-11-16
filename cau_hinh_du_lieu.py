"""Tạo dữ liệu nền cho demo tiệm nail trên Odoo.

Chạy:  python cau_hinh_du_lieu.py
Chạy lại nhiều lần được: bản ghi đã có thì cập nhật, tồn kho được đặt lại về mức ban đầu.

TẤT CẢ SỐ LIỆU DƯỚI ĐÂY LÀ GIẢ ĐỊNH (nhóm chưa có số liệu thật từ tiệm).
Khi có số thật, chỉ cần sửa các bảng ở đầu file rồi chạy lại.
"""
from odoo_api import Odoo, doc_env

TEN_TIEM = "Tiệm Nail X"

# Nhà cung cấp
NHA_CUNG_CAP = ["NCC A", "NCC B"]

# Vật tư: tên -> (giá vốn, tồn ban đầu, [(nhà cung cấp, giá mua, số ngày giao)], (tồn tối thiểu, tồn tối đa))
# NCC đứng đầu danh sách là NCC mặc định của quy tắc đặt hàng lại cố định trong Odoo.
# Tồn ban đầu được chọn để AI ra 3 kiểu quyết định khác nhau trong cùng một lần chạy:
#   Gel base, Sơn gel đỏ: sắp hết, đặt NCC A (rẻ)
#   Bột đắp: còn ~2 ngày, NCC A giao 3 ngày -> phải đặt NCC B (đắt hơn nhưng giao 1 ngày)
#   Top coat, Sơn thường, Acetone: còn đủ dùng, chưa cần đặt
VAT_TU = {
    "Gel base 15ml": (120000, 3, [("NCC A", 120000, 3), ("NCC B", 140000, 1)], (2, 6)),
    "Sơn gel màu đỏ 15ml": (90000, 3, [("NCC A", 90000, 3), ("NCC B", 100000, 1)], (2, 6)),
    "Top coat 15ml": (110000, 9, [("NCC A", 110000, 3)], (3, 8)),
    "Bột đắp acrylic 30g": (150000, 1, [("NCC A", 150000, 3), ("NCC B", 175000, 1)], (1, 3)),
    "Sơn thường màu hồng 12ml": (35000, 5, [("NCC A", 35000, 3)], (2, 6)),
    "Acetone 500ml": (60000, 3, [("NCC A", 60000, 2), ("NCC B", 70000, 1)], (1, 4)),
}

# Dịch vụ: tên -> (giá bán, BOM {vật tư: số chai/hũ tiêu hao mỗi lần},
#                   số lượt/ngày thường (min, max), số lượt/ngày cuối tuần (min, max))  -- dùng để tạo lịch sử mô phỏng
# BOM None = cố tình chưa khai báo
DICH_VU = {
    "Sơn gel": (150000, {"Gel base 15ml": 0.04, "Top coat 15ml": 0.03, "Sơn gel màu đỏ 15ml": 0.05},
                (8, 12), (14, 18)),
    "Đắp bột": (300000, {"Bột đắp acrylic 30g": 0.12, "Top coat 15ml": 0.03}, (2, 4), (5, 7)),
    "Sơn thường": (80000, {"Sơn thường màu hồng 12ml": 0.06}, (4, 6), (7, 9)),
    "Tháo gel": (30000, {"Acetone 500ml": 0.03}, (3, 5), (5, 7)),
    "Nail ombre": (250000, None, None, None),  # tình huống ngoại lệ A: đã bán nhưng chưa có BOM
}

def main():
    odoo = Odoo(doc_env())

    print("1) Thông tin tiệm")
    odoo.call("res.company", "write", [1], {"name": TEN_TIEM})
    # Bán ở POS thì trừ kho ngay, không đợi đóng phiên
    odoo.call("res.company", "write", [1], {"point_of_sale_update_stock_quantities": "real"})

    print("2) Nhóm sản phẩm")
    cat = {ten: odoo.find_or_create("product.category", [["name", "=", ten]], {"name": ten})
           for ten in ["Vật tư", "Dịch vụ"]}

    print("3) Nhà cung cấp")
    ncc = {ten: odoo.find_or_create("res.partner", [["name", "=", ten]],
                                    {"name": ten, "is_company": True, "supplier_rank": 1})
           for ten in NHA_CUNG_CAP}

    print("4) Vật tư (theo dõi tồn kho, có giá và thời gian giao của từng NCC)")
    vat_tu = {}
    for ten, (gia_von, _, nguon, _) in VAT_TU.items():
        tmpl = odoo.find_or_create("product.template", [["name", "=", ten]], {
            "name": ten, "type": "consu", "is_storable": True, "categ_id": cat["Vật tư"],
            "standard_price": gia_von, "sale_ok": False, "purchase_ok": True,
            "available_in_pos": False, "taxes_id": [(6, 0, [])], "supplier_taxes_id": [(6, 0, [])],
        })
        for thu_tu, (ten_ncc, gia, ngay) in enumerate(nguon, start=1):
            odoo.find_or_create("product.supplierinfo",
                                [["product_tmpl_id", "=", tmpl], ["partner_id", "=", ncc[ten_ncc]]],
                                {"product_tmpl_id": tmpl, "partner_id": ncc[ten_ncc], "price": gia,
                                 "delay": ngay, "sequence": thu_tu})
        vat_tu[ten] = odoo.call("product.product", "search", [["product_tmpl_id", "=", tmpl]], limit=1)[0]

    print("5) Dịch vụ và BOM (loại Kit: bán dịch vụ thì trừ vật tư)")
    for ten, (gia_ban, bom, *_) in DICH_VU.items():
        tmpl = odoo.find_or_create("product.template", [["name", "=", ten]], {
            "name": ten, "type": "consu", "is_storable": False, "categ_id": cat["Dịch vụ"],
            "list_price": gia_ban, "sale_ok": True, "purchase_ok": False,
            "available_in_pos": True, "taxes_id": [(6, 0, [])],
        })
        cu = odoo.call("mrp.bom", "search", [["product_tmpl_id", "=", tmpl]])
        if cu:
            odoo.call("mrp.bom", "unlink", cu)
        if bom:
            odoo.call("mrp.bom", "create", {
                "product_tmpl_id": tmpl, "type": "phantom", "product_qty": 1,
                "bom_line_ids": [(0, 0, {"product_id": vat_tu[v], "product_qty": sl}) for v, sl in bom.items()],
            })
            print(f"   {ten}: BOM {bom}")
        else:
            print(f"   {ten}: CỐ TÌNH KHÔNG CÓ BOM (tình huống ngoại lệ A)")

    print("6) Quầy POS và phương thức thanh toán")
    so = {j["type"]: j["id"] for j in odoo.search_read("account.journal", [["type", "in", ["cash", "bank"]]], ["type"])}
    pt_tien_mat = odoo.find_or_create("pos.payment.method", [["name", "=", "Tiền mặt"]],
                                      {"name": "Tiền mặt", "journal_id": so["cash"]})
    pt_the = odoo.find_or_create("pos.payment.method", [["name", "=", "Thẻ"]],
                                 {"name": "Thẻ", "journal_id": so["bank"]})
    odoo.find_or_create("pos.config", [["name", "=", TEN_TIEM]],
                        {"name": TEN_TIEM, "payment_method_ids": [(6, 0, [pt_tien_mat, pt_the])]})

    print("7) Quy tắc đặt hàng lại có sẵn của Odoo (tồn tối thiểu/tối đa cố định, để so sánh với AI)")
    kho = odoo.search_read("stock.warehouse", [], ["lot_stock_id"], limit=1)[0]
    for ten, (*_, (toi_thieu, toi_da)) in VAT_TU.items():
        odoo.find_or_create("stock.warehouse.orderpoint", [["product_id", "=", vat_tu[ten]]], {
            "product_id": vat_tu[ten], "warehouse_id": kho["id"], "location_id": kho["lot_stock_id"][0],
            "product_min_qty": toi_thieu, "product_max_qty": toi_da,
            "trigger": "manual",  # Thủ công: Odoo không tự tạo đơn, chỉ dùng để so sánh
        })
        print(f"   {ten}: tối thiểu {toi_thieu}, tối đa {toi_da}")

    print("8) Tồn kho ban đầu")
    dat_ton_kho_ban_dau(odoo)

    print("\nXONG. Số liệu là giả định, ghi rõ khi trình bày.")


def dat_ton_kho_ban_dau(odoo):
    """Đặt tồn kho vật tư về mức ban đầu (dùng trước mỗi buổi tập và hôm thuyết trình)."""
    kho = odoo.search_read("stock.warehouse", [], ["lot_stock_id"], limit=1)[0]["lot_stock_id"][0]
    ctx = {"lang": "vi_VN", "inventory_mode": True}
    quant_ids = []
    for ten, (_, ton, *_) in VAT_TU.items():
        sp = odoo.call("product.product", "search", [["name", "=", ten]], limit=1)[0]
        q = odoo.call("stock.quant", "search", [["product_id", "=", sp], ["location_id", "=", kho]], limit=1)
        if q:
            odoo.call("stock.quant", "write", q, {"inventory_quantity": ton}, context=ctx)
            quant_ids += q
        else:
            quant_ids.append(odoo.call("stock.quant", "create",
                                       {"product_id": sp, "location_id": kho, "inventory_quantity": ton},
                                       context=ctx))
        print(f"   {ten}: {ton}")
    odoo.call("stock.quant", "action_apply_inventory", quant_ids, context=ctx)


if __name__ == "__main__":
    main()
