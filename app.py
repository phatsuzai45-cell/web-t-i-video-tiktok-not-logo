from flask import Flask, render_template, request, send_file, jsonify
import yt_dlp
import os
import uuid
import io

app = Flask(__name__)

DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return render_template('index.html')

# API Lấy thông tin video (Preview)
@app.route('/api/preview', methods=['POST'])
def preview_video():
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'Vui lòng nhập link!'}), 400

    ydl_opts = {'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                'success': True,
                'title': info.get('title', 'Video TikTok'),
                'uploader': info.get('uploader', 'Người dùng ẩn danh'),
                'thumbnail': info.get('thumbnail', '')
            })
    except Exception as e:
        return jsonify({'error': 'Không thể lấy thông tin video. Link có thể bị sai hoặc riêng tư.'}), 500

# API Tải video
@app.route('/api/download', methods=['POST'])
def process_download():
    data = request.json
    url = data.get('url')
    quality = data.get('quality', 'video')

    file_id = str(uuid.uuid4())
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_FOLDER}/{file_id}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    if quality == 'audio':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        ydl_opts['format'] = 'bestvideo+bestaudio/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            ext = 'mp3' if quality == 'audio' else info.get('ext', 'mp4')
            filename = f"{file_id}.{ext}"
            return jsonify({
                'success': True, 
                'download_url': f'/download-file/{filename}'
            })
    except Exception as e:
        return jsonify({'error': 'Lỗi khi tải! Đảm bảo link TikTok hợp lệ.'}), 500

# Trả file cho người dùng & TỰ ĐỘNG DỌN RÁC
@app.route('/download-file/<filename>')
def download_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        # Đọc file vào bộ nhớ đệm
        with open(filepath, 'rb') as f:
            data = io.BytesIO(f.read())
        
        # Xóa file vật lý trên ổ cứng ngay lập tức (Dọn rác)
        os.remove(filepath)
        
        # Gửi file từ bộ nhớ ảo cho trình duyệt tải về
        return send_file(data, as_attachment=True, download_name=filename)
    return "File không tồn tại hoặc đã bị xóa", 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)