from flask import Flask, render_template, request, send_file, jsonify, Response
import yt_dlp
import os
import uuid
import io
import time
import threading
import re

app = Flask(__name__)

DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

progress_dict = {}
result_dict = {}
cancel_flags = {} # Thêm từ điển lưu cờ báo "Hủy" cho từng file

def progress_hook(d):
    file_id = d['info_dict'].get('params', {}).get('file_id')
    if not file_id: return
    
    # KÍCH HOẠT HỦY: Nếu có lệnh hủy từ web, ép yt-dlp dừng lại
    if cancel_flags.get(file_id):
        raise ValueError("CANCELLED_BY_USER")
        
    if d['status'] == 'downloading':
        p = d.get('_percent_str', '0%').replace('%','').strip()
        p = re.sub(r'\x1b[^m]*m', '', p)
        progress_dict[file_id] = p
    elif d['status'] == 'finished':
        progress_dict[file_id] = '100'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/preview', methods=['POST'])
def preview_video():
    url = request.json.get('url')
    ydl_opts = {'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            formats = info.get('formats', [])
            res_dict = {}
            audio_size = 0
            
            # 1. Tìm dung lượng của file âm thanh tốt nhất (để cộng dồn vào video nếu cần)
            for f in formats:
                if f.get('vcodec') == 'none' and f.get('acodec') != 'none':
                    size = f.get('filesize') or f.get('filesize_approx') or 0
                    if size > audio_size:
                        audio_size = size

            # 2. Tìm dung lượng cho từng độ phân giải video
            for f in formats:
                height = f.get('height')
                vcodec = f.get('vcodec')
                
                if height and vcodec != 'none':
                    size = f.get('filesize') or f.get('filesize_approx') or 0
                    
                    # Nếu video bị tách tiếng (không có acodec), ta cộng thêm dung lượng audio ước tính
                    if f.get('acodec') == 'none' and size > 0:
                        size += audio_size
                        
                    if size > 0:
                        if height not in res_dict or size > res_dict[height]:
                            res_dict[height] = size
                    else:
                        if height not in res_dict:
                            res_dict[height] = 0 # 0 nghĩa là "Không rõ dung lượng"
            
            # 3. Sắp xếp từ nét nhất đến mờ nhất và quy đổi sang MB
            sorted_res = sorted(res_dict.keys(), reverse=True)
            res_list = []
            for h in sorted_res:
                size_bytes = res_dict[h]
                if size_bytes > 0:
                    size_mb = round(size_bytes / (1024 * 1024), 1) # Chuyển byte -> MB (Lấy 1 số lẻ)
                    size_text = f"~{size_mb} MB"
                else:
                    size_text = "Không rõ dung lượng"
                    
                res_list.append({
                    'res': f"{h}p",
                    'size': size_text
                })
            
            # Xử lý dung lượng riêng cho nút tải MP3
            audio_mb_text = "Không rõ dung lượng"
            if audio_size > 0:
                audio_mb = round(audio_size / (1024 * 1024), 1)
                audio_mb_text = f"~{audio_mb} MB"

            return jsonify({
                'success': True,
                'title': info.get('title', 'Video'),
                'uploader': info.get('uploader', 'Unknown'),
                'thumbnail': info.get('thumbnail', ''),
                'platform': info.get('extractor_key', 'Generic'),
                'resolutions': res_list,
                'audio_size': audio_mb_text
            })
    except Exception as e:
        return jsonify({'error': 'Không tìm thấy video'}), 500

# API MỚI: Nhận lệnh hủy từ giao diện
@app.route('/api/cancel/<file_id>', methods=['POST'])
def cancel_download(file_id):
    cancel_flags[file_id] = True
    return jsonify({'success': True})

@app.route('/api/stream-progress/<file_id>')
def stream_progress(file_id):
    def generate():
        while True:
            prog = progress_dict.get(file_id, "0")
            
            if prog == "error":
                yield "data: ERROR\n\n"
                break
            elif prog == "cancelled":
                yield "data: CANCELLED\n\n"
                break
            
            if prog == "100" and file_id in result_dict:
                filename = result_dict[file_id]
                yield f"data: DONE|{filename}\n\n"
                break
            
            yield f"data: {prog}\n\n"
            time.sleep(0.5)
    return Response(generate(), mimetype='text/event-stream')

def cleanup_partial_files(file_id):
    # Dọn dẹp các file rác đang tải dở
    for f in os.listdir(DOWNLOAD_FOLDER):
        if f.startswith(file_id):
            try: os.remove(os.path.join(DOWNLOAD_FOLDER, f))
            except: pass

def background_download(url, quality, file_id):
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_FOLDER}/{file_id}.%(ext)s',
        'progress_hooks': [progress_hook],
        'params': {'file_id': file_id},
        'quiet': True,
        'no_warnings': True,
    }

    # THUẬT TOÁN MỚI: Tự động nhận diện mọi độ phân giải (Kể cả 2K, 4K, 8K)
    if quality == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best', 
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
        })
    elif quality != 'best' and quality.endswith('p'):
        # Lấy con số chiều cao (vd: '2160p' -> '2160')
        height = quality.replace('p', '')
        ydl_opts['format'] = f'bestvideo[height<={height}]+bestaudio/best'
    else: 
        ydl_opts['format'] = 'bestvideo+bestaudio/best'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if cancel_flags.get(file_id):
                progress_dict[file_id] = "cancelled"
                cleanup_partial_files(file_id)
                return
                
            ext = 'mp3' if quality == 'audio' else info.get('ext', 'mp4')
            result_dict[file_id] = f"{file_id}.{ext}"
            progress_dict[file_id] = "100"
    except Exception as e:
        if cancel_flags.get(file_id):
            progress_dict[file_id] = "cancelled"
            cleanup_partial_files(file_id)
        else:
            progress_dict[file_id] = "error"

@app.route('/api/download', methods=['POST'])
def process_download():
    data = request.json
    url = data.get('url')
    quality = data.get('quality', 'best')
    file_id = str(uuid.uuid4())
    
    progress_dict[file_id] = "0"
    cancel_flags[file_id] = False
    
    threading.Thread(target=background_download, args=(url, quality, file_id)).start()
    
    return jsonify({'success': True, 'file_id': file_id})

@app.route('/download-file/<filename>')
def download_file(filename):
    filepath = os.path.join(DOWNLOAD_FOLDER, filename)
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f: data = io.BytesIO(f.read())
        os.remove(filepath)
        return send_file(data, as_attachment=True, download_name=filename)
    return "File Error", 404

if __name__ == '__main__':
    app.run(debug=True, port=5000)