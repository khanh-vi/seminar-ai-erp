"""Tạo lịch sử tiêu hao vật tư MÔ PHỎNG cho 28 ngày gần nhất, để AI có dữ liệu mà đọc.

Chạy:  python tao_lich_su.py   (chuan_bi_demo.py cũng tự gọi phần này)

Với mỗi dịch vụ có BOM, mỗi ngày tạo một phiếu xuất kho (nguồn ghi "Lịch sử mô phỏng..."),
số lượng = số lượt làm dịch vụ trong ngày x định mức BOM. Cuối tuần đông khách hơn ngày thường.
Chỉ bù những ngày còn thiếu, nên chạy lại nhiều lần không bị trùng.
"""
import random
from datetime import datetime, timedelta

from cau_hinh_du_lieu import DICH_VU, dat_ton_kho_ban_dau
from odoo_api import Odoo, doc_env

SO_NGAY = 28


def nguon(ten_dich_vu):
    # "Sơn gel" giữ tên nguồn cũ để nhận ra lịch sử đã tạo từ trước
    return "Lịch sử mô phỏng" if ten_dich_vu == "Sơn gel" else f"Lịch sử mô phỏng: {ten_dich_vu}"


def bo_sung_lich_su(odoo, in_chi_tiet=True):
    loai_xuat = odoo.search_read("stock.picking.type", [["code", "=", "outgoing"], ["name", "not ilike", "POS"]],
                                 ["default_location_src_id", "default_location_dest_id"], limit=1)[0]
    kho = loai_xuat["default_location_src_id"][0]
    khach = loai_xuat["default_location_dest_id"][0]
    hom_nay = datetime.now().date()
    tong = 0

    for ten, (_, bom, ngay_thuong, cuoi_tuan) in DICH_VU.items():
        if not bom:
            continue
        da_co = {p["date_done"][:10] for p in odoo.search_read(
            "stock.picking", [["origin", "=", nguon(ten)], ["state", "=", "done"]], ["date_done"])}
        san_pham = {v: odoo.search_read("product.product", [["name", "=", v]], ["uom_id"], limit=1)[0] for v in bom}

        for i in range(SO_NGAY, 0, -1):
            ngay = hom_nay - timedelta(days=i)
            if ngay.isoformat() in da_co:
                continue
            la_cuoi_tuan = ngay.weekday() >= 5
            # Cố định theo (dịch vụ, ngày): chạy lại ra đúng số cũ
            luot = random.Random(f"{ten}-{ngay}").randint(*(cuoi_tuan if la_cuoi_tuan else ngay_thuong))
            # 18:00 giờ VN = 11:00 UTC (Odoo lưu giờ UTC)
            ngay_str = f"{ngay.isoformat()} 11:00:00"

            picking = odoo.call("stock.picking", "create", {
                "picking_type_id": loai_xuat["id"], "location_id": kho, "location_dest_id": khach,
                "origin": nguon(ten), "scheduled_date": ngay_str,
                "move_ids": [(0, 0, {
                    "product_id": sp["id"], "product_uom": sp["uom_id"][0],
                    "product_uom_qty": round(luot * bom[v], 2),
                    "location_id": kho, "location_dest_id": khach,
                }) for v, sp in san_pham.items()],
            })
            odoo.call("stock.picking", "action_confirm", [picking])
            moves = odoo.search_read("stock.move", [["picking_id", "=", picking]], ["product_uom_qty"])
            for m in moves:
                odoo.call("stock.move", "write", [m["id"]], {"quantity": m["product_uom_qty"], "picked": True})
            odoo.call("stock.picking", "button_validate", [picking])

            # Lùi ngày về đúng ngày trong lịch sử
            odoo.call("stock.move", "write", [m["id"] for m in moves], {"date": ngay_str})
            odoo.call("stock.move.line", "write",
                      odoo.call("stock.move.line", "search", [["picking_id", "=", picking]]), {"date": ngay_str})
            odoo.call("stock.picking", "write", [picking], {"date_done": ngay_str})
            tong += 1
            if in_chi_tiet:
                print(f"   {ten:<11} {ngay:%a %d/%m}  {'cuối tuần ' if la_cuoi_tuan else 'ngày thường'}  {luot:>2} lượt")
    return tong


def main():
    odoo = Odoo(doc_env())
    print("Bổ sung lịch sử mô phỏng còn thiếu:")
    tong = bo_sung_lich_su(odoo)
    print(f"Đã tạo {tong} phiếu xuất mô phỏng." if tong else "   Lịch sử đã đủ, không cần tạo thêm.")
    print("\nĐặt lại tồn kho ban đầu:")
    dat_ton_kho_ban_dau(odoo)
    print("\nXONG. Đây là dữ liệu mô phỏng, ghi rõ khi trình bày.")


if __name__ == "__main__":
    main()
