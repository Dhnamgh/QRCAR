import streamlit as st
import pandas as pd
import urllib.parse
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import qrcode
import re
from PIL import Image
from io import BytesIO

# 👉 Hàm xử lý biển số và tên
def normalize_plate(plate):
    return re.sub(r'[^a-zA-Z0-9]', '', plate).lower()

def format_name(name):
    return ' '.join(word.capitalize() for word in name.strip().split())

def format_plate(plate):
    plate = re.sub(r'[^a-zA-Z0-9]', '', plate).upper()
    if len(plate) >= 8:
        return f"{plate[:2]}{plate[2]}-{plate[3:6]}.{plate[6:]}"
    return plate

# 👉 Khởi tạo Google Sheet
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_dict = dict(st.secrets["google_service_account"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").strip()
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

# 👉 Sidebar: logo trường
st.sidebar.image("ump_logo.png", width=120)
st.sidebar.markdown("---")
st.markdown("<h1 style='text-align:center; color:#004080;'>🚗 QR Car Management</h1>", unsafe_allow_html=True)

# 👉 Xử lý tra cứu từ URL nếu có query_id
query_id = st.query_params.get("id", "")
if query_id:
    st.markdown("""
        <style>
            [data-testid="stSidebar"] {display: none;}
            [data-testid="stSidebarNav"] {display: none;}
            [data-testid="stSidebarContent"] {display: none;}
        </style>
    """, unsafe_allow_html=True)

    st.info(f"🔍 Đang tra cứu xe có biển số: {query_id}")
    mat_khau = st.text_input("🔑 Nhập mật khẩu để xem thông tin xe", type="password")

    try:
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ Không thể tải dữ liệu xe: {e}")
        st.stop()

    if mat_khau:
        if mat_khau.strip() != "qr@217hb":
            st.error("❌ Sai mật khẩu!")
        else:
            df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df[df["Biển số chuẩn hóa"] == normalize_plate(query_id)]

            if ket_qua.empty:
                st.error(f"❌ Không tìm thấy xe có biển số: {query_id}")
            else:
                st.success("✅ Thông tin xe:")
                st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

    st.stop()
menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📱 Mã QR xe",
    "📤 Xuất ra Excel",
    "📊 Thống kê xe theo đơn vị"
]

default_tab = "📱 Mã QR xe" if "id" in st.query_params else menu[0]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=menu.index(default_tab))

try:
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
except Exception as e:
    st.error(f"❌ Lỗi tải dữ liệu: {e}")
    st.stop()

if choice == "📋 Xem danh sách":
    st.subheader("📋 Danh sách xe đã đăng ký")

    # 👉 Chuẩn hóa biển số
    def dinh_dang_bien_so(bs):
        bs = re.sub(r"[^A-Z0-9]", "", bs.upper())
        if len(bs) == 8:
            return f"{bs[:3]}-{bs[3:6]}.{bs[6:]}"
        return bs

    df["Biển số"] = df["Biển số"].apply(dinh_dang_bien_so)

    # 👉 Hiển thị bảng full màn hình
    st.dataframe(df, use_container_width=True)
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

elif choice == "➕ Đăng ký xe mới":
    st.subheader("📋 Đăng ký xe mới")

    df = pd.DataFrame(sheet.get_all_records())

    # 👉 Hàm chuẩn hóa biển số
    def dinh_dang_bien_so(bs):
        bs = re.sub(r"[^A-Z0-9]", "", bs.upper())
        if len(bs) == 8:
            return f"{bs[:3]}-{bs[3:6]}.{bs[6:]}"
        return bs

    # 👉 Danh sách đơn vị
    don_vi_map = {
        "HCTH": "HCT", "TCCB": "TCC", "ĐTĐH": "DTD", "ĐTSĐH": "DTS", "KHCN": "KHC", "KHTC": "KHT",
        "QTGT": "QTG", "TTPC": "TTP", "ĐBCLGD&KT": "DBK", "CTSV": "CTS", "Trường Y": "TRY",
        "Trường Dược": "TRD", "Trường ĐD-KTYH": "TRK", "KHCB": "KHB", "RHM": "RHM", "YTCC": "YTC",
        "PK.CKRHM": "CKR", "TT.KCCLXN": "KCL", "TT.PTTN": "PTN", "TT.ĐTNLYT": "DTL", "TT.CNTT": "CNT",
        "TT.KHCN UMP": "KCU", "TT.YSHPT": "YSH", "Thư viện": "TV", "KTX": "KTX", "Tạp chí Y học": "TCY"
    }

    ten_don_vi = st.selectbox("Chọn đơn vị", list(don_vi_map.keys()))
    ma_don_vi = don_vi_map[ten_don_vi]

    col1, col2 = st.columns(2)
    with col1:
        ho_ten_raw = st.text_input("Họ tên")
        bien_so_raw = st.text_input("Biển số xe")
    with col2:
        chuc_vu_raw = st.text_input("Chức vụ")
        so_dien_thoai = st.text_input("Số điện thoại")
        email = st.text_input("Email")

    ho_ten = " ".join(word.capitalize() for word in ho_ten_raw.strip().split())
    chuc_vu = " ".join(word.capitalize() for word in chuc_vu_raw.strip().split())
    bien_so = dinh_dang_bien_so(bien_so_raw)

    bien_so_da_dang_ky = df["Biển số"].dropna().apply(dinh_dang_bien_so)

    if bien_so in bien_so_da_dang_ky.values:
        st.error("🚫 Biển số này đã được đăng ký trước đó!")
    elif not so_dien_thoai.startswith("0"):
        st.warning("⚠️ Số điện thoại phải bắt đầu bằng số 0.")
    elif ho_ten == "" or bien_so == "":
        st.warning("⚠️ Vui lòng nhập đầy đủ thông tin.")
    else:
        filtered = df["Mã thẻ"].dropna()[df["Mã thẻ"].str.startswith(ma_don_vi)]
        next_number = max(filtered.str.extract(f"{ma_don_vi}(\d{{3}})")[0].dropna().astype(int), default=0) + 1
        ma_the = f"{ma_don_vi}{next_number:03d}"

        st.markdown(f"🔐 **Mã thẻ tự sinh:** `{ma_the}`")
        st.markdown(f"🏢 **Mã đơn vị:** `{ma_don_vi}`")
   
        if st.button("📥 Đăng ký"):
            try:
                sheet.append_row([
                    len(df) + 1,
                    ho_ten,
                    bien_so,  # ✅ Biển số đã chuẩn hóa
                    ma_the,
                    ma_don_vi,
                    ten_don_vi,
                    chuc_vu,
                    so_dien_thoai,
                    email
                ])
                st.success(f"✅ Đã đăng ký xe cho `{ho_ten}` với mã thẻ: `{ma_the}`")
                st.toast("🎉 Dữ liệu đã được ghi vào Google Sheet!")
            except Exception as e:
                st.error(f"❌ Lỗi ghi dữ liệu: {e}")
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

elif choice == "🗑️ Xóa xe":
    st.subheader("🗑️ Xóa xe khỏi danh sách")
    bien_so_input = st.text_input("Nhập biển số xe cần xóa")
    if bien_so_input:
        try:
            bien_so_norm = normalize_plate(bien_so_input)
            df = df.copy()
            df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

            if ket_qua.empty:
                st.error("❌ Không tìm thấy biển số xe!")
            else:
                st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
                st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

                index = ket_qua.index[0]
                row = ket_qua.iloc[0]

                if st.button("Xác nhận xóa"):
                    sheet.delete_rows(index + 2)
                    st.success(f"🗑️ Đã xóa xe có biển số `{row['Biển số']}` thành công!")

        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "📱 Mã QR xe":
    st.subheader("📱 Mã QR xe")

    bien_so_url = st.query_params.get("id", "")

    if bien_so_url:
        st.info(f"🔍 Đang tra cứu xe có biển số: {bien_so_url}")
        mat_khau = st.text_input("🔑 Nhập mật khẩu để xem thông tin xe", type="password")

        if mat_khau:
            if mat_khau != "qr@217hb":
                st.error("❌ Sai mật khẩu!")
            else:
                bien_so_norm = normalize_plate(bien_so_url)
                df = df.copy()
                df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
                ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

                if ket_qua.empty:
                    st.error(f"❌ Không tìm thấy xe có biển số: {bien_so_url}")
                else:
                    st.success("✅ Thông tin xe:")
                    st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

    else:
        bien_so_input = st.text_input("📋 Nhập biển số xe để tạo mã QR")
        if bien_so_input:
            try:
                bien_so_norm = normalize_plate(bien_so_input)
                df = df.copy()
                df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
                ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

                if ket_qua.empty:
                    st.error(f"❌ Không tìm thấy xe có biển số: {bien_so_input}")
                else:
                    row = ket_qua.iloc[0]
                    link = f"https://qrcarump.streamlit.app/?id={bien_so_norm}"
                    img = qrcode.make(link)
                    buf = BytesIO()
                    img.save(buf)

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
        writer.close()
        processed_data = output.getvalue()

    st.download_button(
        label="📥 Tải Excel",
        data=processed_data,
        file_name="DanhSachXe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
elif choice == "📊 Thống kê xe theo đơn vị":
    st.markdown("## 📊 Dashboard thống kê xe theo đơn vị")

    df = pd.DataFrame(sheet.get_all_records())

    # 👉 Từ điển ánh xạ tên viết tắt → tên đầy đủ
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

    # 👉 Gom nhóm và tạo bảng thống kê
    thong_ke = df.groupby("Tên đơn vị").size().reset_index(name="Số lượng xe")
    thong_ke = thong_ke.sort_values(by="Số lượng xe", ascending=False)

    # 👉 Tạo cột tên đầy đủ cho bảng
    thong_ke["Tên đầy đủ"] = thong_ke["Tên đơn vị"].apply(lambda x: ten_day_du.get(x, x))

    # 👉 Vẽ biểu đồ dùng tên viết tắt
    import plotly.express as px
    fig = px.bar(
        thong_ke,
        x="Tên đơn vị",  # dùng viết tắt để trục X gọn
        y="Số lượng xe",
        color="Tên đơn vị",
        text="Số lượng xe",
        title="📈 Biểu đồ số lượng xe theo đơn vị"
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        xaxis=dict(tickfont=dict(size=14, family="Arial", color="black", weight="bold")),
        showlegend=False,
        height=600
    )

   # 👉 Biểu đồ sát trái, không thừa khoảng trắng
col1, col2 = st.columns([0.01, 0.99])
with col2:
    st.plotly_chart(fig, use_container_width=True)

# 👉 Bảng thống kê bên dưới, full chiều ngang
st.markdown("#### 📋 Bảng thống kê chi tiết")
thong_ke_display = thong_ke[["Tên đầy đủ", "Số lượng xe"]].rename(columns={"Tên đầy đủ": "Tên đơn vị"})
thong_ke_display.index = range(1, len(thong_ke_display) + 1)
st.dataframe(thong_ke_display, use_container_width=True)
# 👉 Nội dung chân trang
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
