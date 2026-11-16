"""AI Layer: đề xuất đơn mua vật tư cho tiệm nail.

Chạy:
    python ai_layer.py --trinh-chieu   dùng khi THUYẾT TRÌNH: bảng gọn, có màu, bấm Enter để sang từng bước
    python ai_layer.py                 chạy đầy đủ, in hết dữ liệu (dùng khi kiểm tra)
    python ai_layer.py --bao-cao       thống kê đề xuất của AI: duyệt, sửa, bác bỏ (Human Override Rate)

Luồng: Odoo (tồn kho, lịch sử xuất, NCC) -> code tính tiêu hao -> kiểm tra dữ liệu
       -> LLM (OpenRouter) chọn vật tư, số lượng, NCC, lý do, confidence
       -> code kiểm tra kết quả -> so sánh với quy tắc đặt hàng lại cố định của Odoo
       -> Odoo: đơn mua NHÁP hoặc cảnh báo "Cần kiểm tra kỹ".
AI không bao giờ xác nhận đơn mua. Chỉ gửi số liệu vật tư sang AI, không gửi thông tin khách.
"""
import json
import math
import os
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from odoo_api import Odoo, doc_env

NGUONG_CONFIDENCE = 0.75        # dưới ngưỡng: không tạo đơn nháp (nhóm tự đặt cho demo)
SO_NGAY_LICH_SU = 28
SO_NGAY_KIEM_TRA_BOM = 7        # xem các dịch vụ đã bán trong 7 ngày gần nhất
NGAY_DUNG_SAU_KHI_HANG_VE = 14  # đặt đủ dùng 2 tuần sau khi hàng về (tiệm nhập hàng 2 tuần một lần)
NGAY_DEM = 3                    # nên đặt khi số ngày còn lại <= thời gian giao + 3 ngày dự phòng
NGUON_AI = "AI đề xuất"
NHAT_KY = Path(__file__).with_name("nhat_ky_ai.jsonl")
MOC_DEMO = Path(__file__).with_name("moc_demo.txt")  # do chuan_bi_demo.py ghi
GIO_VN = timezone(timedelta(hours=7))

TRINH_CHIEU = "--trinh-chieu" in sys.argv

# Màu chữ trong terminal
os.system("")  # bật mã màu ANSI trên Windows
XANH, VANG, DO, XAM, DAM, HET = "\033[92m", "\033[93m", "\033[91m", "\033[90m", "\033[1m", "\033[0m"


def mau(chu, ma):
    return f"{ma}{chu}{HET}"


def buoc(so, chu):
    if TRINH_CHIEU:
        input(mau(f"\n   ⏎  Enter để sang bước {so}...", XAM))
    print(f"\n{mau(f'[{so}] {chu}', DAM)}")
    print("-" * 78)


def bang(tieu_de_cot, dong, rong):
    """In bảng căn cột đơn giản."""
    print("   " + "  ".join(mau(t.ljust(r), DAM) for t, r in zip(tieu_de_cot, rong)))
    for d in dong:
        print("   " + "  ".join(str(o).ljust(r) for o, r in zip(d, rong)))


def ngan(ten):
    """Tên vật tư rút gọn cho bảng: bỏ dung tích ở cuối."""
    return re.sub(r"\s+\d+(ml|g)$", "", ten)


def so(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


# ---------- 1. Đọc dữ liệu Odoo ----------

def doc_du_lieu(odoo):
    vat_tu = odoo.search_read("product.product", [["categ_id.name", "=", "Vật tư"]],
                              ["name", "qty_available", "incoming_qty", "product_tmpl_id"], order="id")
    ncc = odoo.search_read("product.supplierinfo",
                           [["product_tmpl_id", "in", [v["product_tmpl_id"][0] for v in vat_tu]]],
                           ["product_tmpl_id", "partner_id", "price", "delay", "sequence"], order="sequence, id")
    tu_ngay = (datetime.now(timezone.utc) - timedelta(days=SO_NGAY_LICH_SU)).strftime("%Y-%m-%d %H:%M:%S")
    xuat = odoo.search_read("stock.move", [["state", "=", "done"], ["location_dest_id.usage", "=", "customer"],
                                           ["product_id", "in", [v["id"] for v in vat_tu]], ["date", ">=", tu_ngay]],
                            ["product_id", "quantity", "date"])
    quy_tac = {q["product_id"][0]: q for q in odoo.search_read(
        "stock.warehouse.orderpoint", [["product_id", "in", [v["id"] for v in vat_tu]]],
        ["product_id", "product_min_qty", "product_max_qty"])}
    return vat_tu, ncc, xuat, quy_tac


def tinh_tieu_hao(vat_tu, ncc, xuat):
    """Code tính toán, LLM không tự làm phép tính."""
    hom_nay = datetime.now(GIO_VN).date()
    theo_ngay = defaultdict(lambda: defaultdict(float))
    for m in xuat:
        ngay = datetime.strptime(m["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(GIO_VN).date()
        if ngay < hom_nay:  # chỉ tính các ngày đã trọn vẹn
            theo_ngay[m["product_id"][0]][ngay] += m["quantity"]

    ngay_lich_su = [hom_nay - timedelta(days=i) for i in range(1, SO_NGAY_LICH_SU + 1)]
    ket_qua = []
    for v in vat_tu:
        dung = theo_ngay[v["id"]]
        thuong = [dung.get(d, 0) for d in ngay_lich_su if d.weekday() < 5]
        cuoi_tuan = [dung.get(d, 0) for d in ngay_lich_su if d.weekday() >= 5]
        tb_thuong = sum(thuong) / len(thuong)
        tb_cuoi_tuan = sum(cuoi_tuan) / len(cuoi_tuan)
        # Độ dao động (hệ số biến thiên) của tiêu hao ngày thường: càng cao càng khó dự báo
        do_dao_dong = (sum((x - tb_thuong) ** 2 for x in thuong) / len(thuong)) ** 0.5 / tb_thuong if tb_thuong else None
        du_bao = lambda d: tb_cuoi_tuan if d.weekday() >= 5 else tb_thuong  # noqa: E731

        # Mô phỏng từng ngày tới để biết còn bao nhiêu ngày thì hết
        con, so_ngay_con_lai = v["qty_available"], None
        for i in range(60):
            con -= du_bao(hom_nay + timedelta(days=i))
            if con < 0:
                so_ngay_con_lai = i
                break

        lua_chon = []
        for n in ncc:
            if n["product_tmpl_id"][0] != v["product_tmpl_id"][0]:
                continue
            ngay_can = n["delay"] + NGAY_DUNG_SAU_KHI_HANG_VE
            nhu_cau = sum(du_bao(hom_nay + timedelta(days=i)) for i in range(ngay_can))
            lua_chon.append({
                "nha_cung_cap_id": n["partner_id"][0], "ten": n["partner_id"][1],
                "gia_mot_don_vi": n["price"], "so_ngay_giao": n["delay"],
                "hang_ve_kip_truoc_khi_het": so_ngay_con_lai is None or so_ngay_con_lai >= n["delay"],
                "so_luong_tham_khao": max(0, math.ceil(nhu_cau - v["qty_available"] - v["incoming_qty"])),
            })
        # So sánh NCC do code viết sẵn, để LLM không tự suy diễn sai (rẻ hơn / giao nhanh hơn)
        for a in lua_chon:
            so_sanh = []
            for b in lua_chon:
                if a is b:
                    continue
                chenh_gia = a["gia_mot_don_vi"] - b["gia_mot_don_vi"]
                chenh_ngay = a["so_ngay_giao"] - b["so_ngay_giao"]
                gia = f"{'rẻ hơn' if chenh_gia < 0 else 'đắt hơn'} {abs(chenh_gia):,.0f}đ" if chenh_gia else "cùng giá"
                ngay = f"giao {'nhanh' if chenh_ngay < 0 else 'chậm'} hơn {abs(chenh_ngay)} ngày" if chenh_ngay else "giao cùng lúc"
                so_sanh.append(f"so với {b['ten']}: {gia}, {ngay}")
            a["so_sanh"] = "; ".join(so_sanh) or "nhà cung cấp duy nhất"
        ket_qua.append({
            "vat_tu_id": v["id"], "ten": v["name"], "ton_hien_tai": round(v["qty_available"], 2),
            "dang_ve_tu_don_da_xac_nhan": round(v["incoming_qty"], 2),
            "tieu_hao_tb_ngay_thuong": round(tb_thuong, 3), "tieu_hao_tb_cuoi_tuan": round(tb_cuoi_tuan, 3),
            "so_ngay_co_du_lieu": len(dung), "so_ngay_con_lai": so_ngay_con_lai,
            "do_dao_dong_tieu_hao": round(do_dao_dong, 2) if do_dao_dong is not None else None,
            "nha_cung_cap": lua_chon,
        })
    return ket_qua


def quy_tac_co_dinh(v, quy_tac):
    """Cách cũ: quy tắc đặt hàng lại tối thiểu/tối đa có sẵn của Odoo, NCC mặc định là NCC đầu danh sách."""
    q = quy_tac.get(v["vat_tu_id"])
    if not q:
        return None
    co = v["ton_hien_tai"] + v["dang_ve_tu_don_da_xac_nhan"]
    kq = {"toi_thieu": q["product_min_qty"], "toi_da": q["product_max_qty"], "dat": co <= q["product_min_qty"]}
    if kq["dat"] and v["nha_cung_cap"]:
        n = v["nha_cung_cap"][0]
        kq.update(so_luong=math.ceil(q["product_max_qty"] - co), ncc=n["ten"], so_ngay_giao=n["so_ngay_giao"],
                  het_truoc_khi_ve=not n["hang_ve_kip_truoc_khi_het"])
    return kq


# ---------- 2. Kiểm tra chất lượng dữ liệu (code, không phải AI) ----------

def kiem_tra_du_lieu(odoo, du_lieu):
    """Trả về (danh sách cảnh báo, các dịch vụ thiếu BOM [(product_tmpl_id, tên)])."""
    canh_bao, thieu_bom = [], []
    tu_ngay = (datetime.now(timezone.utc) - timedelta(days=SO_NGAY_KIEM_TRA_BOM)).strftime("%Y-%m-%d %H:%M:%S")
    if MOC_DEMO.exists():  # bỏ qua các đơn bán lúc tập, trước mốc demo
        tu_ngay = max(tu_ngay, MOC_DEMO.read_text(encoding="utf-8").strip())
    da_ban = odoo.search_read("pos.order.line", [["order_id.date_order", ">=", tu_ngay],
                                                 ["product_id.categ_id.name", "=", "Dịch vụ"]],
                              ["product_id", "qty"])
    so_luot = defaultdict(float)
    for l in da_ban:
        so_luot[l["product_id"][1]] += l["qty"]
    for ten, luot in so_luot.items():
        tmpl = odoo.search_read("product.product", [["name", "=", ten]], ["product_tmpl_id"], limit=1)[0]
        if not odoo.call("mrp.bom", "search_count", [["product_tmpl_id", "=", tmpl["product_tmpl_id"][0]]]):
            thieu_bom.append((tmpl["product_tmpl_id"][0], ten))
            canh_bao.append(f"Dịch vụ '{ten}' đã bán {luot:g} lượt trong {SO_NGAY_KIEM_TRA_BOM} ngày "
                            f"nhưng CHƯA CÓ BOM: vật tư dùng cho dịch vụ này không được trừ kho, "
                            f"số liệu tiêu hao thấp hơn thực tế.")
    for v in du_lieu:
        if v["ton_hien_tai"] < 0:
            canh_bao.append(f"{v['ten']}: tồn kho âm ({v['ton_hien_tai']}), cần kiểm kê lại.")
        if not v["nha_cung_cap"]:
            canh_bao.append(f"{v['ten']}: chưa có nhà cung cấp.")
        if v["so_ngay_co_du_lieu"] < 14:
            canh_bao.append(f"{v['ten']}: chỉ có {v['so_ngay_co_du_lieu']} ngày dữ liệu xuất kho, chưa đủ để dự báo.")
    return canh_bao, thieu_bom


# ---------- 3. Gọi LLM ----------

PROMPT_HE_THONG = f"""Bạn là trợ lý mua hàng cho một tiệm nail nhỏ, chạy trong hệ thống ERP Odoo.
Bạn nhận số liệu vật tư ĐÃ ĐƯỢC TÍNH SẴN. Không tự tính lại, không bịa số liệu.
Nhiệm vụ: với từng vật tư, quyết định có nên đặt hàng không, đặt bao nhiêu (chai/hũ), ở nhà cung cấp nào.

Quy tắc:
- dang_ve_tu_don_da_xac_nhan là hàng chủ tiệm đã đặt, sắp về; so_luong_tham_khao đã trừ phần này.
  Nếu so_luong_tham_khao = 0 thì không cần đặt thêm.
- Nên đặt khi so_ngay_con_lai <= so_ngay_giao của nhà cung cấp rẻ nhất + {NGAY_DEM}. so_ngay_con_lai = null nghĩa là còn đủ dùng lâu.
- Chọn nhà cung cấp: CHỈ xét các nhà cung cấp có hang_ve_kip_truoc_khi_het = true, trong số đó chọn bên rẻ nhất.
  Nhà cung cấp có hang_ve_kip_truoc_khi_het = false thì KHÔNG chọn, dù rẻ hơn (tiệm sẽ hết hàng trước khi hàng về).
  Chỉ khi không bên nào kịp mới chọn bên giao nhanh nhất.
- so_luong thường bằng so_luong_tham_khao của nhà cung cấp được chọn; nếu khác thì giải thích.
- Chỉ dùng vat_tu_id và nha_cung_cap_id có trong dữ liệu.
- Nếu có canh_bao_du_lieu, số liệu có thể sai: giảm confidence và nêu rõ trong lý do.
- so_luong_tham_khao = đủ dùng đến khi hàng về và thêm 14 ngày (tiệm nhập hàng 2 tuần một lần), trừ tồn hiện tại.
- ly_do viết tiếng Việt, tối đa 2 câu ngắn, dễ hiểu với chủ tiệm: nêu số ngày còn lại, cuối tuần dùng nhiều hơn
  ngày thường (nếu liên quan), và vì sao chọn nhà cung cấp này. Khi so sánh nhà cung cấp, CHỈ dùng nội dung
  trường so_sanh, không tự suy ra.
- confidence từ 0 đến 0.95 (không bao giờ 1): cao khi có đủ 28 ngày dữ liệu và do_dao_dong_tieu_hao thấp (< 0.3);
  giảm khi ít dữ liệu, dao động mạnh, hoặc có canh_bao_du_lieu.

Trả về DUY NHẤT một JSON:
{{"de_xuat": [{{"vat_tu_id": int, "can_dat": bool, "so_luong": int, "nha_cung_cap_id": int hoặc null,
"ly_do": str, "confidence": float}}]}}  (mỗi vật tư một phần tử)"""


def goi_llm(env, du_lieu_gui):
    body = json.dumps({
        "model": env.get("OPENROUTER_MODEL", "openai/gpt-4.1-mini"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": PROMPT_HE_THONG},
                     {"role": "user", "content": json.dumps(du_lieu_gui, ensure_ascii=False)}],
    }).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, headers={
        "Authorization": f"Bearer {env['OPENROUTER_API_KEY']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)["choices"][0]["message"]["content"]


# ---------- 4. Kiểm tra kết quả AI và quy tắc kiểm soát ----------

def doc_json(noi_dung):
    """Đọc JSON AI trả về, chấp nhận cả khi AI bọc trong ```json ... ```."""
    noi_dung = re.sub(r"^```(json)?|```$", "", noi_dung.strip()).strip()
    return json.loads(noi_dung)["de_xuat"]


def kiem_tra_ket_qua(noi_dung, du_lieu, canh_bao):
    """Trả về (đề xuất cần xử lý: đặt hoặc bị chặn, mọi quyết định của AI theo vat_tu_id)."""
    try:
        de_xuat = doc_json(noi_dung)
    except (json.JSONDecodeError, KeyError, TypeError):
        print(mau("   ✗ AI trả về không đúng định dạng JSON: KHÔNG tạo đơn nào.", DO))
        return [], {}
    theo_id = {v["vat_tu_id"]: v for v in du_lieu}
    hop_le, tat_ca = [], {}
    for d in de_xuat:
        v = theo_id.get(d.get("vat_tu_id"))
        if not v:
            print(mau(f"   ✗ Bỏ qua: vat_tu_id {d.get('vat_tu_id')} không có trong Odoo (AI bịa).", DO))
            continue
        d["confidence"] = conf = min(float(d.get("confidence", 0)), 0.95)
        d["ten"] = v["ten"]
        tat_ca[v["vat_tu_id"]] = d
        if not d.get("can_dat"):
            print(mau(f"   · {ngan(v['ten'])}: chưa cần đặt", XAM))
            continue
        ncc_hop_le = {n["nha_cung_cap_id"]: n for n in v["nha_cung_cap"]}
        if d.get("nha_cung_cap_id") not in ncc_hop_le:
            d["chan"] = "nhà cung cấp AI chọn không có trong danh sách của vật tư này"
        elif not isinstance(d.get("so_luong"), int) or not 1 <= d["so_luong"] <= 50:
            d["chan"] = f"số lượng {d.get('so_luong')} không hợp lệ"
        elif not ncc_hop_le[d["nha_cung_cap_id"]]["hang_ve_kip_truoc_khi_het"] and any(
                n["hang_ve_kip_truoc_khi_het"] for n in v["nha_cung_cap"]):
            d["chan"] = "AI chọn nhà cung cấp giao không kịp, trong khi có nhà cung cấp giao kịp"
        elif canh_bao:
            d["confidence_ai"] = conf
            d["confidence"] = conf = min(conf, 0.6)
            d["chan"] = "dữ liệu đầu vào có cảnh báo"
        elif conf < NGUONG_CONFIDENCE:
            d["chan"] = f"confidence {conf:.0%} thấp hơn ngưỡng {NGUONG_CONFIDENCE:.0%}"
        if d.get("chan"):
            tu = f"AI tự chấm {d['confidence_ai']:.0%} → hạ còn {conf:.0%}" if "confidence_ai" in d else f"confidence {conf:.0%}"
            print(mau(f"   ⚠ {ngan(v['ten'])}: CẦN KIỂM TRA KỸ, không tạo đơn nháp ({d['chan']}; {tu})", VANG))
        else:
            d["ncc"] = ncc_hop_le[d["nha_cung_cap_id"]]
            print(mau(f"   ✓ {ngan(v['ten'])}: đặt {d['so_luong']} ở {d['ncc']['ten']}, confidence {conf:.0%}"
                      f" (trên ngưỡng {NGUONG_CONFIDENCE:.0%}, NCC có thật, số lượng hợp lý)", XANH))
        hop_le.append(d)
    return hop_le, tat_ca


# ---------- 5. So sánh với quy tắc cố định ----------

def so_sanh_quy_tac(du_lieu, quy_tac, tat_ca):
    dong, ghi_chu = [], []
    for v in du_lieu:
        qt = quy_tac_co_dinh(v, quy_tac)
        d = tat_ca.get(v["vat_tu_id"], {})
        if not qt:
            continue
        cot_qt = f"Đặt {qt['so_luong']} ở {qt['ncc']}" if qt["dat"] else "Chưa đặt"
        if d.get("chan"):
            cot_ai = "Bị giữ lại"
        elif d.get("can_dat"):
            cot_ai = f"Đặt {d['so_luong']} ở {d['ncc']['ten']}"
        else:
            cot_ai = "Chưa cần đặt"
        dong.append((ngan(v["ten"]), f"{so(qt['toi_thieu'])} / {so(qt['toi_da'])}", cot_qt, cot_ai))

        con = v["so_ngay_con_lai"]
        if not qt["dat"] and d.get("can_dat") and not d.get("chan"):
            ghi_chu.append(f"{ngan(v['ten'])}: quy tắc chưa đặt vì tồn {so(v['ton_hien_tai'])} vẫn trên mức tối thiểu "
                           f"{so(qt['toi_thieu'])}, nhưng cuối tuần dùng gấp "
                           f"{v['tieu_hao_tb_cuoi_tuan'] / max(v['tieu_hao_tb_ngay_thuong'], 1e-9):.1f} lần "
                           f"nên chỉ còn khoảng {con} ngày, mà {d['ncc']['ten']} giao mất {d['ncc']['so_ngay_giao']} ngày.")
        elif qt["dat"] and qt.get("het_truoc_khi_ve") and d.get("can_dat") and not d.get("chan") \
                and d["ncc"]["ten"] != qt["ncc"]:
            ghi_chu.append(f"{ngan(v['ten'])}: quy tắc đặt {qt['ncc']} (giao {qt['so_ngay_giao']} ngày) nhưng "
                           f"chỉ còn khoảng {con} ngày là hết, tiệm sẽ thiếu hàng. AI chọn {d['ncc']['ten']} "
                           f"giao {d['ncc']['so_ngay_giao']} ngày, chấp nhận giá cao hơn.")
    bang(("Vật tư", "Tối thiểu/đa", "Quy tắc cố định Odoo", "AI"), dong, (20, 12, 21, 20))
    for g in ghi_chu:
        print(mau(f"   ★ {g}", VANG))


# ---------- 6. Ghi về Odoo ----------

def ghi_ve_odoo(odoo, de_xuat, canh_bao, thieu_bom):
    model_sp = odoo.call("ir.model", "search", [["model", "=", "product.template"]], limit=1)[0]
    loai_viec = odoo.call("mail.activity.type", "search", [["category", "=", "default"]], limit=1)
    ket_qua = []
    # Dịch vụ thiếu BOM: luôn báo chủ tiệm bổ sung, dù AI có muốn đặt hàng hay không
    bi_giu = "".join(f"<p>• AI gợi ý đặt {d.get('so_luong')} {d['ten']}: <b>đã giữ lại, không tạo đơn nháp</b> "
                     f"(AI tự chấm {d.get('confidence_ai', d['confidence']):.0%}, hạ còn {d['confidence']:.0%}).</p>"
                     for d in de_xuat if d.get("chan"))
    for tmpl, ten in thieu_bom:
        odoo.call("mail.activity", "create", {
            "res_model_id": model_sp, "res_id": tmpl, "activity_type_id": loai_viec[0] if loai_viec else False,
            "summary": f"Cần kiểm tra kỹ: bổ sung BOM cho '{ten}'",
            "note": "".join(f"<p>{c}</p>" for c in canh_bao) + bi_giu
                    + "<p>Khai báo định mức vật tư cho dịch vụ này rồi chạy lại AI.</p>",
            "user_id": odoo.uid, "date_deadline": datetime.now(GIO_VN).strftime("%Y-%m-%d"),
        })
        print(mau(f"   → Tạo việc 'Cần kiểm tra kỹ: bổ sung BOM cho {ten}' cho chủ tiệm", VANG))
        ket_qua.append({"dich_vu": ten, "hanh_dong": "canh_bao_thieu_bom"})
    theo_ncc = defaultdict(list)
    for d in de_xuat:
        if d.get("chan") and thieu_bom:
            continue  # đã gộp vào việc 'bổ sung BOM' ở trên
        if d.get("chan"):
            tmpl = odoo.search_read("product.product", [["id", "=", d["vat_tu_id"]]], ["product_tmpl_id"])[0]["product_tmpl_id"][0]
            ghi_chu = (f"<p>AI gợi ý đặt {d.get('so_luong')} nhưng <b>không tạo đơn nháp</b>: {d['chan']}.</p>"
                       f"<p>Confidence: {d['confidence']:.0%}. Lý do AI: {d.get('ly_do', '')}</p>"
                       + "".join(f"<p>• {c}</p>" for c in canh_bao))
            odoo.call("mail.activity", "create", {
                "res_model_id": model_sp, "res_id": tmpl, "activity_type_id": loai_viec[0] if loai_viec else False,
                "summary": "Cần kiểm tra kỹ: đề xuất đặt hàng của AI", "note": ghi_chu,
                "user_id": odoo.uid, "date_deadline": datetime.now(GIO_VN).strftime("%Y-%m-%d"),
            })
            print(mau(f"   → Tạo việc 'Cần kiểm tra kỹ' cho chủ tiệm trên sản phẩm {d['ten']}", VANG))
            ket_qua.append({"vat_tu": d["ten"], "hanh_dong": "canh_bao"})
        else:
            theo_ncc[d["nha_cung_cap_id"]].append(d)

    for ncc_id, dong in theo_ncc.items():
        # Không tạo trùng nếu đã có đơn nháp AI cho vật tư này đang chờ duyệt
        dong = [d for d in dong if not odoo.call("purchase.order.line", "search_count", [
            ["product_id", "=", d["vat_tu_id"]], ["order_id.origin", "=", NGUON_AI],
            ["order_id.state", "in", ["draft", "sent"]]])]
        if not dong:
            print(mau("   · Đã có đơn nháp AI đang chờ chủ tiệm duyệt, không tạo thêm.", XAM))
            continue
        ly_do = "".join(f"<li><b>{d['ten']}</b>: {d['ly_do']} (confidence {d['confidence']:.0%})</li>" for d in dong)
        po = odoo.call("purchase.order", "create", {
            "partner_id": ncc_id, "origin": NGUON_AI,
            "note": "Đơn nháp do AI đề xuất. Chủ tiệm kiểm tra, sửa nếu cần rồi mới xác nhận.",
            "order_line": [(0, 0, {"product_id": d["vat_tu_id"], "product_qty": d["so_luong"],
                                   "price_unit": d["ncc"]["gia_mot_don_vi"],
                                   "name": f"{d['ten']} [AI đề xuất: {d['so_luong']}]"}) for d in dong],
        })
        # Tạo mail.message trực tiếp: message_post qua API sẽ hiện thẻ HTML thành chữ
        odoo.call("mail.message", "create", {
            "model": "purchase.order", "res_id": po, "message_type": "comment",
            "subtype_id": odoo.call("mail.message.subtype", "search", [["name", "=", "Note"]], limit=1,
                                    context={"lang": "en_US"})[0],
            "body": f"<p><b>Lý do AI đề xuất</b></p><ul>{ly_do}</ul>"
                    f"<p>AI chỉ tạo đơn nháp. Chủ tiệm là người xác nhận.</p>",
        })
        ten_po = odoo.search_read("purchase.order", [["id", "=", po]], ["name"])[0]["name"]
        print(mau(f"   → Tạo ĐƠN MUA NHÁP {ten_po} gửi {dong[0]['ncc']['ten']}: "
                  + ", ".join(f"{ngan(d['ten'])} x{d['so_luong']}" for d in dong), XANH))
        ket_qua.append({"don_mua": ten_po, "vat_tu": [d["ten"] for d in dong]})
    return ket_qua


# ---------- Báo cáo Human Override Rate ----------

def bao_cao(odoo):
    don = odoo.search_read("purchase.order", [["origin", "=", NGUON_AI]], ["name", "state", "order_line"])
    dem = defaultdict(int)
    for d in don:
        if d["state"] == "cancel":
            dem["Bác bỏ"] += 1
        elif d["state"] in ("draft", "sent"):
            dem["Đang chờ duyệt"] += 1
        else:
            dong = odoo.search_read("purchase.order.line", [["order_id", "=", d["id"]]], ["name", "product_qty"])
            sua = any((m := re.search(r"\[AI đề xuất: (\d+)\]", l["name"] or "")) and float(m.group(1)) != l["product_qty"]
                      for l in dong)
            dem["Duyệt nhưng có sửa" if sua else "Duyệt không sửa"] += 1
    da_xu_ly = len(don) - dem["Đang chờ duyệt"]
    print(mau(f"Tổng đơn nháp AI tạo: {len(don)}", DAM))
    for k in ["Duyệt không sửa", "Duyệt nhưng có sửa", "Bác bỏ", "Đang chờ duyệt"]:
        print(f"  {k:<20} {dem[k]}")
    if da_xu_ly:
        print(mau(f"Human Override Rate (sửa + bác bỏ / đã xử lý): "
                  f"{(dem['Duyệt nhưng có sửa'] + dem['Bác bỏ']) / da_xu_ly:.0%}", DAM))


# ---------- Chạy ----------

def main():
    env = doc_env()
    odoo = Odoo(env)
    if "--bao-cao" in sys.argv:
        bao_cao(odoo)
        return

    print("=" * 78)
    print(mau(f"AI LAYER: đề xuất đơn mua vật tư  |  Tiệm Nail X  |  chạy lúc {datetime.now(GIO_VN):%H:%M %d/%m/%Y}", DAM))
    print("=" * 78)

    buoc(1, "Đọc dữ liệu từ Odoo: tồn kho, lịch sử xuất kho 28 ngày, nhà cung cấp")
    vat_tu, ncc, xuat, quy_tac = doc_du_lieu(odoo)
    du_lieu = tinh_tieu_hao(vat_tu, ncc, xuat)
    dong = []
    for v in du_lieu:
        con = f"~{v['so_ngay_con_lai']} ngày" if v["so_ngay_con_lai"] is not None else "> 60 ngày"
        if v["so_ngay_con_lai"] is not None and v["so_ngay_con_lai"] <= 7:
            con = mau(con, VANG)
        dang_ve = f"+{so(v['dang_ve_tu_don_da_xac_nhan'])}" if v["dang_ve_tu_don_da_xac_nhan"] else ""
        dong.append((ngan(v["ten"]), f"{so(v['ton_hien_tai'])} {dang_ve}", so(v["tieu_hao_tb_ngay_thuong"]),
                     so(v["tieu_hao_tb_cuoi_tuan"]), con))
    bang(("Vật tư", "Tồn (+về)", "TB ngày thường", "TB cuối tuần", "Còn dùng"), dong, (20, 10, 14, 13, 10))
    print(mau("   (đơn vị: chai/hũ; tiêu hao trung bình mỗi ngày, code tính từ lịch sử xuất kho)", XAM))

    buoc(2, "Kiểm tra chất lượng dữ liệu (code, không phải AI)")
    canh_bao, thieu_bom = kiem_tra_du_lieu(odoo, du_lieu)
    print("\n".join(mau(f"   ⚠ {c}", VANG) for c in canh_bao) if canh_bao
          else mau("   ✓ Không phát hiện vấn đề: đủ 28 ngày dữ liệu, mọi dịch vụ đã bán đều có BOM", XANH))

    buoc(3, "Dữ liệu gửi sang AI")
    du_lieu_gui = {"vat_tu": du_lieu, "canh_bao_du_lieu": canh_bao}
    noi_dung_gui = json.dumps(du_lieu_gui, ensure_ascii=False)
    if TRINH_CHIEU:
        print(f"   Gửi {len(du_lieu)} vật tư. Mỗi vật tư gồm: tồn kho, tiêu hao trung bình, số ngày còn lại,")
        print("   giá và thời gian giao của từng nhà cung cấp (kèm câu so sánh do code viết sẵn).")
        print(mau("   KHÔNG gửi: tên khách, số điện thoại, lịch sử dịch vụ của khách, doanh thu, hoa hồng thợ.", XANH))
        print(mau("   Ví dụ phần gửi cho Gel base:", XAM))
        mau_gel = {k: v for k, v in du_lieu[0].items() if k != "nha_cung_cap"}
        print(mau("   " + json.dumps(mau_gel, ensure_ascii=False), XAM))
        print(mau(f"   Bản đầy đủ ({len(noi_dung_gui):,} ký tự) được lưu trong {NHAT_KY.name}.", XAM))
    else:
        print(json.dumps(du_lieu_gui, ensure_ascii=False, indent=2))

    buoc(4, f"AI trả về (model {env.get('OPENROUTER_MODEL')})")
    if TRINH_CHIEU:
        print(mau("   Đang hỏi AI...", XAM))
    try:
        noi_dung = goi_llm(env, du_lieu_gui)
    except Exception as e:  # noqa: BLE001
        print(mau(f"   ✗ Không gọi được AI: {e}", DO))
        print("   → Không tạo đơn. Chủ tiệm dùng quy tắc đặt hàng lại có sẵn của Kho (phương án dự phòng).")
        return
    try:
        tra_ve = doc_json(noi_dung)
        ten_theo_id = {v["vat_tu_id"]: v for v in du_lieu}
        dong = []
        for d in tra_ve:
            v = ten_theo_id.get(d.get("vat_tu_id"), {"ten": f"? id {d.get('vat_tu_id')}", "nha_cung_cap": []})
            ten_ncc = next((n["ten"] for n in v["nha_cung_cap"] if n["nha_cung_cap_id"] == d.get("nha_cung_cap_id")), "")
            dong.append((ngan(v["ten"]), "Đặt" if d.get("can_dat") else "Không",
                         d.get("so_luong") if d.get("can_dat") else "", ten_ncc,
                         f"{float(d.get('confidence', 0)):.0%}"))
        bang(("Vật tư", "Đặt?", "SL", "NCC", "Tin cậy"), dong, (20, 6, 4, 6, 8))
        print()
        for d in tra_ve:
            if d.get("can_dat"):
                v = ten_theo_id.get(d.get("vat_tu_id"), {"ten": "?"})
                print(f"   {mau(ngan(v['ten']) + ':', DAM)} {d.get('ly_do', '')}")
    except (json.JSONDecodeError, KeyError, TypeError):
        print(noi_dung)
    if not TRINH_CHIEU:
        print(mau("\n   JSON gốc: " + noi_dung, XAM))

    buoc(5, f"Code kiểm tra lại kết quả AI (ngưỡng tin cậy {NGUONG_CONFIDENCE:.0%})")
    de_xuat, tat_ca = kiem_tra_ket_qua(noi_dung, du_lieu, canh_bao)

    buoc(6, "So sánh: quy tắc đặt hàng lại có sẵn của Odoo vs AI")
    so_sanh_quy_tac(du_lieu, quy_tac, tat_ca)

    buoc(7, "Ghi về Odoo (chỉ tạo đơn NHÁP, không xác nhận)")
    ket_qua = ghi_ve_odoo(odoo, de_xuat, canh_bao, thieu_bom)
    if not ket_qua:
        print(mau("   · Không có gì cần ghi.", XAM))

    with NHAT_KY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"luc": datetime.now(GIO_VN).isoformat(), "du_lieu_gui": du_lieu_gui,
                            "ai_tra_ve": noi_dung, "ket_qua": ket_qua}, ensure_ascii=False) + "\n")
    print(mau(f"\n   Đã lưu nhật ký vào {NHAT_KY.name}. AI không xác nhận đơn mua nào: chủ tiệm duyệt trên Odoo.", XAM))


if __name__ == "__main__":
    main()
