# -*- coding: utf-8 -*-
"""ZIYI老师TOEFL 每日单词打卡 · 服务端
- 静态页面: /  /index.html  /data.js  /audio/*
- 真人语音: /api/tts?text=...&rate=-12
- 学生数据同步: POST /api/student-sync  (学生上传进度)
- 学生数据查询: GET  /api/students      (教师获取全量)
- 单学生数据:   GET  /api/student?id=   (单个学生详情)
- 健康检查:     GET  /api/ping
"""
import http.server
import socketserver
import urllib.parse
import json
import os
import re
import hashlib
import asyncio
import threading
import sqlite3
import time

import edge_tts

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, 'server-data')
TTS_CACHE = os.path.join(DATA_DIR, 'tts-cache')
DB_PATH = os.path.join(DATA_DIR, 'students.db')
os.makedirs(TTS_CACHE, exist_ok=True)

VOICE = os.environ.get('ZIYI_VOICE', 'en-US-GuyNeural')
PORT = int(os.environ.get('PORT', os.environ.get('ZIYI_PORT', '8770')))

# ========== SQLite 学生数据 ==========
_db_lock = threading.Lock()

def init_db():
    """建表（首次运行自动创建）"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS students (
            sid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_at REAL NOT NULL
        )''')
        conn.commit()
        conn.close()

def upsert_student(sid, name, data_json):
    """插入或更新一个学生的完整数据"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO students (sid, name, data, updated_at)
                     VALUES (?, ?, ?, ?)
                     ON CONFLICT(sid) DO UPDATE SET
                       name=excluded.name, data=excluded.data, updated_at=excluded.updated_at''',
                  (sid, name, data_json, time.time()))
        conn.commit()
        conn.close()

def get_all_students():
    """获取所有学生数据（教师后台用）"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT sid, name, data, updated_at FROM students ORDER BY updated_at DESC')
        rows = c.fetchall()
        conn.close()
    result = []
    for r in rows:
        try:
            data = json.loads(r['data'])
        except:
            data = {}
        result.append({'sid': r['sid'], 'name': r['name'], 'data': data, 'updatedAt': r['updated_at']})
    return result

def get_student(sid):
    """获取单个学生数据"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT sid, name, data, updated_at FROM students WHERE sid=?', (sid,))
        r = c.fetchone()
        conn.close()
    if not r:
        return None
    try:
        data = json.loads(r['data'])
    except:
        data = {}
    return {'sid': r['sid'], 'name': r['name'], 'data': data, 'updatedAt': r['updated_at']}

def delete_student(sid):
    """删除一个学生"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM students WHERE sid=?', (sid,))
        conn.commit()
        conn.close()

# ========== TTS ==========
def gen_tts_sync(text, rate):
    key = hashlib.md5(f'{VOICE}|{rate}|{text}'.encode('utf-8')).hexdigest()
    path = os.path.join(TTS_CACHE, key + '.mp3')
    if not os.path.exists(path) or os.path.getsize(path) < 200:
        async def run():
            c = edge_tts.Communicate(text, VOICE, rate=rate)
            await c.save(path + '.tmp')
        asyncio.run(run())
        os.replace(path + '.tmp', path)
    return path

# ========== HTTP Handler ==========
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ZiyiTOEFL/2.0'

    def log_message(self, fmt, *args):
        if os.environ.get('RENDER') or os.environ.get('RAILWAY_ENVIRONMENT'):
            print(f"[{self.address_string()}] {fmt % args}", flush=True)

    # ---------- helpers ----------
    def _send(self, code, body=b'', ctype='text/plain; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _send_file(self, path, ctype):
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length) if length > 0 else b''

    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # 静态文件
        if route in ('/', '/index.html'):
            return self._send_file(os.path.join(ROOT, 'index.html'), 'text/html; charset=utf-8')
        if route == '/data.js':
            return self._send_file(os.path.join(ROOT, 'data.js'), 'application/javascript; charset=utf-8')

        # audio 目录（预生成真人语音）
        if route.startswith('/audio/'):
            fname = route.split('/')[-1]
            fpath = os.path.join(ROOT, 'audio', fname)
            if os.path.exists(fpath) and not '..' in fname:
                ctype = 'audio/mpeg' if fname.endswith('.mp3') else 'application/octet-stream'
                if fname.endswith('.json'):
                    ctype = 'application/json; charset=utf-8'
                return self._send_file(fpath, ctype)
            return self._send(404, b'not found')

        # API
        if route == '/api/ping':
            return self._send_json({'ok': True, 'voice': VOICE})

        if route == '/api/tts':
            text = (qs.get('text') or [''])[0].strip()
            rate = (qs.get('rate') or ['0'])[0]
            if not rate.startswith(('+', '-')):
                rate = ('+' if int(rate) >= 0 else '') + rate + '%'
            else:
                rate = rate + '%'
            if not text:
                return self._send_json({'ok': False, 'err': 'no text'}, 400)
            if len(text) > 600:
                text = text[:600]
            try:
                path = gen_tts_sync(text, rate)
                return self._send_file(path, 'audio/mpeg')
            except Exception as e:
                return self._send_json({'ok': False, 'err': str(e)}, 500)

        # 教师后台：获取所有学生数据
        if route == '/api/students':
            students = get_all_students()
            return self._send_json({'ok': True, 'students': students})

        # 单个学生数据
        if route == '/api/student':
            sid = (qs.get('id') or [''])[0]
            if not sid:
                return self._send_json({'ok': False, 'err': 'no id'}, 400)
            stu = get_student(sid)
            if not stu:
                return self._send_json({'ok': False, 'err': 'not found'}, 404)
            return self._send_json({'ok': True, 'student': stu})

        return self._send(404, b'not found')

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        # 学生数据同步
        if route == '/api/student-sync':
            try:
                body = self._read_body()
                obj = json.loads(body.decode('utf-8'))
                sid = obj.get('id', '').strip()
                name = obj.get('name', '').strip()
                if not sid or not name:
                    return self._send_json({'ok': False, 'err': 'missing id/name'}, 400)
                # 只存需要的数据字段（不存 recordings 音频等大字段）
                data = obj.get('data', {})
                upsert_student(sid, name, json.dumps(data, ensure_ascii=False))
                return self._send_json({'ok': True})
            except Exception as e:
                return self._send_json({'ok': False, 'err': str(e)}, 500)

        return self._send(404, b'not found')

    # ---------- DELETE ----------
    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if route == '/api/student':
            sid = (qs.get('id') or [''])[0]
            if not sid:
                return self._send_json({'ok': False, 'err': 'no id'}, 400)
            delete_student(sid)
            return self._send_json({'ok': True})

        return self._send(404, b'not found')


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    init_db()
    print(f'ZIYI老师TOEFL server  voice={VOICE}')
    print(f'  访问: http://localhost:{PORT}')
    print(f'  数据库: {DB_PATH}')
    print('  Ctrl+C 退出')
    httpd = Server(('0.0.0.0', PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
