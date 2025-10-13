import streamlit as st
import pandas as pd
import urllib.parse
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import qrcode
import re
from PIL import Image
from io import BytesIO
import difflib

# ---------- Page config ----------
st.set_page_config(page_title="QR Car Management", page_icon="🚗", layout="wide")

# ---------- Constants ----------
REQUIRED_COLUMNS = ["STT", "Họ tên", "Biển số", "Mã thẻ", "Mã đơn vị", "Tên đơn vị", "Chức vụ", "Số điện thoại", "Email"]

DON_VI_MAP = {
    "HCTH": "HCT", "TCCB": "TCC", "ĐTĐH": "DTD", "ĐTSĐH": "DTS", "KHCN": "KHC", "KHTC": "KHT",
    "QTGT": "QTG", "TTPC": "TTP", "ĐBCLGD&KT": "DBK", "CTSV": "CTS", "Trường Y": "TRY",
    "Trường Dược": "TRD", "Trường ĐD-KTYH": "TRK", "KHCB": "KHB", "RHM": "RHM", "YTCC": "YTC",
    "PK.CKRHM": "CKR", "TT.KCCLXN": "KCL", "TT.PTTN": "PTN", "TT.ĐTNLYT": "DTL", "TT.CNTT": "CNT",
    "TT.KHCN UMP": "KCU", "TT.YSHPT": "YSH", "Thư viện": "TV", "KTX": "KTX", "Tạp chí Y học": "TCY",
    "BV ĐHYD": "BVY", "TT. GDYH": "GDY", "VPĐ": "VPD"
}

# ---------- Helpers (shared) ----------
def normalize_plate(plate: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]', '', str(plate)).lower()

def format_name(name: str) -> str:
    return ' '.join(word.capitalize() for word in str(name).strip().split())

def dinh_dang_bien_so(bs: str) -> str:
    bs = re.sub(r"[^A-Z0-9]", "", str(bs).upper())
    if len(bs) == 8:
        return f"{bs[:3]}-{bs[3:6]}.{bs[6:]}"
    return bs

def to_native_ll(df: pd.DataFrame):
    out = []
    for _, row in df.iterrows():
        items = []
        for v in row.tolist():
            if pd.isna(v):
                items.append("")
            elif isinstance(v, (int, float)):
                if isinstance(v, float) and v.is_integer():
                    items.append(int(v))
                else:
                    items.append(float(v) if isinstance(v, float) else int(v))
            else:
                items.append(str(v))
        out.append(items)
    return out

def ensure_columns(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Thiếu cột bắt buộc: {', '.join(missing)}")
    return df[REQUIRED_COLUMNS].copy()

def resolve_ma_don_vi(ten_don_vi: str, ma_don_vi_cur: str = "") -> str:
    if str(ma_don_vi_cur).strip():
        return str(ma_don_vi_cur).strip().upper()
    name = str(ten_don_vi).strip()
    return DON_VI_MAP.get(name, "").upper()

def build_unit_counters(df_cur: pd.DataFrame) -> dict:
    counters = {}
    if "Mã thẻ" in df_cur.columns:
        for val in df_cur["Mã thẻ"].dropna().astype(str):
            m = re.match(r"^([A-Z]{3})(\d{3})$", val.strip().upper())
            if m:
                unit = m.group(1)
                num = int(m.group(2))
                counters[unit] = max(counters.get(unit, 0), num)
    return counters

def assign_codes_for_row(row: pd.Series, counters: dict) -> pd.Series:
    ma_dv = resolve_ma_don_vi(row.get("Tên đơn vị", ""), row.get("Mã đơn vị", ""))
    row["Mã đơn vị"] = ma_dv
    ma_the = str(row.get("Mã thẻ", "") or "").strip().upper()
    if not ma_dv:
        return row
    if not ma_the:
        cur = counters.get(ma_dv, 0) + 1
        counters[ma_dv] = cur
        row["Mã thẻ"] = f"{ma_dv}{cur:03d}"
    else:
        m = re.match(rf"^{ma_dv}(\d{{3}})$", ma_the)
        if m:
            counters[ma_dv] = max(counters.get(ma_dv, 0), int(m.group(1)))
        row["Mã thẻ"] = ma_the
    return row

def reindex_stt(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["STT"] = list(range(1, len(df) + 1))
    return df

# ---------- Lightweight AI helpers ----------
def fuzzy_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def fuzzy_search_df(df: pd.DataFrame, query: str, topk: int = 50):
    if df.empty or not str(query).strip():
        return df.copy()
    scores = []
    for idx, row in df.iterrows():
        s = 0.0
        s += 2.0 * fuzzy_ratio(query, row.get("Biển số", ""))
        s += fuzzy_ratio(query, row.get("Họ tên", ""))
        s += fuzzy_ratio(query, row.get("Mã thẻ", ""))
        s += 0.8 * fuzzy_ratio(query, row.get("Tên đơn vị", ""))
        s += 0.8 * fuzzy_ratio(query, row.get("Mã đơn vị", ""))
        s += 0.5 * fuzzy_ratio(query, row.get("Số điện thoại", ""))
        s += 0.6 * fuzzy_ratio(query, row.get("Email", ""))
        scores.append((idx, s))
    scores.sort(key=lambda x: x[1], reverse=True)
    idxs = [i for i, _ in scores[:topk]]
    out = df.loc[idxs].copy()
    out["__score__"] = [sc for _, sc in scores[:topk]]
    return out.sort_values("__score__", ascending=False)

def simple_query_parser(q: str):
    q = str(q).strip()
    tokens = re.findall(r"[\wÀ-ỹ]+", q, flags=re.IGNORECASE)
    keys = {"unit": None, "plate": None, "name": None, "email": None, "phone": None}
    m_email = re.search(r"[\w\.-]+@[\w\.-]+", q)
    if m_email: keys["email"] = m_email.group(0)
    m_phone = re.search(r"(0\d{8,11})", q)
    if m_phone: keys["phone"] = m_phone.group(1)
    best_unit = None; best_score = 0
    for t in tokens:
        for name in DON_VI_MAP.keys():
            sc = fuzzy_ratio(t, name)
            if sc > best_score and sc > 0.75:
                best_unit = name; best_score = sc
    keys["unit"] = best_unit
    plate_like = [t for t in tokens if re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]", t)]
    if plate_like:
        keys["plate"] = plate_like[0]
    if not keys["email"] and not keys["phone"] and not keys["plate"]:
        if tokens:
            keys["name"] = max(tokens, key=len)
    return keys

def filter_with_keys(df: pd.DataFrame, keys: dict):
    cur = df.copy()
    applied = False
    if keys.get("unit"):
        cur = cur[cur["Tên đơn vị"].astype(str).str.contains(keys["unit"], case=False, regex=False)]
        applied = True
    if keys.get("email"):
        cur = cur[cur["Email"].astype(str).str.contains(keys["email"], case=False, regex=False)]
        applied = True
    if keys.get("phone"):
        cur = cur[cur["Số điện thoại"].astype(str).str.contains(keys["phone"], case=False, regex=False)]
        applied = True
    if keys.get("plate"):
        norm = normalize_plate(keys["plate"])
        cur["__norm"] = cur["Biển số"].astype(str).apply(normalize_plate)
        cur = cur[cur["__norm"].str.contains(norm, na=False)]
        cur = cur.drop(columns=["__norm"], errors="ignore")
        applied = True
    if keys.get("name"):
        cur = cur[cur["Họ tên"].astype(str).str.contains(keys["name"], case=False, regex=False)]
        applied = True
    return cur, applied

# ---------- Google Sheet init ----------
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
if "google_service_account" not in st.secrets:
    st.error("❌ Thiếu thông tin xác thực Google Service Account trong secrets.toml.")
    st.stop()
try:
    creds_dict = dict(st.secrets["google_service_account"])
    pk = str(creds_dict.get("private_key", "")).strip()
    if "-----BEGIN" not in pk:
        pk = pk.replace("\\n", "\n")
    pk = pk.replace("\r\n", "\n")
    creds_dict["private_key"] = pk
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
except Exception as e:
    st.error(f"❌ Lỗi khởi tạo Google Credentials: {e}")
    st.stop()

SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
try:
    sheet = client.open_by_key(SHEET_ID).worksheet("Sheet1")
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ---------- Sidebar & Title ----------
st.sidebar.image("ump_logo.png", width=120)
st.sidebar.markdown("---")
st.markdown("<h1 style='text-align:center; color:#004080;'>🚗 QR Car Management</h1>", unsafe_allow_html=True)

# ---------- Load data ----------
@st.cache_data(ttl=60)
def load_df():
    try:
        data = sheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ Không thể tải dữ liệu xe: {e}")
        st.stop()

if "df" not in st.session_state:
    st.session_state.df = load_df()
df = st.session_state.df

# ---------- Menu ----------
menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📱 Mã QR xe",
    "📤 Xuất ra Excel",
    "📥 Tải dữ liệu lên",
    "📊 Thống kê xe theo đơn vị",
    "🤖 Trợ lý AI"
]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=0)

# ---------- Features ----------
if choice == "📋 Xem danh sách":
    st.subheader("📋 Danh sách xe đã đăng ký")
    df_show = df.copy()
    df_show["Biển số"] = df_show["Biển số"].apply(dinh_dang_bien_so)
    st.dataframe(df_show, use_container_width=True)

elif choice == "🔍 Tìm kiếm xe":
    st.subheader("🔍 Tìm kiếm xe theo biển số (hỗ trợ gần đúng)")
    bien_so_input = st.text_input("Nhập biển số xe cần tìm")
    allow_fuzzy = st.checkbox("Cho phép gợi ý gần đúng nếu không khớp tuyệt đối", value=True)
    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df_tmp = df.copy()
        df_tmp["Biển số chuẩn hóa"] = df_tmp["Biển số"].astype(str).apply(normalize_plate)
        ket_qua = df_tmp[df_tmp["Biển số chuẩn hóa"] == bien_so_norm]
        if ket_qua.empty and allow_fuzzy:
            st.info("Không khớp tuyệt đối. Thử gợi ý gần đúng…")
            top = fuzzy_search_df(df, bien_so_input, topk=20)
            if top.empty:
                st.warning("🚫 Không tìm thấy kết quả.")
            else:
                st.success(f"✅ Gợi ý gần đúng (top {len(top)}):")
                st.dataframe(top.drop(columns=["__score__"], errors="ignore"), use_container_width=True)
        elif ket_qua.empty:
            st.warning("🚫 Không tìm thấy xe nào khớp với biển số đã nhập.")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

elif choice == "➕ Đăng ký xe mới":
    st.subheader("📋 Đăng ký xe mới")
    df_current = df.copy()
    ten_don_vi = st.selectbox("Chọn đơn vị", list(DON_VI_MAP.keys()))
    ma_don_vi = DON_VI_MAP[ten_don_vi]
    col1, col2 = st.columns(2)
    with col1:
        ho_ten_raw = st.text_input("Họ tên")
        bien_so_raw = st.text_input("Biển số xe")
    with col2:
        chuc_vu_raw = st.text_input("Chức vụ")
        so_dien_thoai = st.text_input("Số điện thoại")
        email = st.text_input("Email")
    ho_ten = format_name(ho_ten_raw)
    chuc_vu = format_name(chuc_vu_raw)
    bien_so = dinh_dang_bien_so(bien_so_raw)
    bien_so_da_dang_ky = df_current["Biển số"].dropna().apply(dinh_dang_bien_so)
    if bien_so in bien_so_da_dang_ky.values:
        st.error("🚫 Biển số này đã được đăng ký trước đó!")
    elif so_dien_thoai and not str(so_dien_thoai).startswith("0"):
        st.warning("⚠️ Số điện thoại phải bắt đầu bằng số 0.")
    elif ho_ten == "" or bien_so == "":
        st.warning("⚠️ Vui lòng nhập đầy đủ thông tin.")
    else:
        counters = build_unit_counters(df_current)
        cur = counters.get(ma_don_vi, 0) + 1
        counters[ma_don_vi] = cur
        ma_the = f"{ma_don_vi}{cur:03d}"
        st.markdown(f"🔐 **Mã thẻ tự sinh:** `{ma_the}`")
        st.markdown(f"🏢 **Mã đơn vị:** `{ma_don_vi}`")
        if st.button("📥 Đăng ký"):
            try:
                sheet.append_row([
                    int(len(df_current) + 1),
                    ho_ten,
                    bien_so,
                    ma_the,
                    ma_don_vi,
                    ten_don_vi,
                    chuc_vu,
                    so_dien_thoai,
                    email
                ])
                st.success(f"✅ Đã đăng ký xe cho `{ho_ten}` với mã thẻ: `{ma_the}`")
                st.toast("🎉 Dữ liệu đã được ghi vào Google Sheet!")
                st.session_state.df = load_df()
            except Exception as e:
                st.error(f"❌ Lỗi ghi dữ liệu: {e}")

elif choice == "✏️ Cập nhật xe":
    st.subheader("✏️ Cập nhật xe")
    bien_so_input = st.text_input("Nhập biển số xe cần cập nhật")
    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df_tmp = df.copy()
        df_tmp["Biển số chuẩn hóa"] = df_tmp["Biển số"].astype(str).apply(normalize_plate)
        ket_qua = df_tmp[df_tmp["Biển số chuẩn hóa"] == bien_so_norm]
        if ket_qua.empty:
            st.error("❌ Không tìm thấy biển số xe!")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)
            idx_np = ket_qua.index[0]
            index = int(idx_np)
            row = ket_qua.iloc[0]
            st.markdown("### 📝 Nhập thông tin mới để cập nhật")
            col1, col2 = st.columns(2)
            with col1:
                ho_ten_moi = st.text_input("Họ tên", value=str(row["Họ tên"]))
                bien_so_moi = st.text_input("Biển số xe", value=str(row["Biển số"]))
                ten_don_vi_moi = st.text_input("Tên đơn vị", value=str(row["Tên đơn vị"]))
                ma_don_vi_moi = st.text_input("Mã đơn vị", value=str(row["Mã đơn vị"]))
            with col2:
                chuc_vu_moi = st.text_input("Chức vụ", value=str(row["Chức vụ"]))
                so_dien_thoai_moi = st.text_input("Số điện thoại", value=str(row["Số điện thoại"]))
                email_moi = st.text_input("Email", value=str(row["Email"]))
            if st.button("Cập nhật"):
                try:
                    try:
                        stt_val = int(row.get("STT", ""))
                    except Exception:
                        stt_val = str(row.get("STT", ""))
                    payload = [
                        stt_val,
                        ho_ten_moi,
                        bien_so_moi,
                        str(row["Mã thẻ"]),
                        ma_don_vi_moi,
                        ten_don_vi_moi,
                        chuc_vu_moi,
                        so_dien_thoai_moi,
                        email_moi
                    ]
                    sheet.update(f"A{index+2}:I{index+2}", [payload])
                    st.success("✅ Đã cập nhật thông tin xe thành công!")
                    st.session_state.df = load_df()
                except Exception as e:
                    st.error(f"❌ Lỗi cập nhật: {e}")

elif choice == "🗑️ Xóa xe":
    st.subheader("🗑️ Xóa xe khỏi danh sách")
    bien_so_input = st.text_input("Nhập biển số xe cần xóa")
    if bien_so_input:
        try:
            bien_so_norm = normalize_plate(bien_so_input)
            df_tmp = df.copy()
            df_tmp["Biển số chuẩn hóa"] = df_tmp["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df_tmp[df_tmp["Biển số chuẩn hóa"] == bien_so_norm]
            if ket_qua.empty:
                st.error("❌ Không tìm thấy biển số xe!")
            else:
                st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
                st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)
                idx_np = ket_qua.index[0]
                index = int(idx_np)
                row = ket_qua.iloc[0]
                if st.button("Xác nhận xóa"):
                    sheet.delete_rows(int(index) + 2)
                    st.success(f"🗑️ Đã xóa xe có biển số `{row['Biển số']}` thành công!")
                    st.session_state.df = load_df()
        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "📱 Mã QR xe":
    st.subheader("📱 Mã QR xe")
    bien_so_input = st.text_input("📋 Nhập biển số xe để tạo mã QR")
    if bien_so_input:
        try:
            bien_so_norm = normalize_plate(bien_so_input)
            df_tmp = df.copy()
            df_tmp["Biển số chuẩn hóa"] = df_tmp["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df_tmp[df_tmp["Biển số chuẩn hóa"] == bien_so_norm]
            if ket_qua.empty:
                st.error(f"⚠️ Không tìm thấy xe có biển số: {bien_so_input}")
            else:
                row = ket_qua.iloc[0]
                link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(bien_so_norm)}"
                img = qrcode.make(link)
                buf = BytesIO()
                img.save(buf)
                buf.seek(0)
                st.image(buf.getvalue(), caption=f"Mã QR cho xe {row['Biển số']}", width=200)
                st.download_button(
                    label="📥 Tải mã QR",
                    data=buf.getvalue(),
                    file_name=f"QR_{row['Biển số']}.png",
                    mime="image/png"
                )
                st.success("✅ Thông tin xe:")
                st.dataframe(row.to_frame().T, use_container_width=True)
        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "📤 Xuất ra Excel":
    st.subheader("📤 Tải danh sách xe dưới dạng Excel")
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DanhSachXe')
    processed_data = output.getvalue()
    st.download_button(
        label="📥 Tải Excel",
        data=processed_data,
        file_name="DanhSachXe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

elif choice == "📥 Tải dữ liệu lên":
    st.subheader("📥 Tải dữ liệu từ file lên Google Sheet")
    st.markdown("Tải file **.xlsx** hoặc **.csv** theo mẫu định dạng chuẩn. Bạn **có thể để trống** cột **Mã thẻ** và **Mã đơn vị** — hệ thống sẽ tự sinh dựa trên **Tên đơn vị**.")
    tmpl = pd.DataFrame(columns=REQUIRED_COLUMNS)
    buf_tmpl = BytesIO()
    with pd.ExcelWriter(buf_tmpl, engine='openpyxl') as writer:
        tmpl.to_excel(writer, index=False, sheet_name='Template')
    st.download_button("📄 Tải mẫu Excel", data=buf_tmpl.getvalue(), file_name="Template_DanhSachXe.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    file = st.file_uploader("Chọn file dữ liệu (.xlsx hoặc .csv)", type=["xlsx", "csv"])
    mode = st.selectbox("Chọn chế độ", ["Thêm (append)", "Thay thế toàn bộ (replace all)", "Cập nhật theo Biển số (upsert)"])
    auto_stt = st.checkbox("🔢 Đánh lại STT sau khi ghi", value=True)
    dry_run = st.checkbox("🧪 Chạy thử (không ghi)", value=True)
    if file is not None:
        try:
            df_up = pd.read_csv(file) if file.name.lower().endswith(".csv") else pd.read_excel(file)
            df_up = ensure_columns(df_up)
            st.success(f"✅ Đã đọc {len(df_up)} dòng từ file.")
            st.dataframe(df_up.head(20), use_container_width=True)
            df_cur = load_df()
            counters = build_unit_counters(df_cur)
            def fill_missing_codes(_df: pd.DataFrame) -> pd.DataFrame:
                _df = _df.copy()
                rows = []
                for _, r in _df.iterrows():
                    r = assign_codes_for_row(r, counters)
                    rows.append(r)
                out = pd.DataFrame(rows, columns=_df.columns)
                if (out["Mã đơn vị"].astype(str).str.len() == 0).any():
                    missing_rows = out[out["Mã đơn vị"].astype(str).str.len() == 0].index.tolist()
                    raise ValueError(f"Không thể suy ra 'Mã đơn vị' từ 'Tên đơn vị' ở các dòng: {', '.join(str(i+2) for i in missing_rows)}")
                return out
            if st.button("🚀 Thực thi"):
                if dry_run:
                    st.info("🔎 Chế độ chạy thử: không ghi dữ liệu. Bỏ chọn để ghi thật.")
                else:
                    if mode == "Thêm (append)":
                        df_to_write = fill_missing_codes(df_up)
                        values = to_native_ll(df_to_write)
                        for row_vals in values:
                            sheet.append_row(row_vals)
                        st.success(f"✅ Đã thêm {len(values)} dòng.")
                    elif mode == "Thay thế toàn bộ (replace all)":
                        df_to_write = fill_missing_codes(df_up)
                        sheet.clear()
                        sheet.update("A1", [REQUIRED_COLUMNS])
                        values = to_native_ll(df_to_write)
                        if values:
                            sheet.update(f"A2:I{len(values)+1}", values)
                        st.success(f"✅ Đã thay thế toàn bộ dữ liệu ({len(df_to_write)} dòng).")
                    else:
                        df_up2 = fill_missing_codes(df_up)
                        df_cur["__norm"] = df_cur["Biển số"].astype(str).apply(normalize_plate)
                        df_up2["__norm"] = df_up2["Biển số"].astype(str).apply(normalize_plate)
                        updated, inserted = 0, 0
                        for _, r in df_up2.iterrows():
                            norm = r["__norm"]
                            match = df_cur[df_cur["__norm"] == norm]
                            payload = [r.get(c, "") for c in REQUIRED_COLUMNS]
                            norm_payload = []
                            for x in payload:
                                if pd.isna(x):
                                    norm_payload.append("")
                                elif isinstance(x, (int, float)):
                                    if isinstance(x, float) and x.is_integer():
                                        norm_payload.append(int(x))
                                    else:
                                        norm_payload.append(float(x) if isinstance(x, float) else int(x))
                                else:
                                    norm_payload.append(str(x))
                            if not match.empty:
                                idx = int(match.index[0])
                                sheet.update(f"A{idx+2}:I{idx+2}", [norm_payload])
                                updated += 1
                            else:
                                sheet.append_row(norm_payload)
                                inserted += 1
                        st.success(f"✅ Upsert xong: cập nhật {updated} • thêm mới {inserted}.")
                    if auto_stt:
                        try:
                            df_all = load_df()
                            df_all = reindex_stt(df_all)
                            sheet.clear()
                            sheet.update("A1", [REQUIRED_COLUMNS])
                            values_all = to_native_ll(df_all)
                            if values_all:
                                sheet.update(f"A2:I{len(values_all)+1}", values_all)
                            st.toast("🔢 Đã đánh lại STT 1..N.")
                        except Exception as e:
                            st.warning(f"⚠️ Không thể đánh lại STT tự động: {e}")
                    st.toast("🔄 Làm mới dữ liệu hiển thị...")
                    st.session_state.df = load_df()
        except Exception as e:
            st.error(f"❌ Lỗi khi tải/ghi dữ liệu: {e}")

elif choice == "📊 Thống kê xe theo đơn vị":
    st.markdown("## 📊 Dashboard thống kê xe theo đơn vị")
    df_stats = df.copy()
    ten_day_du = {
        "HCTH": "Phòng Hành Chính Tổng hợp",
        "TCCB": "Phòng Tổ chức Cán bộ",
        "ĐTĐH": "Phòng Đào tạo Đại học",
        "ĐTSĐH": "Phòng Đào tạo Sau đại học",
        "KHCN": "Phòng Khoa học Công nghệ",
        "KHTC": "Phòng Kế hoạch Tài chính",
        "QTGT": "Phòng Quản trị Giáo tài",
        "TTPC": "Phòng Thanh tra Pháp chế",
        "ĐBCLGD&KT": "Phòng Đảm bảo chất lượng GD và Khảo thí",
        "CTSV": "Phòng Công tác sinh viên",
        "KHCB": "Khoa Khoa học Cơ bản",
        "RHM": "Khoa Răng hàm mặt",
        "YTCC": "Khoa Y tế Công cộng",
        "PK.CKRHM": "Phòng khám RHM",
        "TT.KCCLXN": "Trung tâm Kiểm chuẩn CLXN",
        "TT.KHCN UMP": "Trung tâm KHCN UMP",
        "TT.YSHPT": "Trung tâm Y sinh học phân tử",
        "KTX": "Ký túc xá",
        "BV ĐHYD": "Bệnh viện ĐHYD",
        "TT.PTTN": "Trung tâm PTTN",
        "TT. GDYH": "Trung tâm GDYH",
        "VPĐ": "VP Đoàn thể",
        "Trường Y": "Trường Y",
        "Trường Dược": "Trường Dược",
        "Trường ĐD-KTYH": "Trường ĐD-KTYH",
        "Thư viện": "Thư viện",
        "Tạp chí Y học": "Tạp chí Y học"
    }
    thong_ke = df_stats.groupby("Tên đơn vị").size().reset_index(name="Số lượng xe")
    thong_ke = thong_ke.sort_values(by="Số lượng xe", ascending=False)
    thong_ke["Tên đầy đủ"] = thong_ke["Tên đơn vị"].apply(lambda x: ten_day_du.get(x, x))
    import plotly.express as px
    fig = px.bar(thong_ke, x="Tên đơn vị", y="Số lượng xe", color="Tên đơn vị", text="Số lượng xe", title="📈 Biểu đồ số lượng xe theo đơn vị")
    fig.update_traces(textposition="outside")
    fig.update_layout(xaxis=dict(tickfont=dict(size=14, family="Arial", color="black", weight="bold")), showlegend=False, height=600)
    col = st.columns([0.1, 0.9])
    with col[1]:
        st.plotly_chart(fig, use_container_width=True)
    st.markdown("#### 📋 Bảng thống kê chi tiết")
    thong_ke_display = thong_ke[["Tên đầy đủ", "Số lượng xe"]].rename(columns={"Tên đầy đủ": "Tên đơn vị"})
    thong_ke_display.index = range(1, len(thong_ke_display) + 1)
    st.dataframe(thong_ke_display, use_container_width=True)

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
