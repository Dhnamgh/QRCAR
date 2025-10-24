
# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import re, io, zipfile, urllib.parse, time, random
from io import BytesIO

# ================== CONFIG ==================
SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
WORKSHEET_NAME = "Sheet 1"
BASE_URL_QR = "https://dhnamgh.github.io/car/index.html"

# Columns
REQUIRED_COLUMNS = ["STT","Họ tên","Biển số","Mã thẻ","Mã đơn vị","Tên đơn vị","Chức vụ","Số điện thoại","Email"]

# Đổi theo quy ước
DON_VI_MAP = {
    "HCTH": "HCT", "TCCB": "TCC", "ĐTĐH": "DTD", "ĐTSĐH": "DTS", "KHCN": "KHC", "KHTC": "KHT",
    "QTGT": "QTG", "TTPC": "TTP", "ĐBCLGD&KT": "DBK", "CTSV": "CTS", "Trường Y": "TRY",
    "Trường Dược": "TRD", "Trường ĐD-KTYH": "TRK", "KHCB": "KHB", "RHM": "RHM", "YTCC": "YTC",
    "PK.CKRHM": "CKR", "TT.KCCLXN": "KCL", "TT.PTTN": "PTN", "TT.ĐTNLYT": "DTL", "TT.CNTT": "CNT",
    "TT.KHCN UMP": "KCU", "TT.YSHPT": "YSH", "Thư viện": "TV", "KTX": "KTX", "Tạp chí Y học": "TCY",
    "BV ĐHYD": "BVY", "TT. GDYH": "GDY", "VPĐ": "VPD", "YHCT": "YHC", "HTQT": "HTQ"
}
UNIT_ALIASES = {
    "bvdhyd": "BV ĐHYD", "bv dhyd": "BV ĐHYD", "bvđhyd":"BV ĐHYD", "bvdvyd":"BV ĐHYD", "bv đvyd":"BV ĐHYD",
    "rhm": "RHM", "rmh": "RHM",
}
CARD_PAD = 3  # TRY001 ...

# ================== AUTH / GSPREAD ==================
@st.cache_resource(show_spinner=False)
def get_sheet():
    # expects secrets["google_service_account"] block
    info = st.secrets["google_service_account"]
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(WORKSHEET_NAME)
    return ws

def gs_retry(func, *args, max_retries=7, base=0.6, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 500, 503):
                time.sleep(base*(2**i) + random.uniform(0,0.5)); continue
            msg = str(e).lower()
            if any(t in msg for t in ["quota","rate limit","internal error","timeout"]):
                time.sleep(base*(2**i) + random.uniform(0,0.5)); continue
            raise
    raise RuntimeError("Google Sheets write failed after multiple retries")

def read_df():
    ws = get_sheet()
    values = gs_retry(ws.get_all_values)
    if not values:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    header = values[0]
    rows = values[1:]
    if not header:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    # pad/truncate rows to header length
    rows = [r + [""]*(len(header)-len(r)) if len(r)<len(header) else r[:len(header)] for r in rows]
    df = pd.DataFrame(rows, columns=header)
    return df

# ================== HELPERS ==================
def _canon(s):
    import unicodedata
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

_CANON2STD = {
    "stt":"STT","hoten":"Họ tên","ten":"Họ tên","hovaten":"Họ tên","name":"Họ tên",
    "bienso":"Biển số","bien so":"Biển số","licenseplate":"Biển số","plate":"Biển số",
    "mathe":"Mã thẻ","ma the":"Mã thẻ","ma_the":"Mã thẻ",
    "madonvi":"Mã đơn vị","ma don vi":"Mã đơn vị","tendonvi":"Tên đơn vị","ten don vi":"Tên đơn vị",
    "chucvu":"Chức vụ","sodienthoai":"Số điện thoại","dienthoai":"Số điện thoại","phone":"Số điện thoại",
    "email":"Email"
}

def coerce_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    rename, seen = {}, set()
    for c in df.columns:
        std = _CANON2STD.get(_canon(c))
        if std and std not in seen:
            rename[c] = std; seen.add(std)
    out = df.rename(columns=rename).copy()
    for c in REQUIRED_COLUMNS:
        if c not in out.columns:
            out[c] = ""
    return out

def normalize_plate(s: str) -> str:
    s = "" if s is None else str(s).upper()
    return re.sub(r"[^A-Z0-9]", "", s)

def safe_format_plate(s: str) -> str:
    return "" if s is None else str(s).upper()

def make_qr_bytes(data: str) -> bytes:
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(data); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = BytesIO(); img.save(bio, format="PNG")
    return bio.getvalue()

def ensure_codes_all(df_up: pd.DataFrame, df_cur: pd.DataFrame) -> pd.DataFrame:
    df_up = coerce_columns(df_up).dropna(how="all").reset_index(drop=True)
    df_cur = coerce_columns(df_cur if df_cur is not None else pd.DataFrame(columns=REQUIRED_COLUMNS))

    import unicodedata, re as _re
    def _canon_name(s):
        s = "" if s is None else str(s)
        s = unicodedata.normalize("NFD", s)
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
        s = _re.sub(r"\s+", " ", s).strip().lower()
        return s

    def _is_blank(v) -> bool:
        if v is None: return True
        s = str(v).strip()
        if s == "": return True
        return s.lower() in {"nan","none","null","na","n/a","-","_"}

    canon_from_const = { _canon_name(k): v for k, v in DON_VI_MAP.items() }

    unit_map_sheet = {}
    if not df_cur.empty and all(c in df_cur.columns for c in ["Tên đơn vị","Mã đơn vị"]):
        for _, r in df_cur[["Tên đơn vị","Mã đơn vị"]].dropna().iterrows():
            name = str(r["Tên đơn vị"]).strip().upper()
            code = str(r["Mã đơn vị"]).strip().upper()
            if name and code:
                unit_map_sheet[name] = code

    used_units = set(df_cur.get("Mã đơn vị", pd.Series(dtype=str)).dropna().astype(str).str.upper())

    def _slug_unit(name: str) -> str:
        if not isinstance(name, str) or not name.strip(): return "DV"
        words = _re.findall(r"[A-Za-zÀ-ỹ0-9]+", name.strip(), flags=_re.UNICODE)
        if not words: return "DV"
        initials = "".join(w[0] for w in words).upper()
        if len(initials) <= 1:
            flat = _re.sub(r"[^A-Za-z0-9]", "", name.upper())
            return (flat or "DV")[:8]
        return initials[:8]

    def resolve_unit_code(ten):
        if _is_blank(ten):
            return _slug_unit("")
        ckey = _canon_name(ten)
        if ckey in UNIT_ALIASES:
            std_name = UNIT_ALIASES[ckey]
            return DON_VI_MAP.get(std_name, _slug_unit(std_name))
        if ckey in canon_from_const:
            return canon_from_const[ckey]
        key_up = str(ten).strip().upper()
        if key_up in unit_map_sheet:
            return unit_map_sheet[key_up]
        base, cand, k = _slug_unit(str(ten)), None, 2
        cand = base
        while cand.upper() in used_units:
            cand = f"{base}{k}"; k += 1
        used_units.add(cand.upper())
        return cand

    # seed per unit from df_cur
    per_unit_seed = {}
    if not df_cur.empty and all(c in df_cur.columns for c in ["Mã đơn vị","Mã thẻ"]):
        for uc, grp in df_cur.groupby(df_cur["Mã đơn vị"].astype(str).str.upper(), dropna=True):
            mx = 0
            for v in grp["Mã thẻ"].dropna().astype(str):
                m = re.match(rf"^{re.escape(uc)}(\d+)$", v.strip(), flags=re.IGNORECASE)
                if m:
                    try: mx = max(mx, int(m.group(1)))
                    except: pass
            per_unit_seed[uc] = mx

    # fill
    for i, r in df_up.iterrows():
        ten_dv = r.get("Tên đơn vị", "")
        target_uc = resolve_unit_code(ten_dv)
        df_up.at[i, "Mã đơn vị"] = target_uc

        ma_the = r.get("Mã thẻ","")
        if _is_blank(ma_the):
            uc = str(target_uc).strip().upper()
            if uc not in per_unit_seed:
                per_unit_seed[uc] = 0
            per_unit_seed[uc] += 1
            df_up.at[i, "Mã thẻ"] = f"{uc}{str(per_unit_seed[uc]).zfill(CARD_PAD)}"

    return df_up

def write_bulk(sheet, df_cur: pd.DataFrame, df_new: pd.DataFrame, chunk_rows=200, pause=0.5):
    df_cur = coerce_columns(df_cur)
    df_new = ensure_codes_all(df_new, df_cur)
    values = []
    for _, row in df_new.iterrows():
        ll = []
        for c in REQUIRED_COLUMNS:
            v = row.get(c, "")
            if pd.isna(v): v = ""
            ll.append(str(v))
        values.append(ll)
    start = len(df_cur) + 2
    written = 0
    for i in range(0, len(values), chunk_rows):
        block = values[i:i+chunk_rows]
        end_row = start+i+len(block)-1
        rng = f"A{start+i}:I{end_row}"
        gs_retry(sheet.update, rng, block)
        written += len(block)
        time.sleep(pause)
    return written

def build_qr_zip(df, base_url: str) -> bytes:
    files = []
    for _, r in df.iterrows():
        vid = str(r.get("Mã thẻ","")).strip()
        if not vid and "Biển số" in df.columns:
            vid = normalize_plate(r.get("Biển số",""))
        if not vid:
            continue
        url = f"{base_url}?id={urllib.parse.quote(vid)}"
        png = make_qr_bytes(url)
        unit = str(r.get("Mã đơn vị","")).strip().upper() or "NO_UNIT"
        files.append((f"{unit}/{vid}.png", png))
    if not files:
        return b""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    buf.seek(0)
    return buf.getvalue()

# ================== GATES ==================
def _get_query_params():
    try:
        return st.query_params
    except Exception:
        return st.experimental_get_query_params()

def is_qr_mode() -> bool:
    q = _get_query_params()
    raw = q.get("id", "")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return bool(str(raw).strip())

def gate_app():
    if is_qr_mode():
        return True
    if st.session_state.get("_app_ok"):
        return True
    pw = st.text_input("🔒 Nhập mật khẩu vào ứng dụng", type="password", key="_app_pw")
    if pw:
        if pw == st.secrets["app_password"]:
            st.session_state["_app_ok"] = True
            st.rerun()
        else:
            st.error("Mật khẩu sai."); st.stop()
    st.stop()

gate_app()

def qr_gate_and_show(df_show: pd.DataFrame):
    q = _get_query_params()
    raw_id = q.get("id", "")
    if isinstance(raw_id, list):
        raw_id = raw_id[0] if raw_id else ""
    id_ = str(raw_id).strip()
    if not id_:
        return False

    QR_SECRET = st.secrets.get("QR_PASSWORD") or st.secrets.get("qr_password")
    if QR_SECRET is None:
        st.error("Thiếu secret: QR_PASSWORD."); st.stop()

    if not st.session_state.get("_qr_ok"):
        pw = st.text_input("🔑 Nhập mật khẩu để xem thông tin xe", type="password", key="_qr_pw")
        if pw:
            if pw == QR_SECRET:
                st.session_state["_qr_ok"] = True; st.rerun()
            else:
                st.error("❌ Mật khẩu QR sai."); st.stop()
        st.stop()

    sel = df_show[df_show.get("Mã thẻ","").astype(str).str.upper() == id_.upper()] \
          if "Mã thẻ" in df_show.columns else df_show.iloc[0:0]
    if sel.empty and "Biển số" in df_show.columns:
        sel = df_show[df_show["Biển số"].astype(str).map(normalize_plate) == normalize_plate(id_)]
    if sel.empty:
        st.error("❌ Không tìm thấy xe.")
    else:
        st.success("✅ Xác thực OK – Thông tin xe:")
        st.dataframe(sel, hide_index=True, use_container_width=True)
    st.stop()

# ================== APP ==================
st.set_page_config(page_title="QR Car Management", page_icon="🚗", layout="wide")

# Load dữ liệu gốc
try:
    df = read_df()
except Exception as e:
    st.error(f"Lỗi đọc Google Sheet: {e}")
    df = pd.DataFrame(columns=REQUIRED_COLUMNS)

# Sidebar menu
menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📥 Tải dữ liệu lên",
    "📤 Xuất ra Excel",
    "📊 Thống kê xe theo đơn vị",
    "🎁 Tạo mã QR hàng loạt",
    "🤖 Trợ lý AI"
]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=0)

# ---------- Xem danh sách ----------
if choice == "📋 Xem danh sách":
    st.subheader("📋 Danh sách xe đã đăng ký")
    df_show = coerce_columns(df.copy())
    if "Biển số" in df_show.columns:
        df_show["Biển số"] = df_show["Biển số"].apply(safe_format_plate)
    qr_gate_and_show(df_show)  # nếu có ?id=... thì chỉ hiển thị 1 xe
    st.dataframe(df_show, hide_index=True, use_container_width=True)

# ---------- Tìm kiếm ----------
elif choice == "🔍 Tìm kiếm xe":
    st.subheader("🔍 Tìm kiếm xe")
    df_s = coerce_columns(df.copy())
    q = st.text_input("Nhập Mã thẻ hoặc Biển số")
    if q:
        qn = normalize_plate(q)
        res = df_s[df_s.get("Mã thẻ","").astype(str).str.upper()==q.upper()]
        if res.empty and "Biển số" in df_s.columns:
            res = df_s[df_s["Biển số"].astype(str).map(normalize_plate)==qn]
        st.dataframe(res, hide_index=True, use_container_width=True)
    else:
        st.dataframe(df_s.head(50), hide_index=True, use_container_width=True)

# ---------- Đăng ký mới ----------
elif choice == "➕ Đăng ký xe mới":
    st.subheader("➕ Đăng ký xe mới")
    ws = get_sheet()
    df_cur = coerce_columns(df.copy())
    ho_ten = st.text_input("Họ tên")
    bien_so = st.text_input("Biển số")
    ten_dv = st.text_input("Tên đơn vị")
    chuc_vu = st.text_input("Chức vụ")
    so_dt = st.text_input("Số điện thoại")
    email = st.text_input("Email")
    if st.button("Đăng ký"):
        try:
            rec = pd.DataFrame([{
                "STT":"", "Họ tên":ho_ten, "Biển số":bien_so, "Mã thẻ":"",
                "Mã đơn vị":"", "Tên đơn vị":ten_dv, "Chức vụ":chuc_vu,
                "Số điện thoại":so_dt, "Email":email
            }])
            rec = ensure_codes_all(rec, df_cur)
            rows = write_bulk(ws, df_cur, rec)
            st.success(f"✅ Đã đăng ký xe cho `{ho_ten}` với mã thẻ: `{rec.iloc[0]['Mã thẻ']}`")

            vid = rec.iloc[0]["Mã thẻ"] or normalize_plate(bien_so)
            url = f"{BASE_URL_QR}?id={urllib.parse.quote(str(vid))}"
            png = make_qr_bytes(url)
            st.image(png, caption=f"QR cho {bien_so}", width=200)
            st.download_button("📥 Tải mã QR", data=png, file_name=f"QR_{vid}.png", mime="image/png")

        except Exception as e:
            st.error(f"❌ Lỗi: {e}")

# ---------- Cập nhật xe ----------
elif choice == "✏️ Cập nhật xe":
    st.subheader("✏️ Cập nhật xe")
    st.info("Chức năng rút gọn: dùng tab '📥 Tải dữ liệu lên' (Upsert) để cập nhật hàng loạt.")

# ---------- Xóa xe ----------
elif choice == "🗑️ Xóa xe":
    st.subheader("🗑️ Xóa xe")
    st.info("Chức năng rút gọn: vui lòng quản trị trực tiếp trên Google Sheet.")

# ---------- Tải dữ liệu lên ----------
elif choice == "📥 Tải dữ liệu lên":
    st.subheader("📥 Tải dữ liệu từ Excel/CSV")
    ws = get_sheet()
    up = st.file_uploader("Chọn tệp Excel (.xlsx) hoặc CSV", type=["xlsx","csv"])
    mode = st.selectbox("Chế độ ghi dữ liệu", ["Thêm (append)","Thay thế toàn bộ (replace all)","Upsert"])
    dry_run = st.checkbox("🔎 Chạy thử (không ghi Google Sheets)")

    if up is not None:
        try:
            if up.name.lower().endswith(".csv"):
                df_up = pd.read_csv(up, dtype=str, keep_default_na=False)
            else:
                df_up = pd.read_excel(up, dtype=str)
        except Exception as e:
            st.error(f"❌ Không đọc được tệp: {e}"); st.stop()

        df_up = coerce_columns(df_up)
        st.dataframe(df_up.head(10), hide_index=True, use_container_width=True)

        if st.button("🚀 Thực thi"):
            try:
                df_cur = coerce_columns(df.copy())
                df_to_write = ensure_codes_all(df_up.copy(), df_cur)

                if dry_run:
                    st.info("🔎 Chạy thử: không ghi Google Sheets.")
                else:
                    if mode == "Thêm (append)":
                        rows = write_bulk(ws, df_cur, df_to_write)
                        st.success(f"✅ Đã thêm {rows} dòng.")
                    elif mode == "Thay thế toàn bộ (replace all)":
                        gs_retry(ws.clear)
                        gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])
                        values = []
                        for _, row in df_to_write.iterrows():
                            values.append([str(row.get(c,"")) for c in REQUIRED_COLUMNS])
                        if values:
                            gs_retry(ws.update, f"A2:I{1+len(values)}", values)
                        st.success(f"✅ Đã thay thế toàn bộ dữ liệu ({len(df_to_write)} dòng).")
                    else:  # Upsert
                        df_cur2 = coerce_columns(read_df())
                        df_to_write = df_to_write.copy()
                        def _keyify(df0):
                            k1 = df0.get("Mã thẻ", pd.Series([""]*len(df0))).astype(str).str.upper().str.strip()
                            k2 = df0["Biển số"].astype(str).map(normalize_plate) if "Biển số" in df0.columns else pd.Series([""]*len(df0))
                            return k1.where(k1!="", k2)
                        df_cur2["__KEY__"] = _keyify(df_cur2)
                        df_to_write["__KEY__"] = _keyify(df_to_write)
                        key_to_row = {k:i for i,k in df_cur2["__KEY__"].items() if str(k).strip()!=""}
                        updated=inserted=0
                        for _, r in df_to_write.iterrows():
                            key = str(r["__KEY__"]).strip()
                            payload = [str(r.get(c,"")) for c in REQUIRED_COLUMNS]
                            if key and key in key_to_row:
                                idx0 = int(key_to_row[key])
                                gs_retry(ws.update, f"A{idx0+2}:I{idx0+2}", [payload]); updated+=1
                            else:
                                gs_retry(ws.append_row, payload); inserted+=1
                        st.success(f"✅ Upsert xong: cập nhật {updated} • thêm mới {inserted}.")

                zip_bytes = build_qr_zip(df_to_write, BASE_URL_QR)
                if zip_bytes:
                    st.download_button("⬇️ Tải ZIP QR (phân theo đơn vị)",
                                       data=zip_bytes, file_name="qr_xe_theo_don_vi.zip",
                                       mime="application/zip")
                    st.caption("Quét QR sẽ mở GitHub Pages và app yêu cầu mật khẩu QR (từ secrets).")

            except Exception as e:
                st.error(f"❌ Lỗi xử lý/ghi dữ liệu: {e}")

# ---------- Xuất ra Excel ----------
elif choice == "📤 Xuất ra Excel":
    st.subheader("📤 Xuất ra Excel")
    bio = BytesIO()
    coerce_columns(df).to_excel(bio, index=False)
    st.download_button("⬇️ Tải Excel", data=bio.getvalue(), file_name="ds_xe.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------- Thống kê ----------
elif choice == "📊 Thống kê xe theo đơn vị":
    st.subheader("📊 Thống kê xe theo đơn vị")
    d = coerce_columns(df.copy())
    st.bar_chart(d.groupby("Mã đơn vị").size())

# ---------- QR hàng loạt ----------
elif choice == "🎁 Tạo mã QR hàng loạt":
    st.subheader("🎁 Tạo mã QR hàng loạt")
    df_qr = coerce_columns(df.copy())
    for col in ["Mã thẻ","Biển số","Mã đơn vị"]:
        if col not in df_qr.columns: df_qr[col] = ""
    if st.button("⚡ Tạo ZIP QR"):
        zip_bytes = build_qr_zip(df_qr, BASE_URL_QR)
        if zip_bytes:
            st.download_button("⬇️ Tải ZIP QR (phân theo đơn vị)",
                               data=zip_bytes, file_name="qr_xe_theo_don_vi.zip",
                               mime="application/zip")
            st.success(f"✅ Đã tạo {len(df_qr)} QR.")
        else:
            st.warning("Không có bản ghi hợp lệ.")

# ---------- Trợ lý AI ----------
elif choice == "🤖 Trợ lý AI":
    st.subheader("🤖 Trợ lý AI")
    st.info("Tính năng đang được đơn giản hóa trong bản này.")


elif choice == "🤖 Trợ lý AI":
    st.subheader("🤖 Trợ lý AI (AI nhẹ, không dùng API)")
    q = st.text_input("Gõ câu tự nhiên: ví dụ 'xe của Trường Y tên Hùng', '59A1', 'email @ump.edu.vn', '0912345678'…")
    if q:
        keys = simple_query_parser(q)
        with st.expander("Xem cách app hiểu câu hỏi (keys)", expanded=False):
            st.json(keys)
        filtered, applied = filter_with_keys(df, keys)
        if applied and not filtered.empty:
            st.success(f"✅ Lọc theo ý hiểu được {len(filtered)} dòng. Sắp xếp gợi ý thông minh…")
            ranked = fuzzy_search_df(filtered, q, topk=50)
            st.dataframe(ranked.drop(columns=["__score__"], errors="ignore"), use_container_width=True)
        else:
            st.info("Không lọc được rõ ràng từ câu hỏi. Thử gợi ý gần đúng toàn bộ…")
            top = fuzzy_search_df(df, q, topk=50)
            if top.empty:
                st.warning("🚫 Không tìm thấy kết quả.")
            else:
                st.dataframe(top.drop(columns=["__score__"], errors="ignore"), use_container_width=True)

# ---------- Footer ----------
st.markdown("""
<hr style='margin-top:50px; margin-bottom:20px;'>

<div style='font-size:14px; line-height:1.6; text-align:center; color:#444;'>
    <strong>Phòng Hành chính Tổng Hợp - Đại học Y Dược Thành phố Hồ Chí Minh</strong><br>
    Địa chỉ: 217 Hồng Bàng, Phường Chợ Lớn, TP. Hồ Chí Minh<br>
    ĐT: (+84-28) 3855 8411 - (+84-28) 3853 7949 - (+84-28) 3855 5780<br>
    Fax: (+84-28) 3855 2304<br>
    Email: <a href='mailto:hanhchinh@ump.edu.vn'>hanhchinh@ump.edu.vn</a><br><br>
    <em>Copyright © 2025 Bản quyền thuộc về Phòng Hành chính Tổng Hợp - Đại học Y Dược Thành phố Hồ Chí Minh</em>
</div>
""", unsafe_allow_html=True)
