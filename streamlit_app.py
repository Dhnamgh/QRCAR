# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import urllib.parse
import gspread
from oauth2client.service_account import ServiceAccountCredentials  # vẫn giữ để tương thích nếu cần fallback
import qrcode
import re
from PIL import Image
from io import BytesIO
import difflib
import zipfile
import io
import time, random
# --- helper lấy biến secret bất chấp viết hoa/thường/thừa khoảng trắng ---
def _get_secret(*names: str) -> str:
    # Chuẩn hóa key: bỏ khoảng trắng, hạ thường, thay '-' thành '_'
    def norm(s): return str(s).strip().lower().replace(" ", "").replace("-", "_")
    # quét st.secrets theo khóa chuẩn hóa
    wanted = {norm(n) for n in names}
    for k, v in st.secrets.items():
        if norm(k) in wanted:
            return str(v)
    return ""

# ==========================
# CẤU HÌNH CHUNG & HỖ TRỢ
# ==========================
st.set_page_config(page_title="QR Car Management", page_icon="🚗", layout="wide")

REQUIRED_COLUMNS = ["STT", "Họ tên", "Biển số", "Mã thẻ", "Mã đơn vị", "Tên đơn vị", "Chức vụ", "Số điện thoại", "Email"]
DON_VI_MAP = {
    "HCTH": "HCT", "TCCB": "TCC", "ĐTĐH": "DTD", "ĐTSĐH": "DTS", "KHCN": "KHC", "KHTC": "KHT",
    "QTGT": "QTG", "TTPC": "TTP", "ĐBCLGD&KT": "DBK", "CTSV": "CTS", "Trường Y": "TRY",
    "Trường Dược": "TRD", "Trường ĐD-KTYH": "TRK", "KHCB": "KHB", "RHM": "RHM", "YTCC": "YTC",
    "PK.CKRHM": "CKR", "TT.KCCLXN": "KCL", "TT.PTTN": "PTN", "TT.ĐTNLYT": "DTL", "TT.CNTT": "CNT",
    "TT.KHCN UMP": "KCU", "TT.YSHPT": "YSH", "Thư viện": "TV", "KTX": "KTX", "Tạp chí Y học": "TCY",
    "BV ĐHYD": "BVY", "TT. GDYH": "GDY", "VPĐ": "VPD", "YHCT": "YHC", "HTQT": "HTQ"
}

# Sheet/Worksheet dùng cố định
SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
WORKSHEET_NAME = "Sheet1"  # đúng tên sheet trong gg sheet

# ----- Helpers bảng -----
def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Đổi tên cột về str, bỏ cột Unnamed, reset index."""
    if df is None or df.empty:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    cols = [(str(c).strip() if c is not None else "") for c in df.columns]
    df = df.copy()
    df.columns = cols
    keep = [c for c in df.columns if not re.match(r"^\s*Unnamed", c)]
    df = df.loc[:, keep]
    return df.reset_index(drop=True)

def normalize_plate(plate: str) -> str:
    return re.sub(r'[^a-zA-Z0-9]', '', str(plate)).lower()

def format_name(name: str) -> str:
    return ' '.join(word.capitalize() for word in str(name).strip().split())

def dinh_dang_bien_so(bs: str) -> str:
    bs = re.sub(r"[^A-Z0-9]", "", str(bs).upper())
    if len(bs) == 8:
        return f"{bs[:3]}-{bs[3:6]}.{bs[6:]}"
    return bs

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

def gs_retry(func, *args, max_retries=7, base=0.6, **kwargs):
    """Retry nhẹ nhàng khi dính quota/timeout 429/5xx."""
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if any(t in msg for t in ["quota", "rate limit", "timeout", "internal error", "503", "500", "429"]):
                time.sleep(base * (2 ** i) + random.uniform(0, 0.5))
                continue
            raise
    raise RuntimeError(f"Google Sheets API failed sau {max_retries} lần thử")

def write_bulk_block(ws, df_cur: pd.DataFrame, df_new: pd.DataFrame,
                     columns=None, chunk_rows=500, pause=0.5):
    """Append cả DataFrame theo block để tránh quota."""
    if columns is None:
        columns = REQUIRED_COLUMNS
    df_new = df_new.copy()
    values = _df_to_values(df_new, columns)
    if not values:
        return 0
    start = len(df_cur) + 2  # + header
    written = 0
    for i in range(0, len(values), chunk_rows):
        block = values[i:i+chunk_rows]
        end_row = start + i + len(block) - 1
        rng = f"A{start+i}:I{end_row}"
        gs_retry(ws.update, rng, block)
        written += len(block)
        if pause: time.sleep(pause)
    return written

def ensure_columns(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Thiếu cột bắt buộc: {', '.join(missing)}")
    return df[REQUIRED_COLUMNS].copy()

def resolve_ma_don_vi(ten_dv: str, ma_dv_cur: str = "") -> str:
    """Luôn trả mã đơn vị nếu tên đơn vị hợp lệ; nếu chưa có trong map thì tạo tạm 3 ký tự đầu."""
    if str(ma_dv_cur).strip():
        return str(ma_dv_cur).strip().upper()
    name = str(ten_dv).strip()
    if not name:
        return ""
    ma = DON_VI_MAP.get(name)
    if ma:
        return ma.upper()
    # fallback: lấy 3 chữ cái đầu (viết hoa, bỏ dấu)
    name_ascii = re.sub(r"[^A-Z]", "", re.sub(r"Đ", "D", name.upper()))
    return name_ascii[:3] if name_ascii else ""


def build_unit_counters(df_cur: pd.DataFrame) -> dict:
    counters = {}
    if "Mã thẻ" in df_cur.columns:
        for val in df_cur["Mã thẻ"].dropna().astype(str):
            m = re.match(r"^([A-Z]{3})(\d{3})$", val.strip().upper())
            if m:
                unit, num = m.group(1), int(m.group(2))
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
def fill_missing_codes_strict(df_new: pd.DataFrame, df_cur: pd.DataFrame) -> pd.DataFrame:
    """
    - Tự gán 'Mã đơn vị' từ 'Tên đơn vị' (theo DON_VI_MAP). Nếu không map được → để rỗng.
    - Tự sinh 'Mã thẻ' theo từng 'Mã đơn vị' (giữ lại mã đã có đúng format).
    - Seed số chạy dựa trên df_cur hiện có.
    """
    df = df_new.copy()

    # Bảo đảm đủ cột & loại NaN thành rỗng
    for c in REQUIRED_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df.fillna("")

    # 1) Mã đơn vị
    def _resolve_unit(row):
        ma_cur = str(row.get("Mã đơn vị", "")).strip().upper()
        if ma_cur:
            return ma_cur
        name = str(row.get("Tên đơn vị", "")).strip()
        if not name:
            return ""
        return DON_VI_MAP.get(name, "").upper()

    df["Mã đơn vị"] = df.apply(_resolve_unit, axis=1)

    # 2) Mã thẻ theo từng đơn vị (seed từ dữ liệu đang có)
    counters = build_unit_counters(df_cur)

    def _gen_codes(group: pd.DataFrame) -> pd.Series:
        unit = str(group.name or "").strip().upper()
        if not unit:
            # không có đơn vị → trả nguyên giá trị (nhưng đổi NaN -> rỗng)
            return group["Mã thẻ"].astype(str).replace({"nan": ""})
        cur = counters.get(unit, 0)
        out = []
        for v in group["Mã thẻ"].astype(str):
            v2 = (v or "").strip().upper()
            if v2 in ("", "NAN"):
                cur += 1
                out.append(f"{unit}{cur:03d}")
            else:
                m = re.match(rf"^{unit}(\d{{3}})$", v2)
                if m:
                    cur = max(cur, int(m.group(1)))
                out.append(v2)
        counters[unit] = cur
        return pd.Series(out, index=group.index)

    df["Mã thẻ"] = df.groupby("Mã đơn vị", dropna=False, group_keys=False).apply(_gen_codes)

    # 3) Chuẩn hoá STT (nếu muốn)
    try:
        df["STT"] = pd.RangeIndex(1, len(df) + 1)
    except Exception:
        pass

    return df[REQUIRED_COLUMNS].copy()

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

# ==========================
# KẾT NỐI GOOGLE SHEETS
# ==========================
from google.oauth2.service_account import Credentials

def get_sheet():
    """Mở đúng SHEET_ID + tab WORKSHEET_NAME; tự tạo header nếu sheet mới."""
    info = st.secrets["google_service_account"]
    # Nếu private_key bị dán dạng '\n' thì chuyển về xuống dòng thật
    info2 = dict(info)
    pk = info2.get("private_key", "")
    if isinstance(pk, str) and "\\n" in pk and "BEGIN PRIVATE KEY" in pk:
        info2["private_key"] = pk.replace("\\n", "\n")

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        creds = Credentials.from_service_account_info(info2, scopes=scopes)
    except Exception:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(info2, scopes=scopes)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(WORKSHEET_NAME)  # "Sheet1"
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=WORKSHEET_NAME, rows="2000", cols="20")
        gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])
    return ws

try:
    ws = get_sheet()
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ==========================
# BẢO VỆ – MẬT KHẨU
# ==========================
APP_PASSWORD = st.secrets.get("app_password") or st.secrets.get("qr_password")
if not APP_PASSWORD:
    st.error("❌ Thiếu mật khẩu ứng dụng trong secrets (app_password hoặc qr_password).")
    st.stop()

# ==========================
# LOAD DỮ LIỆU CHÍNH
# ==========================
@st.cache_data(ttl=60)
def load_df():
    try:
        data = ws.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ Không thể tải dữ liệu xe: {e}")
        st.stop()

# ====== QR gate: mật khẩu QR riêng & chỉ hiển thị đúng 1 xe rồi dừng ======
QR_PASSWORD = _get_secret("QR_PASSWORD", "qr_password", "qrpassword", "qr_pwd")  # CHỈ mật khẩu QR

# Lấy id=? tương thích API mới/cũ
try:
    qp = getattr(st, "query_params", None)
    params = dict(qp) if qp is not None else st.experimental_get_query_params()
except Exception:
    params = st.experimental_get_query_params()

qr_id = ""
if "id" in params:
    v = params["id"]
    qr_id = v[0] if isinstance(v, list) else str(v)

if qr_id:
    # Ẩn sidebar khi mở bằng QR
    st.markdown("""
        <style>
        [data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="stSidebarContent"]{display:none!important;}
        </style>
    """, unsafe_allow_html=True)

    st.subheader("🔍 Tra cứu xe qua QR")

    if not QR_PASSWORD:
        st.error("Thiếu QR_PASSWORD trong secrets."); st.stop()

    pwd = st.text_input("🔑 Nhập mật khẩu QR", type="password", placeholder="Mật khẩu chỉ để xem QR")
    if not pwd:
        st.info("Vui lòng nhập mật khẩu QR để xem thông tin xe."); st.stop()
    if pwd.strip() != QR_PASSWORD:
        st.error("❌ Sai mật khẩu QR."); st.stop()

    # Đúng mật khẩu QR → chỉ hiển thị bản ghi khớp rồi DỪNG
    df0 = load_df().copy()
    df0["__plate_norm"] = df0["Biển số"].astype(str).apply(normalize_plate)
    df0["__card_up"]    = df0.get("Mã thẻ", "").astype(str).str.upper().str.strip()

    q_up = str(qr_id).upper().strip()
    q_norm = normalize_plate(str(qr_id))

    # Ưu tiên khớp MÃ THẺ; nếu không có thì khớp biển số chuẩn hóa
    if re.fullmatch(r"[A-Z]{3}\d{3}", q_up):
        view = df0[df0["__card_up"].eq(q_up)]
    else:
        view = df0[df0["__plate_norm"].eq(q_norm)]

    if view.empty:
        st.error(f"Không tìm thấy xe với mã/biển số: {qr_id}")
    else:
        st.success("✅ Thông tin xe:")
        st.dataframe(
            view.drop(columns=["__plate_norm","__card_up"], errors="ignore"),
            hide_index=True, use_container_width=True
        )
    st.stop()  # BẮT BUỘC: không cho chạy xuống app quản trị

# Cổng đăng nhập app
if "auth_ok" not in st.session_state:
    st.session_state.auth_ok = False
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

# Sau đăng nhập
st.sidebar.image("ump_logo.png", width=120)
st.sidebar.markdown("---")

if "df" not in st.session_state:
    st.session_state.df = load_df()
df = st.session_state.df

# ==========================
# MENU
# ==========================
menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📥 Tải dữ liệu lên",
    "🎁 Tạo mã QR hàng loạt",
    "📤 Xuất ra Excel",
    "📊 Thống kê",
    "🤖 Trợ lý AI"
]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=0)

# ==========================
# CHỨC NĂNG
# ==========================
if choice == "📋 Xem danh sách":
    st.subheader("📋 Danh sách xe đã đăng ký")
    df_show = clean_df(df.copy())
    if "Biển số" in df_show.columns:
        df_show["Biển số"] = df_show["Biển số"].apply(dinh_dang_bien_so)
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
            # gợi ý gần đúng đơn giản
            def fuzzy_ratio(a: str, b: str) -> float:
                return difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()
            scores = []
            for idx, row in df.iterrows():
                s = 0.0
                s += 2.0 * fuzzy_ratio(bien_so_input, row.get("Biển số", ""))
                s += fuzzy_ratio(bien_so_input, row.get("Họ tên", ""))
                s += fuzzy_ratio(bien_so_input, row.get("Mã thẻ", ""))
                s += 0.8 * fuzzy_ratio(bien_so_input, row.get("Tên đơn vị", ""))
                scores.append((idx, s))
            scores.sort(key=lambda x: x[1], reverse=True)
            idxs = [i for i, _ in scores[:20]]
            top = df.loc[idxs].copy()
            st.success(f"✅ Gợi ý gần đúng (top {len(top)}):")
            st.dataframe(top, hide_index=True, use_container_width=True)
        elif ket_qua.empty:
            st.warning("🚫 Không tìm thấy xe nào khớp với biển số đã nhập.")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), hide_index=True, use_container_width=True)

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
                counters = build_unit_counters(df_current)
                cur = counters.get(ma_don_vi, 0) + 1
                ma_the = f"{ma_don_vi}{cur:03d}"
                gs_retry(ws.append_row, [
                    int(len(df_current) + 1),
                    ho_ten, bien_so, ma_the, ma_don_vi, ten_don_vi,
                    chuc_vu, so_dien_thoai, email
                ])
                st.success(f"✅ Đã đăng ký xe cho `{ho_ten}` với mã thẻ: `{ma_the}`")
                norm = normalize_plate(bien_so)
                link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(norm)}"
                qr_png = make_qr_bytes(link)
                st.image(qr_png, caption=f"QR cho {bien_so}", width=200)
                st.download_button("📥 Tải mã QR", data=qr_png, file_name=f"QR_{bien_so}.png", mime="image/png")
                st.caption("Quét mã sẽ yêu cầu mật khẩu trước khi xem thông tin.")
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
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), hide_index=True, use_container_width=True)
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
                        stt_val, ho_ten_moi, bien_so_moi, str(row["Mã thẻ"]),
                        ma_don_vi_moi, ten_don_vi_moi, chuc_vu_moi, so_dien_thoai_moi, email_moi
                    ]
                    gs_retry(ws.update, f"A{index+2}:I{index+2}", [payload])
                    st.success("✅ Đã cập nhật thông tin xe thành công!")
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
                st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), hide_index=True, use_container_width=True)
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
    up = st.file_uploader("Chọn tệp Excel (.xlsx) hoặc CSV", type=["xlsx", "csv"])
    mode = st.selectbox("Chế độ ghi dữ liệu", ["Thêm (append)", "Thay thế toàn bộ (replace all)", "Upsert"])
    dry_run = st.checkbox("🔎 Chạy thử (không ghi Google Sheets)")

    if up is not None:
        try:
            if up.name.lower().endswith(".csv"):
                df_up = pd.read_csv(up, dtype=str, keep_default_na=False)
            else:
                df_up = pd.read_excel(up, dtype=str)
        except Exception as e:
            st.error(f"❌ Không đọc được tệp: {e}")
            st.stop()

        df_up = clean_df(df_up)
        for c in REQUIRED_COLUMNS:
            if c not in df_up.columns:
                df_up[c] = ""

        

        st.info(f"Đã nạp {len(df_up)} dòng.")

        if st.button("🚀 Thực thi"):
            try:
                cur_vals = gs_retry(ws.get_all_values)
                if not cur_vals:
                    gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])
                    df_cur = pd.DataFrame(columns=REQUIRED_COLUMNS)
                else:
                    header, rows = cur_vals[0], cur_vals[1:]
                    rows = [r + [""]*(len(header)-len(r)) if len(r) < len(header) else r[:len(header)] for r in rows]
                    df_cur = pd.DataFrame(rows, columns=header)
                    for c in REQUIRED_COLUMNS:
                        if c not in df_cur.columns:
                            df_cur[c] = ""

                df_to_write = fill_missing_codes_strict(df_up, df_cur)

                if dry_run:
                    st.info("🔎 Chạy thử: không ghi Google Sheets.")
                else:
                    if mode == "Thêm (append)":
                        added = write_bulk_block(ws, df_cur, df_to_write, columns=REQUIRED_COLUMNS)
                        st.success(f"✅ Đã thêm {added} dòng.")
                    elif mode == "Thay thế toàn bộ (replace all)":
                        gs_retry(ws.clear)
                        gs_retry(ws.update, "A1", [REQUIRED_COLUMNS])
                        vals = _df_to_values(df_to_write, REQUIRED_COLUMNS)
                        if vals:
                            gs_retry(ws.update, f"A2:I{1+len(vals)}", vals)
                        st.success(f"✅ Đã thay thế toàn bộ dữ liệu ({len(df_to_write)} dòng).")
                    else:
                        # Upsert nhanh
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
                                idx0 = int(key_to_row[key])
                                updates.append((idx0+2, payload))
                            else:
                                inserts.append(payload)

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

                        if inserts:
                            start = len(df_cur2) + 2
                            for i in range(0, len(inserts), 500):
                                blk = inserts[i:i+500]
                                end_row = start + i + len(blk) - 1
                                rng = f"A{start+i}:I{end_row}"
                                gs_retry(ws.update, rng, blk)

                        st.success(f"✅ Upsert xong: cập nhật {len(updates)} • thêm mới {len(inserts)}.")

                st.dataframe(df_to_write.head(20), hide_index=True, use_container_width=True)
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
    df_qr = clean_df(df_qr)
    for col in ["Mã thẻ", "Biển số", "Mã đơn vị"]:
        if col not in df_qr.columns:
            df_qr[col] = ""
    st.info(f"Mỗi QR sẽ mở: {BASE_URL_QR}?id=<MãThẻ>")
    if st.button("⚡ Tạo ZIP mã QR"):
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
            st.download_button("⬇️ Tải ZIP QR (phân theo đơn vị)",
                               data=bio.getvalue(),
                               file_name="qr_xe_theo_don_vi.zip",
                               mime="application/zip")
            st.success(f"✅ Đã tạo {len(files)} QR.")

elif choice == "📤 Xuất ra Excel":
    st.subheader("📤 Tải danh sách xe dưới dạng Excel")
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='DanhSachXe')
    processed_data = output.getvalue()
    st.download_button(label="📥 Tải Excel",
                       data=processed_data,
                       file_name="DanhSachXe.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

elif choice == "📊 Thống kê":
    st.markdown("## 📊 Dashboard thống kê xe theo đơn vị")
    df_stats = df.copy()

    import unicodedata
    def _unit_norm(x: str) -> str:
        s = "" if x is None else str(x)
        s = s.replace("\xa0", " ")
        s = unicodedata.normalize("NFKC", s)
        s = s.replace("Ð", "Đ").replace("đ", "Đ")
        s = re.sub(r"\s+", " ", s).strip()
        return s

    df_stats["__unit"] = df_stats["Tên đơn vị"].apply(_unit_norm)

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
        "Tạp chí Y học": "Tạp chí Y học",
        "YHCT": "Khoa Y học Cổ truyền",
        "HTQT": "Phòng Hợp tác Quốc tế"
    }

    thong_ke = (
        df_stats.groupby("__unit", dropna=False)
        .size()
        .reset_index(name="Số lượng xe")
        .rename(columns={"__unit": "Tên đơn vị"})
    )
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
    thong_ke_display["Số lượng xe"] = thong_ke_display["Số lượng xe"].astype(str)
    st.dataframe(thong_ke_display, hide_index=True, use_container_width=True)


elif choice == "🤖 Trợ lý AI":
    st.subheader("🤖 Trợ lý AI (lọc theo TỪ trọn vẹn, nhiều từ – AND, phân biệt dấu)")

    # Tokenize tên: GIỮ dấu tiếng Việt, tách thành các "từ" unicode (a–z, 0–9, và chữ có dấu)
    def name_tokens_vn(s: str):
        s = "" if s is None else str(s).lower()
        return re.findall(r"[0-9A-Za-zÀ-ỹ]+", s)

    # Tách truy vấn thành các "từ" (tokens) – hỗ trợ nhiều từ => điều kiện AND
    def query_tokens_vn(q: str):
        q = (q or "").strip().lower()
        # Hỗ trợ ngăn cách bằng khoảng trắng hoặc dấu phẩy
        qs = re.split(r"[,\s]+", q)
        return [t for t in qs if t]

    q_raw = st.text_input("Nhập từ khóa (ví dụ: 'an', 'đạt', 'nam văn', '73', 'TRY', 'BVY', 'Trường Y', 'BV ĐHYD')").strip()
    if q_raw:
        base = df.copy()
        base["__name_tokens"] = base.get("Họ tên", "").astype(str).apply(name_tokens_vn)
        base["__plate_norm"]  = base.get("Biển số", "").astype(str).apply(normalize_plate)
        base["__unit_code"]   = base.get("Mã đơn vị", "").astype(str).str.upper().str.strip()
        base["__card_code"]   = base.get("Mã thẻ", "").astype(str).str.upper().str.strip()

        q_up    = q_raw.upper()
        q_tokens = query_tokens_vn(q_raw)

        res = base

        # 1) Nếu toàn số → lọc biển số CHỨA chuỗi số đó (ví dụ '73')
        if re.fullmatch(r"\d{2,}", q_raw):
            res = res[res["__plate_norm"].str.contains(q_raw, na=False)]
        else:
            # 2) Mã đơn vị / mã thẻ (ưu tiên)
            if q_up in DON_VI_MAP.values():                # ví dụ TRY, BVY
                res = res[res["__unit_code"].eq(q_up)]
            elif re.fullmatch(r"[A-Z]{3}\d{3}", q_up):     # ví dụ TRY012
                res = res[res["__card_code"].eq(q_up)]
            else:
                # 3) Tên đơn vị chuẩn (không fuzzy)
                if q_up in map(str.upper, DON_VI_MAP.keys()):
                    res = res[res["Tên đơn vị"].str.upper().eq(q_up)]
                else:
                    # 4) Mặc định: AND trên các token họ tên (GIỮ dấu, TỪ trọn vẹn)
                    #    - 'an' chỉ khớp token 'an' (không khớp 'ân', 'ẩn', 'khang'…)
                    #    - 'đạt' khớp đúng 'đạt'
                    #    - 'nam văn' yêu cầu cả 'nam' và 'văn' đều xuất hiện trong tên
                    qset = set(q_tokens)
                    res = res[res["__name_tokens"].apply(lambda toks: qset.issubset(set(toks)))]

        res = res.drop(columns=["__name_tokens","__plate_norm","__unit_code","__card_code"], errors="ignore")

        if res.empty:
            st.info("Không tìm thấy kết quả trùng khớp.")
        else:
            st.success(f"Tìm thấy {len(res)} kết quả.")
            st.dataframe(res, hide_index=True, use_container_width=True)


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
