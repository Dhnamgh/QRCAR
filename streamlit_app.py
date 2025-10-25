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
import zipfile
import io
def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Chuẩn hóa tên cột về string, bỏ cột Unnamed, reset index (ẩn cột 0,1,2...)."""
    if df is None or df.empty:
        return pd.DataFrame()
    # tên cột -> string
    cols = [(str(c).strip() if c is not None else "") for c in df.columns]
    df = df.copy()
    df.columns = cols
    # bỏ cột rác dạng 'Unnamed: ...'
    keep = [c for c in df.columns if not re.match(r"^\s*Unnamed", c)]
    df = df.loc[:, keep]
    # bỏ cột index hiển thị
    return df.reset_index(drop=True)

# ==== Google Sheets connector (chuẩn cho SHEET_ID và Sheet 1) ====
from google.oauth2.service_account import Credentials
import gspread

SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
WORKSHEET_NAME = "Sheet1"

@st.cache_resource(show_spinner=False)
def get_sheet():
    """Mở đúng Google Sheet ID + tab 'Sheet 1'. Tự tạo tab + header nếu chưa có."""
    try:
        info = st.secrets["google_service_account"]
    except KeyError:
        st.error("Thiếu block [google_service_account] trong secrets.")
        st.stop()

    # Kiểm tra private_key có định dạng đúng không (có BEGIN/END và xuống dòng)
    pk = info.get("private_key", "")
    if not isinstance(pk, str) or "BEGIN PRIVATE KEY" not in pk:
        st.error("private_key trong secrets sai định dạng. Hãy dùng triple quotes và giữ nguyên xuống dòng.")
        st.stop()

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)

    try:
        ws = sh.worksheet(WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows="2000", cols="20")
        # tạo header mặc định nếu sheet mới tinh
        gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])

    return ws
# ---------- Google Sheets helper ----------
import time, random

# ---------- Ghi Google Sheet theo block (siêu nhanh) ----------
def _df_to_values(df, columns):
    vals = []
    for _, r in df.iterrows():
        row = []
        for c in columns:
            v = r.get(c, "")
            if pd.isna(v): v = ""
            row.append(str(v))
        vals.append(row)
    return vals

def write_bulk_block(ws, df_cur: pd.DataFrame, df_new: pd.DataFrame,
                     columns=None, chunk_rows=500, pause=0.5):
    """Append cả DataFrame thành các block lớn để tránh quota."""
    if columns is None:
        columns = REQUIRED_COLUMNS
    df_new = df_new.copy()
    values = _df_to_values(df_new, columns)
    if not values:
        return 0

    start = len(df_cur) + 2  # +1 header, +1 bắt đầu từ dòng 2
    written = 0
    for i in range(0, len(values), chunk_rows):
        block = values[i:i+chunk_rows]
        end_row = start + i + len(block) - 1
        rng = f"A{start+i}:I{end_row}"
        gs_retry(ws.update, rng, block)
        written += len(block)
        if pause: time.sleep(pause)
    return written

def gs_retry(func, *args, max_retries=7, base=0.6, **kwargs):
    """
    Thực thi hàm Google Sheets 
    """
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            # các lỗi tạm thời thường gặp
            if any(t in msg for t in ["quota", "rate limit", "timeout", "internal error", "503", "500", "429"]):
                time.sleep(base * (2 ** i) + random.uniform(0, 0.5))
                continue
            raise
    raise RuntimeError(f"Google Sheets API failed sau {max_retries} lần thử")

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

# ---------- Helpers ----------
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

def make_qr_bytes(url: str) -> bytes:
    img = qrcode.make(url)
    buf = BytesIO()
    img.save(buf)
    buf.seek(0)
    return buf.getvalue()

# ---------- Lightweight "AI" helpers ----------
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

# ---------- Secrets: mật khẩu ứng dụng ----------
APP_PASSWORD = st.secrets.get("app_password") or st.secrets.get("qr_password")
if not APP_PASSWORD:
    st.error("❌ Thiếu mật khẩu ứng dụng trong secrets (app_password hoặc qr_password).")
    st.stop()

# Thay bằng Sheet của bạn
SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
try:
    ws = get_sheet()
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ---------- Load data ----------
ws = get_sheet()
@st.cache_data(ttl=60)
def load_df():
    try:
        data = ws.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ Không thể tải dữ liệu xe: {e}")
        st.stop()

# ---------- QR GUARD (cho luồng quét QR) ----------
bien_so_url = st.query_params.get("id", "")
if bien_so_url:
    # Ẩn sidebar & nav để người quét không thấy các tab
    st.markdown("""
        <style>
            [data-testid="stSidebar"] {display: none !important;}
            [data-testid="stSidebarNav"] {display: none !important;}
            [data-testid="stSidebarContent"] {display: none !important;}
        </style>
    """, unsafe_allow_html=True)

    st.subheader("🔍 Tra cứu xe bằng mã QR")
    mat_khau = st.text_input("🔑 Nhập mật khẩu để xem thông tin xe", type="password")
    if mat_khau:
        if mat_khau.strip() != str(APP_PASSWORD):
            st.error("❌ Sai mật khẩu!")
        else:
            df = load_df()
            df_tmp = df.copy()
            df_tmp["__norm"] = df_tmp["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df_tmp[df_tmp["__norm"] == normalize_plate(bien_so_url)]
            if ket_qua.empty:
                st.error(f"❌ Không tìm thấy xe có biển số: {bien_so_url}")
            else:
                st.success("✅ Thông tin xe:")
                st.dataframe(ket_qua.drop(columns=["__norm"]), use_container_width=True)
        st.stop()
    else:
        st.info("Vui lòng nhập mật khẩu để xem thông tin xe.")
        st.stop()

# ---------- App login gate ----------
if "auth_ok" not in st.session_state:
    st.session_state.auth_ok = False

# Logo + tiêu đề
st.markdown("<h1 style='text-align:center; color:#004080;'>🚗 QR Car Management</h1>", unsafe_allow_html=True)

if not st.session_state.auth_ok:
    st.markdown("### 🔐 Đăng nhập")
    pwd = st.text_input("Mật khẩu", type="password")
    if st.button("Đăng nhập"):
        if pwd.strip() == str(APP_PASSWORD):
            st.session_state.auth_ok = True
            st.success("✅ Đăng nhập thành công.")
        else:
            st.error("❌ Sai mật khẩu!")
    st.stop()

# ---------- Sau khi đăng nhập: sidebar + dữ liệu ----------
st.sidebar.image("ump_logo.png", width=120)
st.sidebar.markdown("---")

if "df" not in st.session_state:
    st.session_state.df = load_df()
df = st.session_state.df

# ---------- Menu sau đăng nhập ----------
menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📥 Tải dữ liệu lên",
    "🎁 Tạo mã QR hàng loạt",
    "📤 Xuất ra Excel",
    "📊 Thống kê xe theo đơn vị",
    "🤖 Trợ lý AI"
]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=0)

# ---------- Các tính năng ----------
if choice == "📋 Xem danh sách":
    st.subheader("📋 Danh sách xe đã đăng ký")

    # Chuẩn hoá hiển thị, LOẠI CỘT RÁC, ẨN INDEX
    df_show = df.copy()
    # bỏ các cột "Unnamed: 0" nếu có
    df_show = clean_df(df_show)

    if "Biển số" in df_show.columns:
        df_show["Biển số"] = df_show["Biển số"].apply(dinh_dang_bien_so)

    # === Chế độ xem theo QR (?id=...) chỉ hỏi QR_PASSWORD và hiện đúng 1 xe ===
    def _get_query_params():
        try:
            return st.query_params
        except Exception:
            return st.experimental_get_query_params()

    def _qr_gate_and_show(df_list: pd.DataFrame):
        q = _get_query_params()
        raw = q.get("id", "")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        vid = str(raw).strip()
        if not vid:
            return False  # không ở chế độ QR

        QR_SECRET = st.secrets.get("QR_PASSWORD") or st.secrets.get("qr_password")
        if QR_SECRET is None:
            st.error("Thiếu secret: QR_PASSWORD."); st.stop()

        if not st.session_state.get("_qr_ok"):
            pw = st.text_input("🔑 Nhập mật khẩu để xem thông tin xe", type="password", key="_qr_pw")
            if pw:
                if pw == QR_SECRET:
                    st.session_state["_qr_ok"] = True
                    st.rerun()
                else:
                    st.error("❌ Mật khẩu QR sai.")
                    st.stop()
            st.stop()

        # Ưu tiên Mã thẻ (không phân biệt hoa thường), fallback Biển số đã chuẩn hoá
        sel = df_list[df_list.get("Mã thẻ", "").astype(str).str.upper() == vid.upper()] \
              if "Mã thẻ" in df_list.columns else df_list.iloc[0:0]
        if sel.empty and "Biển số" in df_list.columns:
            sel = df_list[df_list["Biển số"].astype(str).map(normalize_plate) == normalize_plate(vid)]

        if sel.empty:
            st.error("❌ Không tìm thấy xe.")
        else:
            st.success("✅ Xác thực OK – Thông tin xe:")
            st.dataframe(sel.reset_index(drop=True), hide_index=True, use_container_width=True)
        st.stop()

    _qr_gate_and_show(df_show)  # nếu có ?id=..., dừng tab ở đây

    st.dataframe(df_show, hide_index=True, use_container_width=True)

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
    if st.button("📥 Đăng ký"):
        if bien_so in bien_so_da_dang_ky.values:
            st.error("🚫 Biển số này đã được đăng ký trước đó!")
        elif so_dien_thoai and not str(so_dien_thoai).startswith("0"):
            st.warning("⚠️ Số điện thoại phải bắt đầu bằng số 0.")
        elif ho_ten == "" or bien_so == "":
            st.warning("⚠️ Vui lòng nhập đầy đủ thông tin.")
        else:
            try:
                # Auto mã thẻ
                counters = build_unit_counters(df_current)
                cur = counters.get(ma_don_vi, 0) + 1
                ma_the = f"{ma_don_vi}{cur:03d}"
                gs_retry(ws.append_row, [
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
                # Tạo QR cho xe vừa đăng ký
                norm = normalize_plate(bien_so)
                link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(norm)}"
                qr_png = make_qr_bytes(link)
                st.image(qr_png, caption=f"QR cho {bien_so}", width=200)
                st.download_button("📥 Tải mã QR", data=qr_png, file_name=f"QR_{bien_so}.png", mime="image/png")
                st.caption("Quét mã sẽ yêu cầu mật khẩu trước khi xem thông tin.")
                # Refresh
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
                    gs_retry(ws.update, f"A{index+2}:I{index+2}", [payload])
                    st.success("✅ Đã cập nhật thông tin xe thành công!")
                    # Tạo QR cho xe sau cập nhật (dùng biển số mới)
                    norm = normalize_plate(bien_so_moi)
                    link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(norm)}"
                    qr_png = make_qr_bytes(link)
                    st.image(qr_png, caption=f"QR cho {bien_so_moi}", width=200)
                    st.download_button("📥 Tải mã QR", data=qr_png, file_name=f"QR_{bien_so_moi}.png", mime="image/png")
                    st.caption("Quét mã sẽ yêu cầu mật khẩu trước khi xem thông tin.")
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
                    gs_retry(ws.delete_rows, int(index) + 2)
                    st.success(f"🗑️ Đã xóa xe có biển số `{row['Biển số']}` thành công!")
                    st.session_state.df = load_df()
        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "📥 Tải dữ liệu lên":
    st.subheader("📥 Tải dữ liệu từ Excel/CSV")

    # ✅ luôn lấy đúng worksheet theo SHEET_ID/WORKSHEET_NAME
    ws = get_sheet()

    up = st.file_uploader("Chọn tệp Excel (.xlsx) hoặc CSV", type=["xlsx", "csv"])
    mode = st.selectbox("Chế độ ghi dữ liệu", ["Thêm (append)", "Thay thế toàn bộ (replace all)", "Upsert"])
    dry_run = st.checkbox("🔎 Chạy thử (không ghi Google Sheets)")

    if up is not None:
        # ---- Đọc file người dùng upload ----
        try:
            if up.name.lower().endswith(".csv"):
                df_up = pd.read_csv(up, dtype=str, keep_default_na=False)
            else:
                df_up = pd.read_excel(up, dtype=str)
        except Exception as e:
            st.error(f"❌ Không đọc được tệp: {e}")
            st.stop()

        # ---- Làm sạch: bỏ cột rác 'Unnamed:*', ẩn index, đảm bảo đủ cột chuẩn ----
        df_up = clean_df(df_up)
        for c in REQUIRED_COLUMNS:
            if c not in df_up.columns:
                df_up[c] = ""

        # (tuỳ chọn) Chuẩn hoá tên đơn vị hay bị nhập lệch
        if "Tên đơn vị" in df_up.columns:
            alias = {
                "BV ĐVYD": "BV ĐHYD",
                "BVÐHYD": "BV ĐHYD",
                "BV DHYD": "BV ĐHYD",
                "RMH": "RHM",
                "rhm": "RHM",
            }
            df_up["Tên đơn vị"] = df_up["Tên đơn vị"].astype(str).str.replace("Ð", "Đ").str.replace("đ", "Đ")
            df_up["Tên đơn vị"] = df_up["Tên đơn vị"].replace(alias)

        st.info(f"Đã nạp {len(df_up)} dòng. Xem nhanh 10 dòng đầu:")
        st.dataframe(df_up.head(10), hide_index=True, use_container_width=True)

        if st.button("🚀 Thực thi"):
            try:
                # ---- Đọc hiện trạng sheet để seed số tăng theo đơn vị ----
                cur_vals = gs_retry(ws.get_all_values)
                if not cur_vals:
                    gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])  # tạo header nếu sheet trống
                    df_cur = pd.DataFrame(columns=REQUIRED_COLUMNS)
                else:
                    header = cur_vals[0]
                    rows = cur_vals[1:]
                    rows = [r + [""]*(len(header)-len(r)) if len(r) < len(header) else r[:len(header)] for r in rows]
                    df_cur = pd.DataFrame(rows, columns=header)
                    for c in REQUIRED_COLUMNS:
                        if c not in df_cur.columns:
                            df_cur[c] = ""

                # ---- TỰ SINH MÃ ĐƠN VỊ + MÃ THẺ cho dữ liệu upload ----
                counters = build_unit_counters(df_cur)
                df_to_write = df_up.apply(lambda r: assign_codes_for_row(r, counters), axis=1)
                df_to_write = df_to_write[REQUIRED_COLUMNS].copy()

                if dry_run:
                    st.info("🔎 Chạy thử: không ghi Google Sheets.")
                else:
                    if mode == "Thêm (append)":
                        # 👉 GHI THEO BLOCK (nhanh, không chạm quota)
                        added = write_bulk_block(ws, df_cur, df_to_write, columns=REQUIRED_COLUMNS)
                        st.success(f"✅ Đã thêm {added} dòng.")

                    elif mode == "Thay thế toàn bộ (replace all)":
                        gs_retry(ws.clear)
                        gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])
                        vals = _df_to_values(df_to_write, REQUIRED_COLUMNS)
                        if vals:
                            gs_retry(ws.update, f"A2:I{1+len(vals)}", vals)
                        st.success(f"✅ Đã thay thế toàn bộ dữ liệu ({len(df_to_write)} dòng).")

                    else:  # Upsert — update theo nhóm liên tiếp + append theo block
                        df_cur2 = df_cur.copy()

                        def _keyify(d):
                            k1 = d.get("Mã thẻ", pd.Series([""]*len(d))).astype(str).str.upper().str.strip()
                            k2 = d["Biển số"].astype(str).map(normalize_plate) if "Biển số" in d.columns else pd.Series([""]*len(d))
                            return k1.where(k1 != "", k2)

                        df_cur2["__KEY__"] = _keyify(df_cur2)
                        df_to_write["__KEY__"] = _keyify(df_to_write)
                        key_to_row = {k: i for i, k in df_cur2["__KEY__"].items() if str(k).strip() != ""}

                        updates, inserts = [], []
                        for _, r in df_to_write.iterrows():
                            key = str(r["__KEY__"]).strip()
                            payload = [str(r.get(c, "")) for c in REQUIRED_COLUMNS]
                            if key and key in key_to_row:
                                idx0 = int(key_to_row[key])  # vị trí trong sheet (tính cả header)
                                updates.append((idx0+2, payload))  # +2 vì header ở dòng 1
                            else:
                                inserts.append(payload)

                        # 1) UPDATE theo nhóm liên tiếp (giảm request)
                        updates.sort(key=lambda x: x[0])
                        grp, prev = [], None
                        for rownum, payload in updates:
                            if prev is None or rownum == prev + 1:
                                grp.append((rownum, payload))
                            else:
                                rng = f"A{grp[0][0]}:I{grp[-1][0]}"
                                gs_retry(ws.update, rng, [p for _, p in grp])
                                grp = [(rownum, payload)]
                            prev = rownum
                        if grp:
                            rng = f"A{grp[0][0]}:I{grp[-1][0]}"
                            gs_retry(ws.update, rng, [p for _, p in grp])

                        # 2) APPEND theo block (rất nhanh)
                        if inserts:
                            start = len(df_cur2) + 2
                            for i in range(0, len(inserts), 500):
                                blk = inserts[i:i+500]
                                end_row = start + i + len(blk) - 1
                                rng = f"A{start+i}:I{end_row}"
                                gs_retry(ws.update, rng, blk)

                        st.success(f"✅ Upsert xong: cập nhật {len(updates)} • thêm mới {len(inserts)}.")

                # ---- Hiển thị kết quả mẫu (ẩn index, KHÔNG trùng lặp) ----
                st.dataframe(df_to_write.head(20).reset_index(drop=True),
                             hide_index=True, use_container_width=True)

            except Exception as e:
                st.error(f"❌ Lỗi xử lý/ghi dữ liệu: {e}")


elif choice == "🎁 Tạo mã QR hàng loạt":
    st.subheader("🎁 Tạo mã QR hàng loạt")

    BASE_URL_QR = "https://dhnamgh.github.io/car/index.html"  # GH Pages của bạn

    src_opt = st.radio("Chọn nguồn dữ liệu", ["Toàn bộ danh sách", "Danh sách đang lọc"], horizontal=True)

    if src_opt == "Danh sách đang lọc" and 'df_show' in locals():
        df_qr = df_show.copy()
    else:
        df_qr = df.copy()

    # Làm sạch & ẨN INDEX
    df_qr = clean_df(df_qr)

    for col in ["Mã thẻ", "Biển số", "Mã đơn vị"]:
        if col not in df_qr.columns:
            df_qr[col] = ""

    st.info(f"Mỗi QR sẽ mở: {BASE_URL_QR}?id=<MãThẻ>")

    if st.button("⚡ Tạo ZIP mã QR"):
        import zipfile, io, urllib.parse

        files = []
        for _, r in df_qr.iterrows():
            vid = str(r.get("Mã thẻ", "")).strip()
            if not vid and "Biển số" in df_qr.columns:
                vid = normalize_plate(r.get("Biển số", ""))
            if not vid:
                continue
            url = f"{BASE_URL_QR}?id={urllib.parse.quote(vid)}"
            png = make_qr_bytes(url)
            unit = str(r.get("Mã đơn vị", "")).strip().upper() or "NO_UNIT"
            files.append((f"{unit}/{vid}.png", png))

        if not files:
            st.warning("Không có bản ghi hợp lệ để tạo QR.")
        else:
            bio = io.BytesIO()
            with zipfile.ZipFile(bio, "w", zipfile.ZIP_STORED) as zf:
                for name, data in files:
                    zf.writestr(name, data)
            bio.seek(0)
            st.download_button(
                "⬇️ Tải ZIP QR (phân theo đơn vị)",
                data=bio.getvalue(),
                file_name="qr_xe_theo_don_vi.zip",
                mime="application/zip"
            )
            st.success(f"✅ Đã tạo {len(files)} QR.")


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
    fig = px.bar(thong_ke, x="Tên đơn vị", y="Số lượng xe", color="Tên đơn vị", text="Số lượng xe",
                 title="📈 Biểu đồ số lượng xe theo đơn vị")
    fig.update_traces(textposition="outside")
    fig.update_layout(showlegend=False, height=600)
    col = st.columns([0.1, 0.9])
    with col[1]:
        st.plotly_chart(fig, use_container_width=True)
    st.markdown("#### 📋 Bảng thống kê chi tiết")
    thong_ke_display = thong_ke[["Tên đầy đủ", "Số lượng xe"]].rename(columns={"Tên đầy đủ": "Tên đơn vị"})
    thong_ke_display.index = range(1, len(thong_ke_display) + 1)
    st.dataframe(thong_ke_display, use_container_width=True)

elif choice == "🤖 Trợ lý AI":
    st.subheader("🤖 Trợ lý AI")
    q = st.text_input("Gõ câu ngắn, AI hiểu ngôn ngữ tự nhiên: ví dụ 'xe của Trường Y tên Hùng', '59A1', '0912345678'…")
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
