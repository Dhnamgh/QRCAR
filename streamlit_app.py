import streamlit as st
st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1rem;
        }
    </style>
""", unsafe_allow_html=True)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import qrcode
import streamlit as st
import pandas as pd
import re

def format_name(name):
    return ' '.join(word.capitalize() for word in name.strip().split())

def format_plate(plate):
    plate = re.sub(r'[^a-zA-Z0-9]', '', plate).upper()
    if len(plate) >= 8:
        return f"{plate[:2]}{plate[2]}-{plate[3:6]}.{plate[6:]}"
    return plate
import re
def normalize_plate(plate):
    return re.sub(r'[^a-zA-Z0-9]', '', plate).lower()
from PIL import Image
from io import BytesIO

# ===================== GOOGLE SHEET KẾT NỐI =====================
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

try:
    creds_dict = dict(st.secrets["google_service_account"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").strip()
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
except Exception as e:
    st.error(f"❌ Lỗi khởi tạo Google Credentials: {e}")
    st.stop()

# ===================== MỞ GOOGLE SHEET =====================
SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
try:
    sheet = client.open_by_key(SHEET_ID).worksheet("Sheet1")
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ===================== GIAO DIỆN STREAMLIT =====================
st.title("🚗 QR Car Management")

menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📱 Tạo mã QR",
    "📤 Xuất ra Excel"
]
choice = st.sidebar.radio("📌 Chọn chức năng", menu)

# ===================== LẤY DỮ LIỆU =====================
try:
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
except Exception as e:
    st.error(f"❌ Lỗi tải dữ liệu: {e}")
    st.stop()

# ===================== XEM DANH SÁCH =====================
if choice == "📋 Xem danh sách":
    st.subheader("Danh sách xe")
    st.dataframe(df)

# ===================== TÌM KIẾM XE =====================
elif choice == "🔍 Tìm kiếm xe":
    st.subheader("🔍 Tìm kiếm xe theo biển số")

    bien_so_input = st.text_input("Nhập biển số xe cần tìm")

    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df["Biển số chuẩn hóa"] = df["Biển số"].apply(normalize_plate)
        ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

        if ket_qua.empty:
            st.warning("🚫 Không tìm thấy xe nào khớp với biển số đã nhập.")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

# ===================== ĐĂNG KÝ XE MỚI =====================
elif choice == "➕ Đăng ký xe mới":
    st.subheader("Đăng ký xe mới")

    # Tạo ánh xạ Tên đơn vị → Mã đơn vị từ file gốc
    don_vi_map = dict(zip(df["Tên đơn vị"], df["Mã đơn vị"]))
    ten_don_vi_list = sorted(don_vi_map.keys())

    col1, col2 = st.columns(2)
    with col1:
        ho_ten = st.text_input("Họ tên")
        bien_so = st.text_input("Biển số xe")
        ten_don_vi = st.selectbox("Tên đơn vị", ten_don_vi_list)
        ma_don_vi = don_vi_map.get(ten_don_vi, "")
    with col2:
        chuc_vu = st.text_input("Chức vụ")
        so_dien_thoai = st.text_input("Số điện thoại")
        email = st.text_input("Email")

    # Tìm mã thẻ tiếp theo theo quy tắc: Mã đơn vị + số thứ tự
    filtered = df["Mã thẻ"].dropna()[df["Mã thẻ"].str.startswith(ma_don_vi)]
    if not filtered.empty:
        numbers = filtered.str.extract(f"{ma_don_vi}(\d{{3}})")[0].dropna().astype(int)
        next_number = max(numbers) + 1
    else:
        next_number = 1
    ma_the = f"{ma_don_vi}{next_number:03d}"

    st.markdown(f"🔐 **Mã thẻ tự sinh:** `{ma_the}`")
    st.markdown(f"🏢 **Mã đơn vị:** `{ma_don_vi}`")

    if st.button("Lưu thông tin"):
        ho_ten = format_name(ho_ten)
        bien_so = format_plate(bien_so)

        if not ho_ten or not bien_so:
            st.warning("⚠️ Vui lòng nhập ít nhất Họ tên và Biển số xe.")
        elif bien_so in df["Biển số"].apply(format_plate).values:
            st.error("❌ Biển số xe đã tồn tại!")
        else:
            stt = len(df) + 1
            sheet.append_row([
                stt, ho_ten, bien_so, ma_the, ma_don_vi,
                ten_don_vi, chuc_vu, so_dien_thoai, email
            ])
            st.success(f"✅ Đã lưu thông tin xe thành công!\n🔐 Mã thẻ: `{ma_the}`")

# ===================== CẬP NHẬT XE =====================
elif choice == "✏️ Cập nhật xe":
    st.subheader("✏️ Cập nhật xe")

    bien_so_input = st.text_input("Nhập biển số xe cần cập nhật")

    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df["Biển số chuẩn hóa"] = df["Biển số"].apply(normalize_plate)
        ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

        if ket_qua.empty:
            st.error("❌ Không tìm thấy biển số xe!")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

            # Cho phép người dùng sửa thông tin
            index = ket_qua.index[0]
            row = ket_qua.iloc[0]

            st.markdown("### 📝 Nhập thông tin mới để cập nhật")
            col1, col2 = st.columns(2)
            with col1:
                ho_ten_moi = st.text_input("Họ tên", value=row["Họ tên"])
                bien_so_moi = st.text_input("Biển số xe", value=row["Biển số"])
                ten_don_vi_moi = st.text_input("Tên đơn vị", value=row["Tên đơn vị"])
                ma_don_vi_moi = st.text_input("Mã đơn vị", value=row["Mã đơn vị"])
            with col2:
                chuc_vu_moi = st.text_input("Chức vụ", value=row["Chức vụ"])
                so_dien_thoai_moi = st.text_input("Số điện thoại", value=row["Số điện thoại"])
                email_moi = st.text_input("Email", value=row["Email"])

            if st.button("Cập nhật"):
                sheet.update(f"A{index+2}:I{index+2}", [[
                    row["STT"],
                    ho_ten_moi,
                    bien_so_moi,
                    row["Mã thẻ"],
                    ma_don_vi_moi,
                    ten_don_vi_moi,
                    chuc_vu_moi,
                    so_dien_thoai_moi,
                    email_moi
                ]])
                st.success("✅ Đã cập nhật thông tin xe thành công!")

# ===================== XÓA XE =====================
elif choice == "Xóa xe":
    st.subheader("🗑️ Xóa xe khỏi danh sách")

    bien_so_input = st.text_input("Nhập biển số xe cần xóa")

    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df["Biển số chuẩn hóa"] = df["Biển số"].apply(normalize_plate)
        ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

        if ket_qua.empty:
            st.error("❌ Không tìm thấy biển số xe!")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

            index = ket_qua.index[0]
            row = ket_qua.iloc[0]

            if st.button("Xác nhận xóa"):
                sheet.delete_rows(index + 2)  # +2 vì Google Sheet bắt đầu từ dòng 1, có header
                st.success(f"🗑️ Đã xóa xe có biển số `{row['Biển số']}` thành công!")

# ===================== TẠO MÃ QR =====================
elif choice == "📱 Tạo mã QR":
    st.subheader("Tạo mã QR cho xe")
    bien_so = st.text_input("Nhập biển số xe")
    if bien_so in df["Biển số"].values:
        qr = qrcode.make(bien_so)
        buf = BytesIO()
        qr.save(buf)
        st.image(buf, caption=f"Mã QR cho xe {bien_so}")
    elif bien_so:
        st.error("❌ Không tìm thấy biển số xe!")

# ===================== XUẤT RA EXCEL =====================
elif choice == "📤 Xuất ra Excel":
    st.subheader("📤 Tải danh sách xe dưới dạng Excel")
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='DanhSachXe')
        writer.close()
        processed_data = output.getvalue()

    st.download_button(
        label="📥 Tải Excel",
        data=processed_data,
        file_name="DanhSachXe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
