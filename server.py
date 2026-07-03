from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import time
import os
import uuid
import random

app = Flask(__name__)

players = {}
last_update = {}

def get_db():
    conn = sqlite3.connect('database.db', timeout=5, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS blocks (x INTEGER, y INTEGER, type TEXT, PRIMARY KEY (x, y))''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (pid TEXT, block_type TEXT, count INTEGER, PRIMARY KEY (pid, block_type))''')
    c.execute('''CREATE TABLE IF NOT EXISTS players_data (pid TEXT PRIMARY KEY, name TEXT, skin TEXT, role TEXT, coins INTEGER, unlocked_skins TEXT, x REAL, y REAL, home_x REAL, home_y REAL)''')
    conn.commit()
    conn.close()

init_db()

def generate_world():
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM blocks')
    for x in range(-32, 33):
        for y in range(-32, 33):
            if abs(x) == 32 or abs(y) == 32:
                c.execute('INSERT OR IGNORE INTO blocks (x, y, type) VALUES (?, ?, ?)', (x, y, 'barrier'))
            elif y == 0:
                c.execute('INSERT OR IGNORE INTO blocks (x, y, type) VALUES (?, ?, ?)', (x, y, 'grass'))
    conn.commit()
    conn.close()

generate_world()

def get_blocks():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT x, y, type FROM blocks')
    return [{'x': r[0], 'y': r[1], 'type': r[2]} for r in c.fetchall()]

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve(path):
    if os.path.exists(path):
        return send_from_directory('.', path)
    return 'Not found', 404

@app.route('/join', methods=['POST'])
def join():
    data = request.json
    name = data.get('name', 'Игрок')[:12]
    skin = data.get('skin', 'novice.png')

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM players_data WHERE name = ?', (name,))
    row = c.fetchone()

    if row:
        pid = row[0]
        role = row[3]
        if skin == 'admin.png' and role != 'admin':
            role = 'admin'
            c.execute('UPDATE players_data SET role = ? WHERE pid = ?', ('admin', pid))
        elif skin == 'mod.png' and role != 'mod':
            role = 'mod'
            c.execute('UPDATE players_data SET role = ? WHERE pid = ?', ('mod', pid))
        elif skin == 'owner.png' and role != 'owner':
            role = 'owner'
            c.execute('UPDATE players_data SET role = ? WHERE pid = ?', ('owner', pid))
            
        unlocked = row[5].split(',') if row[5] else ['novice.png']
        if role != 'player' and role + '.png' not in unlocked:
            unlocked.append(role + '.png')
            
        players[pid] = {
            'name': row[1], 
            'skin': skin, 
            'role': role, 
            'coins': row[4],
            'unlocked_skins': unlocked,
            'x': row[6] if row[6] is not None else 0.0,
            'y': row[7] if row[7] is not None else 0.8,
            'home': (row[8], row[9]) if row[8] is not None else None
        }
    else:
        pid = str(uuid.uuid4())[:6]
        role = 'player'
        if skin == 'admin.png': role = 'admin'
        elif skin == 'mod.png': role = 'mod'
        elif skin == 'owner.png': role = 'owner'
        unlocked = ['novice.png']
        if role != 'player':
            unlocked.append(skin)

        players[pid] = {
            'name': name, 
            'skin': skin, 
            'role': role, 
            'coins': 0,
            'unlocked_skins': unlocked,
            'x': 0.0, 
            'y': 0.8, 
            'home': None
        }
        c.execute('INSERT INTO players_data (pid, name, skin, role, coins, unlocked_skins, x, y, home_x, home_y) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                  (pid, name, skin, role, 0, ','.join(unlocked), 0.0, 0.8, None, None))
        for b in ['grass', 'stone', 'wood']:
            c.execute('INSERT OR IGNORE INTO inventory (pid, block_type, count) VALUES (?, ?, ?)', (pid, b, 64))

    conn.commit()
    conn.close()
    last_update[pid] = time.time()

    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT block_type, count FROM inventory WHERE pid = ?', (pid,))
    inv = {r[0]: r[1] for r in c.fetchall()}
    conn.close()

    return jsonify({
        'status': 'ok',
        'pid': pid,
        'players': players,
        'blocks': get_blocks(),
        'inventory': inv,
        'coins': players[pid]['coins'],
        'unlocked_skins': players[pid]['unlocked_skins']
    })

@app.route('/update', methods=['POST'])
def update():
    data = request.json
    pid = data.get('pid')
    
    # Если игрока нет в памяти, пробуем восстановить из БД
    if not pid or pid not in players:
        return jsonify({'error': 'not found'}), 404

    player = players[pid]
    now = time.time()
    last_update[pid] = now

    # === КРИТИЧЕСКИ ВАЖНО: сохраняем позицию ===
    if 'x' in data:
        player['x'] = float(data['x'])
    if 'y' in data:
        player['y'] = float(data['y'])

    tx = data.get('tx')
    ty = data.get('ty')
    action = data.get('action')
    result = {'success': False, 'message': ''}

    # Обработка блоков
    if tx is not None and ty is not None and action:
        px = player['x']
        py = player['y']
        dist = ((px - tx)**2 + (py - ty)**2)**0.5
        is_admin = player['role'] in ('admin', 'mod', 'owner')
        max_dist = 999 if is_admin else 2.5

        if dist > max_dist:
            result = {'success': False, 'message': 'Слишком далеко'}
        else:
            conn = get_db()
            c = conn.cursor()
            c.execute('SELECT type FROM blocks WHERE x = ? AND y = ?', (tx, ty))
            existing = c.fetchone()

            if action == 'break':
                if existing and existing[0] != 'barrier':
                    c.execute('DELETE FROM blocks WHERE x = ? AND y = ?', (tx, ty))
                    result = {'success': True, 'action': 'break'}
                else:
                    result = {'success': False, 'message': 'Нельзя сломать'}
            elif action == 'place':
                bt = data.get('block_type', 'grass')
                if not existing:
                    c.execute('SELECT count FROM inventory WHERE pid = ? AND block_type = ?', (pid, bt))
                    inv = c.fetchone()
                    if inv and inv[0] > 0 and abs(tx) < 31 and abs(ty) < 31:
                        c.execute('INSERT INTO blocks (x, y, type) VALUES (?, ?, ?)', (tx, ty, bt))
                        c.execute('UPDATE inventory SET count = count - 1 WHERE pid = ? AND block_type = ?', (pid, bt))
                        result = {'success': True, 'action': 'place'}
                    else:
                        result = {'success': False, 'message': 'Нет ресурсов'}
                else:
                    result = {'success': False, 'message': 'Блок занят'}
            conn.commit()
            conn.close()

    # Удаляем неактивных
    for p_id in list(players.keys()):
        if time.time() - last_update.get(p_id, 0) > 60:
            del players[p_id]

    # Получаем инвентарь
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT block_type, count FROM inventory WHERE pid = ?', (pid,))
    inv = {r[0]: r[1] for r in c.fetchall()}
    conn.close()

    # Возвращаем ВСЁ состояние
    return jsonify({
        'players': players,
        'blocks': get_blocks(),
        'inventory': inv,
        'action_result': result
    })

@app.route('/creative_items', methods=['GET'])
def creative_items():
    return jsonify({'items': ['grass', 'stone', 'wood', 'barrier']})

@app.route('/creative_give', methods=['POST'])
def creative_give():
    data = request.json
    pid, bt = data.get('pid'), data.get('block_type')
    if not pid or not bt:
        return jsonify({'error': 'invalid'}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO inventory (pid, block_type, count) VALUES (?, ?, COALESCE((SELECT count FROM inventory WHERE pid = ? AND block_type = ?) + 64, 64))',
              (pid, bt, pid, bt))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/command', methods=['POST'])
def command():
    data = request.json
    pid = data.get('pid')
    cmd = data.get('cmd', '').strip()
    if not pid or pid not in players:
        return jsonify({'error': 'not found'}), 404

    player = players[pid]
    parts = cmd.split()
    if not parts:
        return jsonify({'result': 'Пустая команда'})

    c = parts[0].lower()
    args = parts[1:]

    if c == '/admin' and args and args[0] == '321nimda':
        player['role'] = 'admin'
        if 'admin.png' not in player['unlocked_skins']:
            player['unlocked_skins'].append('admin.png')
        return jsonify({'result': '✅ Ты стал АДМИНОМ!'})

    elif c == '/mod' and args and args[0] == 'modmod_123':
        player['role'] = 'mod'
        if 'mod.png' not in player['unlocked_skins']:
            player['unlocked_skins'].append('mod.png')
        return jsonify({'result': '✅ Ты стал МОДЕРАТОРОМ!'})

    elif c == '/coins':
        return jsonify({'result': f'💰 У тебя {int(player.get("coins", 0))} монет'})

    elif c == '/skins':
        return jsonify({'result': '🎨 Скины: ' + ', '.join(player.get('unlocked_skins', ['novice.png']))})

    elif c == '/sethome':
        player['home'] = (player['x'], player['y'])
        return jsonify({'result': f'🏠 Дом установлен!'})

    elif c == '/home':
        if player.get('home'):
            player['x'], player['y'] = player['home']
            player['y'] += 0.5
            return jsonify({'result': '🏠 Телепортирован домой!'})
        return jsonify({'result': '❌ Дом не установлен'})

    elif c == '/help':
        return jsonify({'result': """📋 Команды:
/admin 321nimda - стать админом
/mod modmod_123 - стать модератором
/coins - монеты
/skins - список скинов
/sethome - установить дом
/home - телепорт домой""".replace('    ', '')})

    else:
        return jsonify({'result': f'❌ Неизвестная команда. Используй /help'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
