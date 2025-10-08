import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import qrcode
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
SHEET_ID = "18fQqPJ5F9VZdWvkuQq5K7upQHeC7UfZX"
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
choice = st.sidebar.selectbox("Chọn chức năng", menu)

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
    st.subheader("Tìm kiếm xe")
    keyword = st.text_input("Nhập biển số hoặc tên đơn vị")
    if keyword:
        filtered = df[df.apply(lambda row: keyword.lower() in str(row).lower(), axis=1)]
        st.dataframe(filtered)

# ===================== ĐĂNG KÝ XE MỚI =====================
elif choice == "➕ Đăng ký xe mới":
    st.subheader("Đăng ký xe mới")
    col1, col2 = st.columns(2)
    with col1:
        bien_so = st.text_input("Biển số xe")
        mau_son = st.text_input("Màu sơn")
    with col2:
        chu_so_huu = st.text_input("Chủ sở hữu")
        don_vi = st.text_input("Tên đơn vị")

    if st.button("Lưu thông tin"):
        if not bien_so or not mau_son or not chu_so_huu or not don_vi:
            st.warning("⚠️ Vui lòng điền đầy đủ thông tin.")
        elif bien_so in df["Biển số"].values:
            st.error("❌ Biển số xe đã tồn tại!")
        else:
            sheet.append_row([bien_so, mau_son, chu_so_huu, don_vi])
            st.success("✅ Đã lưu thông tin xe thành công!")

# ===================== CẬP NHẬT XE =====================
elif choice == "✏️ Cập nhật xe":
    st.subheader("Cập nhật thông tin xe")
    bien_so = st.text_input("Nhập biển số xe cần cập nhật")
    if bien_so in df["Biển số"].values:
        index = df[df["Biển số"] == bien_so].index[0]
        mau_son = st.text_input("Màu sơn mới", df.at[index, "Màu sơn"])
        chu_so_huu = st.text_input("Chủ sở hữu mới", df.at[index, "Chủ sở hữu"])
        don_vi = st.text_input("Tên đơn vị mới", df.at[index, "Tên đơn vị"])
        if st.button("Cập nhật"):
            sheet.update(f"A{index+2}:D{index+2}", [[bien_so, mau_son, chu_so_huu, don_vi]])
            st.success("✅ Đã cập nhật thông tin xe!")
    elif bien_so:
        st.error("❌ Không tìm thấy biển số xe!")

# ===================== XÓA XE =====================
elif choice == "🗑️ Xóa xe":
    st.subheader("Xóa xe khỏi danh sách")
    bien_so = st.text_input("Nhập biển số xe cần xóa")
    if bien_so in df["Biển số"].values:
        index = df[df["Biển số"] == bien_so].index[0]
        if st.button("Xác nhận xóa"):
            sheet.delete_rows(index + 2)
            st.success("✅ Đã xóa xe khỏi danh sách!")
    elif bien_so:
        st.error("❌ Không tìm thấy biển số xe!")

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
