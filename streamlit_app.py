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
# ==== DROP-IN: bulk upload fixed (no UI changes) ====
import re, time, random
import pandas as pd

END_COL = "I"  # nếu sheet của bạn có nhiều/ít cột hơn, đổi chữ cái cột cuối

def _canon(s):
    import unicodedata
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^A-Za-z0-9]+", "", s).lower()
    return s

_CANON2STD = {
    "stt":"STT","hoten":"Họ tên","name":"Họ tên","hovaten":"Họ tên","ten":"Họ tên",
    "bienso":"Biển số","licenseplate":"Biển số","plate":"Biển số","biensoxe":"Biển số",
    "mathe":"Mã thẻ","ma_the":"Mã thẻ",
    "madonvi":"Mã đơn vị","tendonvi":"Tên đơn vị",
    "chucvu":"Chức vụ","sodienthoai":"Số điện thoại","dienthoai":"Số điện thoại","phone":"Số điện thoại",
    "email":"Email",
}
REQ = ["STT","Họ tên","Biển số","Mã thẻ","Mã đơn vị","Tên đơn vị","Chức vụ","Số điện thoại","Email"]

def coerce_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty: return df
    ren, seen = {}, set()
    for c in df.columns:
        std = _CANON2STD.get(_canon(c))
        if std and std not in seen:
            ren[c] = std; seen.add(std)
    out = df.rename(columns=ren).copy()
    for c in REQ:
        if c not in out.columns: out[c] = ""
    return out

CARD_PREFIX, CARD_PAD = "TH", 6
def _slug_unit(name: str) -> str:
    if not isinstance(name, str) or not name.strip(): return "DV"
    words = re.findall(r"[A-Za-zÀ-ỹ0-9]+", name.strip(), flags=re.UNICODE)
    if not words: return "DV"
    initials = "".join(w[0] for w in words).upper()
    if len(initials) <= 1:
        flat = re.sub(r"[^A-Za-z0-9]", "", name.upper())
        return (flat or "DV")[:8]
    return initials[:8]

def _next_card_seed(series: pd.Series) -> int:
    mx = 0
    for v in (series or pd.Series(dtype=str)).dropna().astype(str):
        m = re.match(rf"^{re.escape(CARD_PREFIX)}(\d+)$", v.strip(), flags=re.IGNORECASE)
        if m:
            try: mx = max(mx, int(m.group(1)))
            except: pass
    return mx

def ensure_codes_all(df_up: pd.DataFrame, df_cur: pd.DataFrame) -> pd.DataFrame:
    df_up = coerce_columns(df_up).dropna(how="all").reset_index(drop=True)
    df_cur = coerce_columns(df_cur if df_cur is not None else pd.DataFrame(columns=REQ))

    import unicodedata, re as _re

    # ===== Aliases để bắt các biến thể vẫn đúng là 1 đơn vị =====
    UNIT_ALIASES = {
        # BV ĐHYD
        "bvdhyd": "BV ĐHYD",
        "bv dhyd": "BV ĐHYD",
        "bvđhyd": "BV ĐHYD",
        "bvdvyd": "BV ĐHYD",   # hay nhầm 'H' -> 'V'
        "bv đvyd": "BV ĐHYD",

        # RHM
        "rhm": "RHM",
        "rmh": "RHM",          # đảo chữ cái
    }

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
        return s.lower() in {"nan", "none", "null", "na", "n/a", "-", "_"}

    # 1) Map tên -> mã đơn vị
    canon_from_const = { _canon_name(k): v for k, v in DON_VI_MAP.items() }  # dùng chính DON_VI_MAP của bạn
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
        # a) Alias -> tên chuẩn -> DON_VI_MAP
        if ckey in UNIT_ALIASES:
            std_name = UNIT_ALIASES[ckey]
            return DON_VI_MAP.get(std_name, _slug_unit(std_name))
        # b) DON_VI_MAP trực tiếp
        if ckey in canon_from_const:
            return canon_from_const[ckey]
        # c) Tên trùng dữ liệu đang có
        key_up = str(ten).strip().upper()
        if key_up in unit_map_sheet:
            return unit_map_sheet[key_up]
        # d) Fallback slug (tránh trùng)
        base, cand, k = _slug_unit(str(ten)), None, 2
        cand = base
        while cand.upper() in used_units:
            cand = f"{base}{k}"; k += 1
        used_units.add(cand.upper())
        return cand

    # 2) Seed số thứ tự mã thẻ theo từng đơn vị (KHB001…, TRY001…)
    CARD_PAD = 3
    per_unit_seed = {}
    if not df_cur.empty and all(c in df_cur.columns for c in ["Mã đơn vị","Mã thẻ"]):
        for uc, grp in df_cur.groupby(df_cur["Mã đơn vị"].astype(str).str.upper(), dropna=True):
            mx = 0
            for v in grp["Mã thẻ"].dropna().astype(str):
                m = _re.match(rf"^{_re.escape(uc)}(\d+)$", v.strip(), flags=_re.IGNORECASE)
                if m:
                    try: mx = max(mx, int(m.group(1)))
                    except: pass
            per_unit_seed[uc] = mx

    # 3) Gán Mã đơn vị + Mã thẻ (luôn ép theo DON_VI_MAP/ALIASES nếu tên đơn vị khớp)
    for i, r in df_up.iterrows():
        ten_dv = r.get("Tên đơn vị", "")
        target_uc = resolve_unit_code(ten_dv)  # <-- mã đơn vị đích theo quy ước
        df_up.at[i, "Mã đơn vị"] = target_uc   # ghi đè để sửa mọi lệch mã có sẵn

        ma_the = r.get("Mã thẻ", "")
        if _is_blank(ma_the):
            uc = str(target_uc).strip().upper()
            if uc not in per_unit_seed:
                per_unit_seed[uc] = 0
            per_unit_seed[uc] += 1
            df_up.at[i, "Mã thẻ"] = f"{uc}{str(per_unit_seed[uc]).zfill(CARD_PAD)}"

    return df_up

def gs_retry(func, *args, max_retries=7, base=0.6, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (429,500,503):
                time.sleep(base*(2**i) + random.uniform(0,0.5))
                continue
            raise
    raise RuntimeError("Google Sheets write failed after multiple retries")

def write_bulk(sheet, df_cur: pd.DataFrame, df_new: pd.DataFrame, chunk_rows=200, pause=0.5):
    """Ghi theo block để tránh quota; tự sinh mã trước khi ghi."""
    df_cur = coerce_columns(df_cur)
    df_new = ensure_codes_all(df_new, df_cur)
    values = df_new.fillna("").astype(object).values.tolist()
    start = len(df_cur) + 2
    written = 0
    for i in range(0, len(values), chunk_rows):
        block = values[i:i+chunk_rows]
        rng = f"A{start+i}:{END_COL}{start+i+len(block)-1}"
        gs_retry(sheet.update, rng, block)
        written += len(block)
        time.sleep(pause)
    return written
def _get_query_params():
    try:
        return st.query_params
    except Exception:
        return st.experimental_get_query_params()

def _normalize_plate(s: str) -> str:
    import re
    s = "" if s is None else str(s).upper()
    return re.sub(r"[^A-Z0-9]", "", s)

def qr_gate_and_show(df_cur):
    q = _get_query_params()
    raw_id = (q.get("id") or [""])[0] if isinstance(q, dict) else q.get("id", "")
    id_ = str(raw_id).strip()
    if not id_:
        return False  # không ở chế độ QR

    # Cổng QR dùng secrets
    if not st.session_state.get("_qr_ok"):
        pw = st.text_input("🔑 Nhập mật khẩu để xem thông tin xe", type="password", key="_qr_pw")
        if pw:
            if pw == st.secrets["qr_password"]:
                st.session_state["_qr_ok"] = True
                st.rerun()
            else:
                st.error("❌ Mật khẩu QR sai.")
                st.stop()
        st.stop()

    # Tìm đúng dòng: ưu tiên Mã thẻ, fallback Biển số
    sel = df_cur[df_cur["Mã thẻ"].astype(str).str.upper() == id_.upper()]
    if sel.empty and "Biển số" in df_cur:
        sel = df_cur[df_cur["Biển số"].astype(str).map(_normalize_plate) == _normalize_plate(id_)]

    if sel.empty:
        st.error("❌ Không tìm thấy xe.")
    else:
        st.success("✅ Xác thực OK")
        st.dataframe(sel, hide_index=True)
    st.stop()

# ---------- Page config ----------
import re as _re_patch, unicodedata as _unicodedata_patch, time as _time_patch, random as _random_patch
import pandas as _pd_patch

def _canon_ap(a):
    if a is None: return ""
    s = str(a)
    s = _unicodedata_patch.normalize("NFD", s)
    s = "".join(ch for ch in s if _unicodedata_patch.category(ch) != "Mn")
    s = s.lower()
    s = _re_patch.sub(r"[^a-z0-9]+", "", s)
    return s

_AP_CANON_TO_STD = {
    "bienso": "Biển số",
    "biensoxe": "Biển số",
    "licenseplate": "Biển số",
    "plate": "Biển số",
    "hoten": "Họ tên",
    "ten": "Họ tên",
    "hovaten": "Họ tên",
    "fullname": "Họ tên",
    "name": "Họ tên",
    "sodienthoai": "Số điện thoại",
    "dienthoai": "Số điện thoại",
    "phone": "Số điện thoại",
    "email": "Email",
    "madonvi": "Mã đơn vị",
    "tendonvi": "Tên đơn vị",
    "chucvu": "Chức vụ",
    "mathe": "Mã thẻ",
    "ma_the": "Mã thẻ",
}

_AP_REQUIRED_COLUMNS = ["STT","Họ tên","Biển số","Mã thẻ","Mã đơn vị","Tên đơn vị","Chức vụ","Số điện thoại","Email"]

def coerce_columns(df):
    try:
        if df is None or getattr(df, "empty", False):
            return df
    except Exception:
        return df
    ren = {}
    seen = set()
    for c in list(df.columns):
        k = _canon_ap(c)
        std = _AP_CANON_TO_STD.get(k)
        if std and std not in seen:
            ren[c] = std
            seen.add(std)
    out = df.rename(columns=ren)
    for col in _AP_REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out

def safe_format_plate(x):
    if _pd_patch.isna(x) or str(x).strip() == "":
        return ""
    try:
        return dinh_dang_bien_so(str(x))
    except Exception:
        return str(x)

_UNIT_PAD   = 0
_CARD_PREFIX= "TH"
_CARD_PAD   = 6

def _slug_unit(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        return "DV"
    words = _re_patch.findall(r"[A-Za-zÀ-ỹ0-9]+", name.strip(), flags=_re_patch.UNICODE)
    if not words:
        return "DV"
    initials = "".join(w[0] for w in words).upper()
    if len(initials) <= 1:
        flat = _re_patch.sub(r"[^A-Za-z0-9]", "", name.upper())
        return (flat or "DV")[:8]
    return initials[:8]

def _next_card_seed(existing_codes):
    max_num = 0
    try:
        it = _pd_patch.Series(existing_codes).dropna().astype(str)
    except Exception:
        it = []
    for v in it:
        m = _re_patch.match(rf"^{_re_patch.escape(_CARD_PREFIX)}(\d+)$", v.strip(), flags=_re_patch.IGNORECASE)
        if m:
            try:
                max_num = max(max_num, int(m.group(1)))
            except:
                pass
    return max_num

def ensure_codes(df_up, df_cur):
    df_up = coerce_columns(df_up)
    df_cur = coerce_columns(df_cur) if df_cur is not None else _pd_patch.DataFrame(columns=_AP_REQUIRED_COLUMNS)
    df_cur = coerce_columns(df_cur)
    unit_map = {}
    if not getattr(df_cur, "empty", True) and all(c in df_cur.columns for c in ["Tên đơn vị","Mã đơn vị"]):
        for _, r in df_cur[["Tên đơn vị","Mã đơn vị"]].dropna().iterrows():
            name = str(r["Tên đơn vị"]).strip().upper()
            code = str(r["Mã đơn vị"]).strip().upper()
            if name and code:
                unit_map[name] = code
    used_unit = set(df_cur["Mã đơn vị"].dropna().astype(str).str.upper()) if "Mã đơn vị" in df_cur.columns else set()

    def _alloc_unit(ten: str) -> str:
        if not ten: return "DV"
        key = ten.strip().upper()
        if key in unit_map: return unit_map[key]
        base = _slug_unit(ten)
        cand = base
        k = 2
        while cand.upper() in used_unit:
            cand = f"{base}{(str(k).zfill(_UNIT_PAD)) if _UNIT_PAD>0 else k}"; k += 1
        used_unit.add(cand.upper())
        unit_map[key] = cand
        return cand

    start_num = _next_card_seed(df_cur.get("Mã thẻ") if "Mã thẻ" in df_cur.columns else _pd_patch.Series(dtype=str))
    cur_num = start_num
    def _alloc_card() -> str:
        nonlocal cur_num
        cur_num += 1
        return f"{_CARD_PREFIX}{str(cur_num).zfill(_CARD_PAD)}"

    for i, r in df_up.iterrows():
        if not str(r.get("Mã đơn vị","")).strip():
            df_up.at[i, "Mã đơn vị"] = _alloc_unit(str(r.get("Tên đơn vị","")).strip())
        if not str(r.get("Mã thẻ","")).strip():
            df_up.at[i, "Mã thẻ"] = _alloc_card()
    return df_up

def gs_retry(func, *args, max_retries=6, base=0.8, **kwargs):
    for i in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (429,500,503):
                _time_patch.sleep(base*(2**i) + _random_patch.uniform(0,0.5))
                continue
            raise
    raise RuntimeError("Google Sheets write failed after multiple retries")
# ==== END INLINED HELPERS ====

st.set_page_config(page_title="QR Car Management", page_icon="🚗", layout="wide")

# ---------- Constants ----------
REQUIRED_COLUMNS = ["STT", "Họ tên", "Biển số", "Mã thẻ", "Mã đơn vị", "Tên đơn vị", "Chức vụ", "Số điện thoại", "Email"]
DON_VI_MAP = {
    "HCTH": "HCT", "TCCB": "TCC", "ĐTĐH": "DTD", "ĐTSĐH": "DTS", "KHCN": "KHC", "KHTC": "KHT",
    "QTGT": "QTG", "TTPC": "TTP", "ĐBCLGD&KT": "DBK", "CTSV": "CTS", "Trường Y": "TRY",
    "Trường Dược": "TRD", "Trường ĐD-KTYH": "TRK", "KHCB": "KHB", "RHM": "RHM", "YTCC": "YTC",
    "PK.CKRHM": "CKR", "TT.KCCLXN": "KCL", "TT.PTTN": "PTN", "TT.ĐTNLYT": "DTL", "TT.CNTT": "CNT",
    "TT.KHCN UMP": "KCU", "TT.YSHPT": "YSH", "Thư viện": "TV", "KTX": "KTX", "Tạp chí Y học": "TCY",
    "BV ĐHYD": "BVY", "TT. GDYH": "GDY", "VPĐ": "VPD", "YHCT": "YHC", "HTQT": "HTQ"
}
# Chuẩn hoá không dấu để bắt các biến thể thường gõ nhầm
UNIT_ALIASES = {
    # Bệnh viện ĐHYD (BVY)
    "bvdhyd": "BV ĐHYD",
    "bv dhyd": "BV ĐHYD",
    "bvđhyd": "BV ĐHYD",
    "bvdvyd": "BV ĐHYD",     # hay gõ nhầm V/H
    "bv đvyd": "BV ĐHYD",

    # RHM
    "rhm": "RHM",
    "rmh": "RHM",            # đảo chữ cái
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

# ---------- Google Sheet init (giữ nguyên secrets/JSON) ----------
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
if "google_service_account" not in st.secrets:
    st.error("❌ Thiếu [google_service_account] trong secrets.")
    st.stop()
try:
    creds_dict = dict(st.secrets["google_service_account"])  # không đổi cấu trúc
    pk = str(creds_dict.get("private_key", ""))
    if ("-----BEGIN" in pk) and ("\\n" in pk) and ("\n" not in pk):
        pk = pk.replace("\\r\\n", "\\n").replace("\\n", "\n")
        creds_dict["private_key"] = pk
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
except Exception as e:
    st.error(f"❌ Lỗi khởi tạo Google Credentials: {e}")
    st.stop()

# Thay bằng Sheet của bạn
SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
try:
    sheet = client.open_by_key(SHEET_ID).worksheet("Sheet1")
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ---------- Load data ----------
@st.cache_data(ttl=60)
def load_df():
    try:
        data = sheet.get_all_records()
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

# Logo + tiêu đề (chỉ hiện sau login, nhưng để đẹp, ta hiện luôn tiêu đề)
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
    "📤 Xuất ra Excel",
    "📊 Thống kê xe theo đơn vị",
    "🎁 Tạo mã QR hàng loạt",
    "🤖 Trợ lý AI"
]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=0)

# ---------- Các tính năng ----------
if choice == "📋 Xem danh sách":
    st.subheader("📋 Danh sách xe đã đăng ký")
    df_show = df.copy()
    df_show = coerce_columns(df_show)
    if "Biển số" in df_show.columns:
        df_show["Biển số"] = df_show["Biển số"].apply(safe_format_plate)
    else:
        try:
            st.warning("Không tìm thấy cột 'Biển số' trong dữ liệu hiển thị.")
        except Exception:
            pass
    st.dataframe(df_show, hide_index=True)


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
                gs_retry(sheet.append_row, [
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
                    gs_retry(sheet.update, f"A{index+2}:I{index+2}", [payload])
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
                    sheet.delete_rows(int(index) + 2)
                    st.success(f"🗑️ Đã xóa xe có biển số `{row['Biển số']}` thành công!")
                    st.session_state.df = load_df()
        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "📥 Tải dữ liệu lên":
    st.subheader("📥 Tải dữ liệu từ file lên Google Sheet")
    st.markdown("Bạn có thể để **trống** cột **Mã thẻ** và **Mã đơn vị** — hệ thống sẽ tự sinh dựa trên **Tên đơn vị**.")

    # Tải file mẫu
    tmpl = pd.DataFrame(columns=REQUIRED_COLUMNS)
    buf_tmpl = BytesIO()
    with pd.ExcelWriter(buf_tmpl, engine='openpyxl') as writer:
        tmpl.to_excel(writer, index=False, sheet_name='Template')
    st.download_button("📄 Tải mẫu Excel", data=buf_tmpl.getvalue(),
                       file_name="Template_DanhSachXe.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    file = st.file_uploader("Chọn file dữ liệu (.xlsx hoặc .csv)", type=["xlsx", "csv"])
    mode = st.selectbox("Chọn chế độ", ["Thêm (append)", "Thay thế toàn bộ (replace all)", "Cập nhật theo Biển số (upsert)"])
    auto_stt = st.checkbox("🔢 Đánh lại STT sau khi ghi", value=True)
    dry_run = st.checkbox("🧪 Chạy thử (không ghi)", value=True)

    # Để gom QR sau upload
    qr_images = []  # danh sách (filename, bytes)

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
                        df_to_write = ensure_codes(df_to_write, df_cur)
                        rows = write_bulk(sheet, df_cur, df_up)   # ghi theo lô, tự sinh mã, chống quota
                        st.success(f"✅ Đã thêm {rows} dòng.")

                        # tạo QR cho toàn bộ df_to_write
                        for _, r in df_to_write.iterrows():
                            norm = normalize_plate(r["Biển số"])
                            link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(norm)}"
                            png = make_qr_bytes(link)
                            qr_images.append((f"QR_{r['Biển số']}.png", png))
                        st.success(f"✅ Đã thêm {len(values)} dòng.")

                    elif mode == "Thay thế toàn bộ (replace all)":
                        df_to_write = fill_missing_codes(df_up)
                        gs_retry(sheet.clear, )
                        gs_retry(sheet.update, "A1", [REQUIRED_COLUMNS])
                        df_to_write = ensure_codes(df_to_write, df_cur)
                        values = to_native_ll(df_to_write)
                        if values:
                            gs_retry(sheet.update, f"A2:I{len(values)+1}", values)
                        # tạo QR cho toàn bộ df_to_write
                        for _, r in df_to_write.iterrows():
                            norm = normalize_plate(r["Biển số"])
                            link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(norm)}"
                            png = make_qr_bytes(link)
                            qr_images.append((f"QR_{r['Biển số']}.png", png))
                        st.success(f"✅ Đã thay thế toàn bộ dữ liệu ({len(df_to_write)} dòng).")

                    else:  # upsert
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
                                gs_retry(sheet.update, f"A{idx+2}:I{idx+2}", [norm_payload])
                                updated += 1
                            else:
                                gs_retry(sheet.append_row, norm_payload)
                                inserted += 1
                            # QR cho từng xe đã xử lý
                            link = f"https://qrcarump.streamlit.app/?id={urllib.parse.quote(norm)}"
                            png = make_qr_bytes(link)
                            qr_images.append((f"QR_{r['Biển số']}.png", png))
                        st.success(f"✅ Upsert xong: cập nhật {updated} • thêm mới {inserted}.")

                    # Đánh lại STT nếu chọn
                    if not dry_run and auto_stt:
                        try:
                            df_all = load_df()
                            df_all = reindex_stt(df_all)
                            gs_retry(sheet.clear, )
                            gs_retry(sheet.update, "A1", [REQUIRED_COLUMNS])
                            values_all = to_native_ll(df_all)
                            if values_all:
                                gs_retry(sheet.update, f"A2:I{len(values_all)+1}", values_all)
                            st.toast("🔢 Đã đánh lại STT 1..N.")
                        except Exception as e:
                            st.warning(f"⚠️ Không thể đánh lại STT tự động: {e}")

                    # Nếu có QR -> gói ZIP để tải về
                    if not dry_run and qr_images:
                        zip_buf = io.BytesIO()
                        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                            for fname, data in qr_images:
                                zf.writestr(fname, data)
                        zip_buf.seek(0)
                        st.download_button(
                            "📦 Tải tất cả mã QR (.zip)",
                            data=zip_buf.getvalue(),
                            file_name="QR_TatCaXe.zip",
                            mime="application/zip"
                        )
                        st.caption("Tệp ZIP chứa PNG mã QR của các xe đã được xử lý trong lần tải dữ liệu này.")

                    st.toast("🔄 Làm mới dữ liệu hiển thị...")
                    st.session_state.df = load_df()

        except Exception as e:
            st.error(f"❌ Lỗi khi tải/ghi dữ liệu: {e}")

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
        "HTQT": "Phòng Hợp tác Quốc tế",
        "KHCB": "Khoa Khoa học Cơ bản",
        "RHM": "Khoa Răng hàm mặt",
        "YTCC": "Khoa Y tế Công cộng",
        "YHCT": "Khoa Y học Cổ truyền",
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
# ====================== 🎁 TẠO MÃ QR HÀNG LOẠT ======================
elif choice == "🎁 Tạo mã QR hàng loạt":
    st.subheader("🎁 Tạo mã QR hàng loạt")

    # URL GitHub Pages (nơi nhúng app Streamlit)
    BASE_URL_QR = "https://dhnamgh.github.io/car/"

    # Chọn nguồn dữ liệu
    src_opt = st.radio("Chọn nguồn dữ liệu", ["Toàn bộ danh sách", "Danh sách đang lọc"], horizontal=True)

    # Lấy dữ liệu gốc hoặc danh sách đang hiển thị
    if src_opt == "Danh sách đang lọc" and "df_show" in locals():
        df_qr = df_show.copy()
    else:
        df_qr = df.copy()

    # Chuẩn hoá cột để chắc chắn có các cột cần thiết
    df_qr = coerce_columns(df_qr)
    for col in ["Mã thẻ", "Biển số", "Mã đơn vị"]:
        if col not in df_qr.columns:
            df_qr[col] = ""

    st.info(f"Mỗi mã QR sẽ mở trang: {BASE_URL_QR}?id=<MãThẻ>")

    if st.button("⚡ Tạo ZIP mã QR"):
        import io, zipfile, urllib.parse

        if df_qr.empty:
            st.warning("Không có dữ liệu để tạo QR.")
        else:
            files = []
            for _, r in df_qr.iterrows():
                # Ưu tiên Mã thẻ, fallback Biển số (đã chuẩn hoá)
                vid = str(r.get("Mã thẻ", "")).strip()
                if not vid and "Biển số" in df_qr.columns:
                    vid = normalize_plate(r.get("Biển số", ""))
                if not vid:
                    continue

                url = f"{BASE_URL_QR}?id={urllib.parse.quote(vid)}"  # KHÔNG thêm mật khẩu
                png = make_qr_bytes(url)

                unit = str(r.get("Mã đơn vị", "")).strip().upper() or "NO_UNIT"
                files.append((f"{unit}/{vid}.png", png))

            if not files:
                st.warning("Không có bản ghi hợp lệ để tạo QR.")
            else:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
                    for name, data in files:
                        zf.writestr(name, data)
                buf.seek(0)

                st.download_button(
                    "⬇️ Tải ZIP QR (phân theo đơn vị)",
                    data=buf.getvalue(),
                    file_name="qr_xe_theo_don_vi.zip",
                    mime="application/zip"
                )
                st.success(f"✅ Đã tạo {len(files)} QR và gói ZIP sẵn sàng tải về.")
                st.caption("Quét QR sẽ mở GitHub Pages, app sẽ yêu cầu mật khẩu QR (từ st.secrets).")
# ====================== /🎁 TẠO MÃ QR HÀNG LOẠT ======================

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
