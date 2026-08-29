# -*- coding: utf-8 -*-
"""ZIYI老师TOEFL 每日单词打卡 · Flask 服务端 (PythonAnywhere 版)
- 静态页面: /  /index.html  /data.js  /audio/*
- 学生数据同步: POST /api/student-sync
- 学生数据查询: GET  /api/students
- 单学生数据:   GET  /api/student?id=
- 健康检查:     GET  /api/ping
"""
import os
import json
import sqlite3
import threading
import time
import hashlib

from flask import Flask, request, jsonify, send_from_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, 'server-data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'students.db')

VOICE = os.environ.get('ZIYI_VOICE', 'en-US-GuyNeural')

app = Flask(__name__)

# ========== SQLite 学生数据 ==========
_db_lock = threading.Lock()

def init_db():
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
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM students WHERE sid=?', (sid,))
        conn.commit()
        conn.close()

# ========== 静态文件 ==========
@app.route('/')
@app.route('/index.html')
def index():
    return send_from_directory(ROOT, 'index.html')

@app.route('/data.js')
def data_js():
    return send_from_directory(ROOT, 'data.js', mimetype='application/javascript; charset=utf-8')

@app.route('/audio/<path:filename>')
def audio(filename):
    return send_from_directory(os.path.join(ROOT, 'audio'), filename)

# ========== API ==========
@app.route('/api/ping')
def api_ping():
    return jsonify({'ok': True, 'voice': VOICE})

@app.route('/api/students')
def api_students():
    students = get_all_students()
    return jsonify({'ok': True, 'students': students})

@app.route('/api/student')
def api_student():
    sid = request.args.get('id', '').strip()
    if not sid:
        return jsonify({'ok': False, 'err': 'no id'}), 400
    stu = get_student(sid)
    if not stu:
        return jsonify({'ok': False, 'err': 'not found'}), 404
    return jsonify({'ok': True, 'student': stu})

@app.route('/api/student-sync', methods=['POST'])
def api_student_sync():
    try:
        obj = request.get_json(force=True)
        sid = obj.get('id', '').strip()
        name = obj.get('name', '').strip()
        if not sid or not name:
            return jsonify({'ok': False, 'err': 'missing id/name'}), 400
        data = obj.get('data', {})
        upsert_student(sid, name, json.dumps(data, ensure_ascii=False))
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'err': str(e)}), 500

@app.route('/api/student', methods=['DELETE'])
def api_student_delete():
    sid = request.args.get('id', '').strip()
    if not sid:
        return jsonify({'ok': False, 'err': 'no id'}), 400
    delete_student(sid)
    return jsonify({'ok': True})

# ========== 启动 ==========
init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8770'))
    print(f'ZIYI老师TOEFL server  voice={VOICE}')
    print(f'  访问: http://localhost:{port}')
    print(f'  数据库: {DB_PATH}')
    app.run(host='0.0.0.0', port=port, debug=False)
