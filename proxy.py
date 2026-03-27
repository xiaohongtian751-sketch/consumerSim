from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import traceback
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
# 关键：允许所有来源跨域访问，彻底解决CORS报错
CORS(app, supports_credentials=True)

# -------------------------- 只改这里！填你的通义千问API Key --------------------------
DASHSCOPE_API_KEY = "sk-d17ce45b1dd74892bcb1f24b95938afa"
# -----------------------------------------------------------------------------------

API_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
TIMEOUT_SECONDS = 30

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "images", "uploads")
ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}


@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')


@app.route('/<path:path>')
def serve_static(path):
    # 避免把 /api/* 误当静态资源
    if path.startswith('api/'):
        return jsonify({"error": {"type": "not_found", "message": "API route not found"}}), 404
    return send_from_directory('.', path)

@app.route('/api/chat', methods=['POST', 'OPTIONS'])
def chat():
    # 处理浏览器的预检请求
    if request.method == 'OPTIONS':
        return jsonify({'code': 0}), 200
    
    # 转发请求给通义千问
    try:
        request_data = request.get_json(silent=True) or {}

        # 1) 优先使用前端传入的 Authorization Bearer Token；没有再回退默认 Key
        incoming_auth = (request.headers.get('Authorization') or '').strip()
        bearer = None
        if incoming_auth.lower().startswith('bearer '):
            token = incoming_auth[7:].strip()
            if token:
                bearer = token
        if not bearer:
            bearer = (DASHSCOPE_API_KEY or '').strip()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}"
        }

        resp = requests.post(API_URL, json=request_data, headers=headers, timeout=TIMEOUT_SECONDS)

        # 2) 非 200：返回结构化错误信息给前端（并打印上游错误）
        if resp.status_code < 200 or resp.status_code >= 300:
            body_text = ""
            body_json = None
            try:
                body_json = resp.json()
            except Exception:
                try:
                    body_text = resp.text
                except Exception:
                    body_text = ""

            app.logger.error(
                "DashScope upstream error: status=%s, body=%s",
                resp.status_code,
                (body_json if body_json is not None else body_text[:2000])
            )
            return jsonify({
                "error": {
                    "type": "upstream_error",
                    "message": "DashScope upstream returned non-2xx response",
                },
                "upstream_status": resp.status_code,
                "upstream_body": body_json if body_json is not None else body_text,
            }), resp.status_code

        # 3) 正常：尽量按 JSON 返回；不是 JSON 也包装返回
        try:
            return jsonify(resp.json()), resp.status_code
        except Exception:
            return jsonify({
                "error": {
                    "type": "upstream_non_json",
                    "message": "DashScope upstream returned non-JSON response",
                },
                "upstream_status": resp.status_code,
                "upstream_body": resp.text,
            }), 502

    except requests.exceptions.Timeout:
        app.logger.error("Proxy timeout after %ss", TIMEOUT_SECONDS)
        return jsonify({
            "error": {"type": "timeout", "message": f"Upstream request timed out after {TIMEOUT_SECONDS}s"}
        }), 504
    except requests.exceptions.RequestException as e:
        app.logger.error("Proxy network error: %s\n%s", str(e), traceback.format_exc())
        return jsonify({
            "error": {"type": "network_error", "message": str(e)}
        }), 502
    except Exception as e:
        app.logger.error("Proxy unexpected error: %s\n%s", str(e), traceback.format_exc())
        return jsonify({
            "error": {"type": "unexpected_error", "message": str(e)}
        }), 500


@app.route('/api/upload', methods=['POST', 'OPTIONS'])
def upload():
    # 处理浏览器的预检请求
    if request.method == 'OPTIONS':
        return jsonify({'code': 0}), 200

    try:
        if 'file' not in request.files:
            return jsonify({"error": {"type": "bad_request", "message": "missing file field"}}), 400

        f = request.files['file']
        if not f or not f.filename:
            return jsonify({"error": {"type": "bad_request", "message": "empty filename"}}), 400

        filename = secure_filename(f.filename)
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ALLOWED_EXTS:
            return jsonify({"error": {"type": "bad_request", "message": "unsupported file type"}}), 400

        os.makedirs(UPLOAD_DIR, exist_ok=True)

        # 防止覆盖：加一个时间戳前缀
        import time
        saved_name = f"{int(time.time() * 1000)}_{filename}"
        save_path = os.path.join(UPLOAD_DIR, saved_name)
        f.save(save_path)

        # 返回可被静态路由访问的路径
        url_path = f"/images/uploads/{saved_name}"
        return jsonify({"url": url_path, "path": url_path}), 200

    except Exception as e:
        app.logger.error("Upload error: %s\n%s", str(e), traceback.format_exc())
        return jsonify({"error": {"type": "upload_error", "message": str(e)}}), 500

if __name__ == '__main__':
    app.run(port=5000)
