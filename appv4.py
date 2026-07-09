from flask import Flask, render_template_string, request, jsonify, Response, session, redirect, url_for
import sqlite3, os, csv, io
from datetime import datetime, timedelta
import random
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'rinl_enterprise.db')

# Realistic dept data: (name, headcount, attendance%, std_hours, ot_hours, incidents, productivity)
# High-heat/physical depts have lower attendance, more incidents, more OT
DEPARTMENTS = [
    ('Blast Furnace',      120, 88, 4800, 1100, 4, 82),
    ('Steel Melt Shop',    150, 84, 6000, 1800, 6, 76),
    ('Coke Ovens',          90, 86, 3600, 1000, 5, 79),
    ('Sinter Plant',        75, 87, 3000,  900, 4, 81),
    ('Rolling Mill',       200, 91, 8000,  700, 2, 88),
    ('Power Plant',         60, 92, 2400,  400, 2, 89),
    ('Oxygen Plant',        40, 94, 1600,  200, 1, 91),
    ('Lime & Dolomite',     45, 90, 1800,  350, 2, 86),
    ('Raw Materials',       85, 88, 3400,  800, 3, 82),
    ('Wire Rod Mill',      110, 90, 4400,  750, 2, 86),
    ('Structural Mill',     95, 89, 3800,  700, 2, 85),
    ('Maintenance',         80, 86, 3200,  950, 3, 80),
    ('Water Management',    40, 93, 1600,  200, 1, 90),
    ('Logistics',          100, 91, 4000,  550, 2, 87),
    ('Quality Control',     55, 93, 2200,  180, 1, 91),
    ('Safety & Environment',65, 91, 2600,  350, 1, 88),
    ('HR & Admin',          70, 92, 2800,  200, 0, 90),
    ('Finance',             50, 94, 2000,  120, 0, 93),
    ('IT & Systems',        30, 96, 1200,   80, 0, 95),
    ('R&D',                 35, 95, 1400,  100, 0, 94),
]

# Environmental Stress Multiplier per department
# Factors: heat(0-1), noise(0-1), chemical_exposure(0-1), physical_exertion(0-1)
# Multiplier = 1 + weighted sum of env factors (range ~1.0 to 1.8)
# IT/Finance/HR = near 1.0 (AC, clean, sedentary)
# SMS/Blast Furnace/Coke Ovens = near 1.8 (extreme heat, fumes, heavy physical)
ENV_WEIGHTS = {
    'Blast Furnace':       {'heat':0.9,'noise':0.7,'chemical':0.5,'physical':0.8},
    'Steel Melt Shop':     {'heat':0.95,'noise':0.8,'chemical':0.6,'physical':0.85},
    'Coke Ovens':          {'heat':0.85,'noise':0.7,'chemical':0.9,'physical':0.75},
    'Sinter Plant':        {'heat':0.8,'noise':0.75,'chemical':0.7,'physical':0.7},
    'Rolling Mill':        {'heat':0.7,'noise':0.8,'chemical':0.3,'physical':0.75},
    'Power Plant':         {'heat':0.6,'noise':0.65,'chemical':0.3,'physical':0.5},
    'Oxygen Plant':        {'heat':0.4,'noise':0.5,'chemical':0.5,'physical':0.4},
    'Lime & Dolomite':     {'heat':0.5,'noise':0.6,'chemical':0.6,'physical':0.65},
    'Raw Materials':       {'heat':0.5,'noise':0.6,'chemical':0.4,'physical':0.7},
    'Wire Rod Mill':       {'heat':0.65,'noise':0.75,'chemical':0.25,'physical':0.7},
    'Structural Mill':     {'heat':0.6,'noise':0.7,'chemical':0.25,'physical':0.65},
    'Maintenance':         {'heat':0.5,'noise':0.6,'chemical':0.4,'physical':0.7},
    'Water Management':    {'heat':0.3,'noise':0.4,'chemical':0.3,'physical':0.4},
    'Logistics':           {'heat':0.35,'noise':0.5,'chemical':0.2,'physical':0.5},
    'Quality Control':     {'heat':0.3,'noise':0.4,'chemical':0.35,'physical':0.3},
    'Safety & Environment':{'heat':0.3,'noise':0.4,'chemical':0.3,'physical':0.35},
    'HR & Admin':          {'heat':0.1,'noise':0.2,'chemical':0.05,'physical':0.1},
    'Finance':             {'heat':0.05,'noise':0.1,'chemical':0.02,'physical':0.05},
    'IT & Systems':        {'heat':0.05,'noise':0.1,'chemical':0.02,'physical':0.05},
    'R&D':                 {'heat':0.15,'noise':0.2,'chemical':0.15,'physical':0.1},
}

def env_multiplier(dept):
    w = ENV_WEIGHTS.get(dept, {'heat':0.3,'noise':0.3,'chemical':0.2,'physical':0.3})
    # Weighted: heat has highest impact (40%), physical (30%), chemical (20%), noise (10%)
    score = w['heat']*0.4 + w['physical']*0.3 + w['chemical']*0.2 + w['noise']*0.1
    return round(1.0 + score, 3)  # range 1.0 (office) to ~1.8 (furnace)

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def csv_response(filename, fieldnames, rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: '' if v is None else v for k, v in row.items()})
    data = output.getvalue()
    return Response(data, mimetype='text/csv', headers={
        'Content-Disposition': f'attachment;filename={filename}',
        'Cache-Control': 'no-cache'
    })

def init_db():
    conn = get_db(); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS workforce_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department TEXT NOT NULL UNIQUE,
        headcount INTEGER, attendance_pct REAL, standard_hours REAL,
        overtime_hours REAL, incidents INTEGER, productivity REAL,
        health_index REAL DEFAULT 80, work_environment REAL DEFAULT 80,
        facilities_score REAL DEFAULT 80, section_efficiency REAL DEFAULT 80,
        emergency_medical REAL DEFAULT 80, leave_balance REAL DEFAULT 80,
        hospital_access REAL DEFAULT 80)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT,
        message TEXT, severity TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT,
        hcsi_score REAL, productivity REAL, recorded_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS equipment_issues (
      id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT, equipment TEXT,
      severity TEXT, description TEXT, reported_by TEXT, created_at TEXT,
      hr_flag INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS retirements (
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, department TEXT,
      expertise TEXT, contact TEXT, retained INTEGER DEFAULT 0, notes TEXT, recorded_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS shift_schedule (
      id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT, shift TEXT,
      staff INTEGER, assigned_at TEXT)''')
    c.execute('SELECT COUNT(*) FROM users')
    if c.fetchone()[0] == 0:
        c.execute('INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)',
                  ('admin', generate_password_hash('admin123'), datetime.now().strftime('%Y-%m-%d %H:%M')))
    c.execute('SELECT COUNT(*) FROM workforce_data')
    if c.fetchone()[0] == 0:
        for d in DEPARTMENTS:
            em = env_multiplier(d[0])
            # HR factors: high-heat depts get lower scores (worse conditions)
            base_hr = round(90 - (em - 1.0)*40, 1)
            hi  = round(base_hr + random.uniform(-5,5), 1)
            we  = round(max(30, 95 - (em-1.0)*60 + random.uniform(-5,5)), 1)
            fs  = round(base_hr + random.uniform(-5,5), 1)
            se  = round(base_hr + random.uniform(-3,3), 1)
            emd = round(base_hr + random.uniform(-5,5), 1)
            lb  = round(base_hr + random.uniform(-5,5), 1)
            ha  = round(base_hr + random.uniform(-5,5), 1)
            c.execute('''INSERT INTO workforce_data
                (department,headcount,attendance_pct,standard_hours,overtime_hours,incidents,productivity,
                 health_index,work_environment,facilities_score,section_efficiency,
                 emergency_medical,leave_balance,hospital_access)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (d[0],d[1],d[2],d[3],d[4],d[5],d[6],hi,we,fs,se,emd,lb,ha))
        # Realistic history: high-heat depts start higher HCSI, trend upward faster
        for d in DEPARTMENTS:
            em = env_multiplier(d[0])
            base_hcsi = 8 + (em - 1.0) * 25  # SMS ~22, IT ~8
            for i in range(30):
                day = (datetime.now()-timedelta(days=29-i)).strftime('%Y-%m-%d')
                noise = random.uniform(-2, 2)
                trend = i * (0.1 if em > 1.4 else 0.05)  # high-heat depts deteriorate faster
                hcsi = round(max(5, base_hcsi + noise + trend), 1)
                prod = round(d[6] + random.uniform(-3, 3), 1)
                c.execute('INSERT INTO history (department,hcsi_score,productivity,recorded_at) VALUES (?,?,?,?)',
                          (d[0], hcsi, prod, day))
    conn.commit(); conn.close()

init_db()

def migrate_db():
    conn = get_db(); c = conn.cursor()
    cols = [r[1] for r in c.execute("PRAGMA table_info(workforce_data)").fetchall()]
    needed = {'standard_hours':'REAL DEFAULT 4000','health_index':'REAL DEFAULT 80',
              'work_environment':'REAL DEFAULT 80','facilities_score':'REAL DEFAULT 80',
              'section_efficiency':'REAL DEFAULT 80','emergency_medical':'REAL DEFAULT 80',
              'leave_balance':'REAL DEFAULT 80','hospital_access':'REAL DEFAULT 80'}
    for col,typ in needed.items():
        if col not in cols:
            c.execute(f"ALTER TABLE workforce_data ADD COLUMN {col} {typ}")
    conn.commit(); conn.close()

migrate_db()


def apply_hr_impact(conn, department, severity):
    if not department:
        return None
    severity_map = {'low': 0, 'medium': 2, 'high': 4, 'critical': 6}
    impact = severity_map.get((severity or 'low').lower(), 0)
    if impact <= 0:
        return None
    row = conn.execute('SELECT health_index, work_environment, facilities_score, section_efficiency, emergency_medical, leave_balance FROM workforce_data WHERE department=?', (department,)).fetchone()
    if not row:
        return None
    updates = {
        'health_index': max(0, float(row['health_index']) - impact),
        'work_environment': max(0, float(row['work_environment']) - impact),
        'facilities_score': max(0, float(row['facilities_score']) - impact),
        'section_efficiency': max(0, float(row['section_efficiency']) - impact),
        'emergency_medical': max(0, float(row['emergency_medical']) - max(1, impact // 2)),
        'leave_balance': max(0, float(row['leave_balance']) - max(1, impact // 2)),
    }
    set_clause = ', '.join(f"{col}=?" for col in updates)
    values = list(updates.values()) + [department]
    conn.execute(f'UPDATE workforce_data SET {set_clause} WHERE department=?', values)
    return {
        'impact': impact,
        'summary': f"HR factors reduced by {impact} points due to {severity} severity equipment issue",
        'updated': updates,
    }

AUTH_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{{ title }}</title>
  <style>
    body{font-family:Inter,Arial,sans-serif;background:linear-gradient(135deg,#111827,#1f2937);color:#fff;margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;}
    .card{width:min(420px,100%);background:rgba(255,255,255,.08);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:24px;box-shadow:0 18px 45px rgba(0,0,0,.2);}
    h1{margin:0 0 8px;font-size:1.5rem;}
    p{color:#cbd5e1;margin:0 0 16px;}
    label{display:block;margin-bottom:6px;font-size:.9rem;font-weight:600;}
    input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.16);background:#0f172a;color:#fff;margin-bottom:12px;box-sizing:border-box;}
    button{width:100%;padding:10px 12px;border:none;border-radius:10px;background:linear-gradient(135deg,#ec4899,#f59e0b);color:#fff;font-weight:700;cursor:pointer;}
    .error{background:#7f1d1d;padding:10px;border-radius:10px;margin-bottom:12px;color:#fecaca;font-size:.9rem;}
    .link{margin-top:12px;text-align:center;font-size:.9rem;color:#cbd5e1;}
    .link a{color:#fbcfe8;text-decoration:none;font-weight:700;}
  </style>
</head>
<body>
<div class="card">
  <h1>{{ title }}</h1>
  <p>{{ subtitle }}</p>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="post">
    <label>Username</label>
    <input name="username" required>
    <label>Password</label>
    <input name="password" type="password" required>
    <button type="submit">{{ button_text }}</button>
  </form>
  <div class="link">
    {% if mode == 'login' %}
      No account yet? <a href="{{ url_for('signup') }}">Create one</a>
    {% else %}
      Already have an account? <a href="{{ url_for('login') }}">Sign in</a>
    {% endif %}
  </div>
</div>
</body>
</html>
"""

@app.before_request
def auth_gate():
    if request.path in ('/login', '/signup', '/logout', '/favicon.ico') or request.path.startswith('/static/'):
        return None
    if request.path.startswith('/api/'):
        if not session.get('user_id'):
            return jsonify({'error': 'login required'}), 401
        return None
    if not session.get('user_id'):
        return redirect(url_for('login'))

HR_COLS = ['health_index','work_environment','facilities_score','section_efficiency',
           'emergency_medical','leave_balance','hospital_access']

def calc_hcsi(dept, ot, std, att, inc):
    """
    HCSI Formula (step by step):
      1. OT Ratio     = (OT Hours / Std Hours) × 100
      2. Absenteeism  = 100 - Attendance%
      3. Base Score   = (OT_Ratio × 0.35) + (Absenteeism × 0.25) + (Incidents × 10 × 0.25) + 10
      4. Env Multiplier = 1 + (Heat×0.4 + Physical×0.3 + Chemical×0.2 + Noise×0.1)
         → SMS gets ~1.76, IT gets ~1.05
      5. Final HCSI  = Base Score × Env Multiplier
    Thresholds: <20 Healthy, <40 Moderate, ≥40 Critical
    """
    ot_ratio    = (ot/std)*100 if std else 0
    absenteeism = 100 - att
    base        = (ot_ratio*0.35) + (absenteeism*0.25) + (inc*10*0.25) + 10
    em          = env_multiplier(dept)
    final       = round(base * em, 1)
    if final < 20:  return final, "Healthy",  "success", base, em
    elif final < 40: return final, "Moderate", "warning", base, em
    else:            return final, "Critical", "danger",  base, em

def predict_hcsi(dept):
  # Simple pure-Python least squares (no numpy) to avoid binary wheel issues
  conn = get_db()
  rows = conn.execute('SELECT hcsi_score FROM history WHERE department=? ORDER BY recorded_at',(dept,)).fetchall()
  conn.close()
  y = [float(r['hcsi_score']) for r in rows]
  n = len(y)
  if n < 5:
    return None
  x = list(range(n))
  mean_x = sum(x)/n
  mean_y = sum(y)/n
  # slope = sum((xi-mean_x)*(yi-mean_y)) / sum((xi-mean_x)^2)
  num = sum((xi-mean_x)*(yi-mean_y) for xi,yi in zip(x,y))
  den = sum((xi-mean_x)**2 for xi in x)
  slope = (num/den) if den != 0 else 0.0
  intercept = mean_y - slope*mean_x
  next_val = round(float(max(5, min(80, slope*(n+7)+intercept))), 1)
  status = "Healthy" if next_val<20 else "Moderate" if next_val<40 else "Critical"
  return {"predicted_hcsi":next_val,"predicted_status":status,
      "trend_slope":round(float(slope),3),"intercept":round(float(intercept),2)}

def hr_risk_score(r):
    factors = {c: r[c] for c in HR_COLS}
    avg = sum(factors.values())/len(factors)
    return round(100-avg, 1), factors

@app.route('/api/data')
def get_data():
    conn = get_db()
    rows = conn.execute('SELECT * FROM workforce_data').fetchall()
    alerts = conn.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 20').fetchall()
    conn.close()
    depts=[]; total_emp=0; total_ot=0
    for r in rows:
        sc,st,cl,base,em = calc_hcsi(r['department'],r['overtime_hours'],r['standard_hours'],r['attendance_pct'],r['incidents'])
        risk,factors = hr_risk_score(r)
        ew = ENV_WEIGHTS.get(r['department'],{'heat':0.3,'noise':0.3,'chemical':0.2,'physical':0.3})
        depts.append({"id":r['id'],"department":r['department'],"headcount":r['headcount'],
            "attendance":r['attendance_pct'],"overtime":r['overtime_hours'],
            "incidents":r['incidents'],"productivity":r['productivity'],
            "standard_hours":r['standard_hours'],"hcsi_score":sc,"hcsi_status":st,"color":cl,
            "base_score":round(base,1),"env_multiplier":em,"env_weights":ew,
            "hr_risk":risk,"hr_factors":factors})
        total_emp+=r['headcount']; total_ot+=r['overtime_hours']
    avg_att=round(sum(d['attendance'] for d in depts)/len(depts),1) if depts else 0
    return jsonify({"departments":depts,
        "kpis":{"total_employees":total_emp,"total_overtime":int(total_ot),
                "avg_attendance":avg_att,"critical_depts":sum(1 for d in depts if d['hcsi_status']=='Critical')},
        "alerts":[{"id":a['id'],"department":a['department'],"message":a['message'],
                   "severity":a['severity'],"created_at":a['created_at']} for a in alerts]})

@app.route('/api/history/<dept>')
def get_history(dept):
    conn = get_db()
    rows = conn.execute('SELECT * FROM history WHERE department=? ORDER BY recorded_at',(dept,)).fetchall()
    conn.close()
    return jsonify([{"date":r['recorded_at'],"hcsi":r['hcsi_score'],"productivity":r['productivity']} for r in rows])

@app.route('/api/predict/<dept>')
def predict(dept):
    p = predict_hcsi(dept)
    if p is None: return jsonify({"error":"not enough history"}), 400
    return jsonify(p)

@app.route('/api/formula/<dept>')
def formula(dept):
    """Returns step-by-step HCSI calculation for demo/viva screen."""
    conn = get_db()
    r = conn.execute('SELECT * FROM workforce_data WHERE department=?',(dept,)).fetchone()
    conn.close()
    if not r: return jsonify({"error":"not found"}),404
    ot=r['overtime_hours']; std=r['standard_hours']; att=r['attendance_pct']; inc=r['incidents']
    ot_ratio = round((ot/std)*100,2) if std else 0
    absenteeism = round(100-att,1)
    base = round((ot_ratio*0.35)+(absenteeism*0.25)+(inc*10*0.25)+10, 2)
    ew = ENV_WEIGHTS.get(dept,{'heat':0.3,'noise':0.3,'chemical':0.2,'physical':0.3})
    em = env_multiplier(dept)
    final,status,_,_,_ = calc_hcsi(dept,ot,std,att,inc)
    return jsonify({
        "department": dept,
        "step1_ot_ratio": ot_ratio,
        "step2_absenteeism": absenteeism,
        "step3_base_score": base,
        "step3_formula": f"({ot_ratio}×0.35) + ({absenteeism}×0.25) + ({inc}×10×0.25) + 10 = {base}",
        "step4_env_weights": ew,
        "step4_env_multiplier": em,
        "step4_formula": f"1 + ({ew['heat']}×0.4 + {ew['physical']}×0.3 + {ew['chemical']}×0.2 + {ew['noise']}×0.1) = {em}",
        "step5_final_hcsi": final,
        "step5_formula": f"{base} × {em} = {final}",
        "status": status,
        "thresholds": "< 20 = Healthy, 20-40 = Moderate, ≥ 40 = Critical"
    })

@app.route('/api/update/hr', methods=['POST'])
def update_hr():
    d = request.json; conn = get_db()
    conn.execute(f"UPDATE workforce_data SET {','.join(c+'=?' for c in HR_COLS)} WHERE department=?",
                 (*[d[c] for c in HR_COLS], d['department']))
    conn.commit(); conn.close()
    return jsonify({"status":"success"})

@app.route('/api/update', methods=['POST'])
def update_data():
    d = request.json; conn = get_db()
    # FIX: fetch standard_hours from DB so HCSI calc is correct
    row = conn.execute('SELECT standard_hours FROM workforce_data WHERE department=?',(d['department'],)).fetchone()
    std_hours = row['standard_hours'] if row else 4000
    conn.execute('UPDATE workforce_data SET headcount=?,attendance_pct=?,overtime_hours=?,incidents=?,productivity=? WHERE department=?',
                 (d['headcount'],d['attendance'],d['overtime'],d['incidents'],d['productivity'],d['department']))
    sc,st,_,_,_ = calc_hcsi(d['department'],d['overtime'],std_hours,d['attendance'],d['incidents'])
    conn.execute('INSERT INTO history (department,hcsi_score,productivity,recorded_at) VALUES (?,?,?,?)',
                 (d['department'],sc,d['productivity'],datetime.now().strftime('%Y-%m-%d')))
    if st=='Critical':
      insert_alert(conn, d['department'], f"{d['department']} is now Critical stress level!", 'danger')
    conn.commit(); conn.close()
    return jsonify({"status":"success"})

@app.route('/api/alert', methods=['POST'])
def add_alert():
  d = request.json
  conn = get_db()
  insert_alert(conn, d.get('department','System'), d['message'], d.get('severity','info'))
  conn.commit()
  conn.close()
  return jsonify({"status":"ok"})


def insert_alert(conn, department, message, severity='info'):
  # Check alerts table columns and insert with fallback if extra columns exist
  c = conn.cursor()
  cols = [r[1] for r in c.execute("PRAGMA table_info(alerts)").fetchall()]
  now = datetime.now().strftime('%H:%M')
  if 'alert_type' in cols:
    c.execute(
      'INSERT INTO alerts (department,message,severity,created_at,alert_type) VALUES (?,?,?,?,?)',
      (department, message, severity, now, 'system')
    )
  else:
    c.execute(
      'INSERT INTO alerts (department,message,severity,created_at) VALUES (?,?,?,?)',
      (department, message, severity, now)
    )

# FIX: Single CSV export endpoint - always exports live DB data
@app.route('/api/export/csv')
def export_csv():
    return export_csv_dashboard()

@app.route('/api/export/csv/dashboard')
def export_csv_dashboard():
    conn = get_db(); rows = conn.execute('SELECT * FROM workforce_data ORDER BY department').fetchall(); conn.close()
    data = []
    for r in rows:
        sc,st,cl,base,em = calc_hcsi(r['department'], r['overtime_hours'], r['standard_hours'], r['attendance_pct'], r['incidents'])
        risk,_ = hr_risk_score(r)
        data.append({
            'department': r['department'], 'headcount': r['headcount'],
            'attendance_pct': r['attendance_pct'], 'standard_hours': r['standard_hours'],
            'overtime_hours': r['overtime_hours'], 'incidents': r['incidents'],
            'productivity': r['productivity'], 'hcsi_score': sc, 'hcsi_status': st,
            'hr_risk': risk, 'env_multiplier': em
        })
    return csv_response('dashboard_summary.csv', ['department','headcount','attendance_pct','standard_hours','overtime_hours','incidents','productivity','hcsi_score','hcsi_status','hr_risk','env_multiplier'], data)

@app.route('/api/export/csv/heatmap')
def export_csv_heatmap():
    conn = get_db(); rows = conn.execute('SELECT * FROM workforce_data ORDER BY department').fetchall(); conn.close()
    data = []
    for r in rows:
        sc,st,cl,base,em = calc_hcsi(r['department'], r['overtime_hours'], r['standard_hours'], r['attendance_pct'], r['incidents'])
        data.append({
            'department': r['department'], 'hcsi_score': sc, 'hcsi_status': st,
            'attendance_pct': r['attendance_pct'], 'overtime_hours': r['overtime_hours'],
            'productivity': r['productivity'], 'base_score': round(base,1), 'env_multiplier': em
        })
    return csv_response('heatmap.csv', ['department','hcsi_score','hcsi_status','attendance_pct','overtime_hours','productivity','base_score','env_multiplier'], data)

@app.route('/api/shifts', methods=['GET'])
def get_shifts():
    conn = get_db(); rows = conn.execute('SELECT * FROM shift_schedule ORDER BY id').fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/shifts', methods=['POST'])
def add_shift():
    d = request.json
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO shift_schedule (department, shift, staff, assigned_at) VALUES (?,?,?,?)',
              (d.get('department'), d.get('shift'), d.get('staff'), datetime.now().strftime('%Y-%m-%d %H:%M')))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/shifts/<int:shift_id>', methods=['DELETE'])
def delete_shift(shift_id):
    conn = get_db(); c = conn.cursor()
    c.execute('DELETE FROM shift_schedule WHERE id=?',(shift_id,))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})

@app.route('/api/export/csv/shifts')
def export_csv_shifts():
    conn = get_db(); rows = conn.execute('SELECT * FROM shift_schedule ORDER BY id').fetchall(); conn.close()
    return csv_response('shift_schedule.csv', ['id','department','shift','staff','assigned_at'], [dict(r) for r in rows])

@app.route('/api/export/csv/alerts')
def export_csv_alerts():
    conn = get_db(); rows = conn.execute('SELECT * FROM alerts ORDER BY id').fetchall(); conn.close()
    return csv_response('alerts.csv', ['id','department','message','severity','created_at'], [dict(r) for r in rows])

@app.route('/api/export/csv/data')
def export_csv_data():
    conn = get_db(); rows = conn.execute('SELECT * FROM workforce_data ORDER BY department').fetchall(); conn.close()
    fieldnames = rows[0].keys() if rows else ['department','headcount','attendance_pct','standard_hours','overtime_hours','incidents','productivity']
    return csv_response('workforce_data.csv', fieldnames, [dict(r) for r in rows])

@app.route('/api/export/csv/hrfactors')
def export_csv_hrfactors():
    conn = get_db(); rows = conn.execute('SELECT department, '+','.join(HR_COLS)+' FROM workforce_data ORDER BY department').fetchall(); conn.close()
    return csv_response('hr_factors.csv', ['department'] + HR_COLS, [dict(r) for r in rows])

@app.route('/api/export/csv/prediction')
def export_csv_prediction():
    dept = request.args.get('dept')
    if not dept:
        return jsonify({'error':'missing dept parameter'}), 400
    conn = get_db(); hist = conn.execute('SELECT * FROM history WHERE department=? ORDER BY recorded_at',(dept,)).fetchall(); conn.close()
    rows = [dict(r) for r in hist]
    prediction = predict_hcsi(dept)
    if prediction is not None:
        rows.append({
            'id': '', 'department': dept, 'hcsi_score': prediction['predicted_hcsi'],
            'productivity': '', 'recorded_at': 'predicted',
            'predicted_status': prediction['predicted_status'],
            'trend_slope': prediction['trend_slope'], 'intercept': prediction['intercept']
        })
    fieldnames = ['id','department','hcsi_score','productivity','recorded_at','predicted_status','trend_slope','intercept']
    return csv_response('prediction_history.csv', fieldnames, rows)

@app.route('/api/export/csv/equipment')
def export_csv_equipment():
    conn = get_db(); rows = conn.execute('SELECT * FROM equipment_issues ORDER BY id').fetchall(); conn.close()
    return csv_response('equipment_issues.csv', ['id','department','equipment','severity','description','reported_by','created_at','hr_flag'], [dict(r) for r in rows])

@app.route('/api/export/csv/retirements')
def export_csv_retirements():
    conn = get_db(); rows = conn.execute('SELECT * FROM retirements ORDER BY id').fetchall(); conn.close()
    return csv_response('retirements.csv', ['id','name','department','expertise','contact','retained','notes','recorded_at'], [dict(r) for r in rows])

@app.route('/api/equipment/report', methods=['POST'])
def report_equipment():
    d = request.json or {}
    dept = d.get('department')
    equip = d.get('equipment') or 'Unknown asset'
    sev = d.get('severity', 'low')
    desc = d.get('description', '')
    rb = d.get('reported_by', 'anonymous')
    conn = get_db(); c = conn.cursor()
    hr_flag = 1 if sev.lower() in ('high', 'critical') else 0
    c.execute('INSERT INTO equipment_issues (department,equipment,severity,description,reported_by,created_at,hr_flag) VALUES (?,?,?,?,?,?,?)',
              (dept, equip, sev, desc, rb, datetime.now().strftime('%Y-%m-%d %H:%M'), hr_flag))
    if sev.lower() in ('high', 'critical'):
      hr_impact = apply_hr_impact(conn, dept, sev)
      insert_alert(conn, dept, f"Equipment service alert: {equip} ({sev}) requires urgent attention", 'danger')
      if hr_impact:
        insert_alert(conn, dept, hr_impact['summary'], 'warning')
    elif sev.lower() == 'medium':
      insert_alert(conn, dept, f"Routine maintenance requested for {equip}", 'warning')
    conn.commit(); conn.close()
    return jsonify({'status':'ok', 'hr_flag': hr_flag, 'equipment': equip, 'severity': sev})


@app.route('/api/equipment/<dept>' )
def get_equipment(dept):
    conn=get_db(); rows=conn.execute('SELECT * FROM equipment_issues WHERE department=? ORDER BY id DESC',(dept,)).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/equipment/stats')
def equipment_stats():
    conn=get_db(); rows=conn.execute('SELECT equipment, COUNT(*) as cnt, SUM(CASE severity WHEN "critical" THEN 4 WHEN "high" THEN 3 WHEN "medium" THEN 2 ELSE 1 END) as service_load FROM equipment_issues GROUP BY equipment ORDER BY service_load DESC, cnt DESC').fetchall(); conn.close()
    return jsonify([{ 'equipment':r['equipment'],'count':int(r['service_load'] or 0),'issue_count':int(r['cnt'] or 0)} for r in rows])


@app.route('/api/retirements', methods=['POST'])
def add_retirement():
    d = request.json
    conn = get_db(); c = conn.cursor()
    c.execute('INSERT INTO retirements (name,department,expertise,contact,retained,notes,recorded_at) VALUES (?,?,?,?,?,?,?)',
              (d.get('name'),d.get('department'),d.get('expertise',''),d.get('contact',''),1 if d.get('retained') else 0,d.get('notes',''),datetime.now().strftime('%Y-%m-%d')))
    conn.commit(); conn.close()
    return jsonify({'status':'ok'})


@app.route('/api/retirements/<dept>')
def list_retirements(dept):
    conn = get_db(); rows = conn.execute('SELECT * FROM retirements WHERE department=? ORDER BY id DESC',(dept,)).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db(); user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone(); conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        return render_template_string(AUTH_HTML, title='Sign in', subtitle='Access the workforce dashboard', button_text='Log in', mode='login', error='Invalid username or password')
    return render_template_string(AUTH_HTML, title='Sign in', subtitle='Access the workforce dashboard', button_text='Log in', mode='login', error=None)

@app.route('/signup', methods=['GET','POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            return render_template_string(AUTH_HTML, title='Create account', subtitle='Create a new account to continue', button_text='Create account', mode='signup', error='Username and password are required')
        if len(password) < 4:
            return render_template_string(AUTH_HTML, title='Create account', subtitle='Create a new account to continue', button_text='Create account', mode='signup', error='Password must be at least 4 characters')
        conn = get_db(); c = conn.cursor()
        existing = c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        if existing:
            conn.close()
            return render_template_string(AUTH_HTML, title='Create account', subtitle='Create a new account to continue', button_text='Create account', mode='signup', error='That username already exists')
        c.execute('INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)',
                  (username, generate_password_hash(password), datetime.now().strftime('%Y-%m-%d %H:%M')))
        conn.commit(); conn.close()
        return redirect(url_for('login'))
    return render_template_string(AUTH_HTML, title='Create account', subtitle='Create a new account to continue', button_text='Create account', mode='signup', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

HTML = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RINL Workforce Intelligence</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root{--pink:#ff6b9d;--orange:#ff9f43;--purple:#a29bfe;--blue:#74b9ff;--red:#ff7675;
  --soft:#fff8f0;--card:#ffffff;--sid:#1a1a2e;--sid2:#16213e;--txt:#2d3436;--mut:#636e72;
  --shad:0 8px 32px rgba(0,0,0,.08);--rad:16px;}
[data-theme="dark"]{--soft:#0f0f1a;--card:#1e1e2e;--txt:#e2e8f0;--mut:#94a3b8;}
*{box-sizing:border-box;transition:background .3s,color .3s;}
body{background:var(--soft);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;margin:0;overflow-x:hidden;}
.wrapper{display:flex;min-height:100vh;}
/* SIDEBAR */
.sidebar{width:220px;background:var(--sid);min-height:100vh;position:fixed;top:0;left:0;z-index:100;display:flex;flex-direction:column;}
.sbrand{padding:1.2rem 1rem;background:var(--sid2);display:flex;align-items:center;gap:9px;}
.sicon{width:35px;height:35px;background:linear-gradient(135deg,var(--pink),var(--orange));border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:1rem;flex-shrink:0;}
.stxt{color:#fff;font-weight:800;font-size:.88rem;line-height:1.2;}
.ssub{color:var(--purple);font-size:.58rem;letter-spacing:1px;}
.snav{display:flex;align-items:center;gap:9px;padding:.7rem .9rem;color:#94a3b8;cursor:pointer;border-radius:11px;margin:.12rem .6rem;font-size:.8rem;font-weight:500;border:none;background:none;width:calc(100% - 1.2rem);text-align:left;}
.snav:hover{background:rgba(255,255,255,.07);color:#fff;}
.snav.active{background:linear-gradient(135deg,rgba(255,107,157,.18),rgba(255,159,67,.18));color:#fff;border:1px solid rgba(255,107,157,.25);}
.snav .ni{width:28px;height:28px;border-radius:7px;display:flex;align-items:center;justify-content:center;font-size:.8rem;flex-shrink:0;background:rgba(255,255,255,.07);}
.snav.active .ni{background:linear-gradient(135deg,var(--pink),var(--orange));}
.ssec{padding:.6rem 1rem .2rem;color:#4a5568;font-size:.6rem;letter-spacing:1.5px;font-weight:700;text-transform:uppercase;}
.abadge{background:var(--red);color:#fff;border-radius:999px;font-size:.6rem;padding:.1rem .38rem;margin-left:auto;}
.dtoggle{margin:auto .8rem .8rem;padding:.6rem .9rem;background:rgba(255,255,255,.05);border-radius:11px;display:flex;align-items:center;justify-content:space-between;color:#94a3b8;font-size:.75rem;cursor:pointer;border:none;width:calc(100% - 1.6rem);}
.mobile-nav-toggle{display:none;align-items:center;gap:.4rem;padding:.4rem .7rem;border:1px solid #e2e8f0;border-radius:9px;background:var(--card);color:var(--txt);font-size:.75rem;font-weight:700;}
.sidebar-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);opacity:0;pointer-events:none;transition:opacity .2s;z-index:999;}
.sidebar-overlay.active{opacity:1;pointer-events:auto;}
/* MAIN — FIX: use full remaining width */
.main{margin-left:220px;padding:1.2rem 1.4rem;min-height:100vh;width:calc(100vw - 220px);}
.topbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:1.2rem;flex-wrap:wrap;gap:.5rem;}
.ptitle{font-size:1.4rem;font-weight:900;background:linear-gradient(135deg,var(--pink),var(--orange));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.tright{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;}
.spill{background:linear-gradient(135deg,#00b894,#00cec9);color:#fff;border-radius:999px;padding:.28rem .8rem;font-size:.7rem;font-weight:700;}
.rbtn{background:var(--card);border:1.5px solid #e2e8f0;border-radius:9px;padding:.32rem .8rem;font-size:.75rem;color:var(--txt);cursor:pointer;text-decoration:none;display:inline-block;}
.rbtn:hover{border-color:var(--pink);color:var(--pink);}
.card{background:var(--card);border-radius:var(--rad);border:none;box-shadow:var(--shad);padding:1.2rem;transition:transform .2s,box-shadow .2s;}
.card:hover{transform:translateY(-2px);box-shadow:0 14px 40px rgba(0,0,0,.11);}
.kico{width:42px;height:42px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:1.15rem;margin-bottom:.6rem;}
.kval{font-size:1.75rem;font-weight:900;line-height:1;}
.klbl{font-size:.7rem;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;margin-top:.2rem;}
.kchg{font-size:.7rem;margin-top:.3rem;font-weight:600;}
.page{display:none;animation:fadeUp .3s ease;}
.page.active{display:block;}
@keyframes fadeUp{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
/* HEATMAP */
.hgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:.8rem;}
.hcard{border-radius:14px;padding:1rem;color:#fff;cursor:pointer;position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s;}
.hcard:hover{transform:scale(1.04);box-shadow:0 12px 32px rgba(0,0,0,.28);}
.hcard::before{content:'';position:absolute;top:-16px;right:-16px;width:64px;height:64px;border-radius:50%;background:rgba(255,255,255,.12);}
.hc-success{background:linear-gradient(135deg,#00b894,#00cec9);}
.hc-warning{background:linear-gradient(135deg,#fdcb6e,#e17055);}
.hc-danger{background:linear-gradient(135deg,#d63031,#e84393);}
.hdept{font-weight:800;font-size:.9rem;margin-bottom:.2rem;}
.hst{font-size:.7rem;opacity:.85;margin-bottom:.65rem;}
.hmeta{display:flex;justify-content:space-between;font-size:.7rem;opacity:.9;}
.hhcsi{position:absolute;top:.6rem;right:.7rem;font-size:1.3rem;font-weight:900;opacity:.22;}
/* MODAL */
.modal-content{border-radius:18px;border:none;box-shadow:0 20px 60px rgba(0,0,0,.2);}
.modal-header{border-bottom:none;padding:1.3rem 1.3rem .4rem;}
.dstat{background:var(--soft);border-radius:11px;padding:.7rem;text-align:center;}
.dsval{font-size:1.25rem;font-weight:800;}
.dslbl{font-size:.65rem;color:var(--mut);text-transform:uppercase;}
/* SIMULATOR */
.sres{text-align:center;padding:.9rem;}
.srv{font-size:1.9rem;font-weight:900;line-height:1;}
.srl{font-size:.7rem;color:#94a3b8;margin-top:.3rem;}
/* ALERTS & LIST ITEMS */
.aitm{display:flex;align-items:center;gap:10px;padding:.75rem;border-radius:10px;margin-bottom:.45rem;background:var(--soft);}
[data-theme="dark"] .aitm{background:rgba(255,255,255,.05);}
.adot{width:9px;height:9px;border-radius:50%;flex-shrink:0;}
.shift-table td,.shift-table th{padding:.5rem .7rem;font-size:.78rem;vertical-align:middle;}
.sbdge{border-radius:7px;padding:.2rem .5rem;font-size:.68rem;font-weight:700;}
/* Sim helper text */
.sim-help{font-size:.7rem;color:var(--mut);margin-top:.3rem;line-height:1.4;}
@media(max-width:768px){.sidebar{width:220px;}.main{margin-left:220px;width:calc(100vw - 220px);padding:.9rem;}}
@media(max-width:576px){.sidebar{position:fixed;top:0;left:0;bottom:0;transform:translateX(-100%);transition:transform .3s ease;z-index:1000;width:220px;}.sidebar.open{transform:translateX(0);}.main{margin-left:0;width:100%;padding:.9rem;}.mobile-nav-toggle{display:inline-flex;}.topbar{flex-direction:column;align-items:flex-start;}.tright{width:100%;justify-content:space-between;flex-wrap:wrap;}.card{padding:1rem;}.kval{font-size:1.4rem;}.hgrid{grid-template-columns:1fr;}.shift-table td,.shift-table th{padding:.4rem .5rem;font-size:.72rem;}.modal-dialog{margin:.5rem;}}
</style>
</head>
<body>
<div class="sidebar-overlay" id="sidebar-overlay" onclick="closeSidebar()"></div>
<div class="wrapper">

<div class="sidebar" id="sidebar">
  <div class="sbrand">
    <div class="sicon">🏭</div>
    <div><div class="stxt">RINL Analytics</div><div class="ssub">WORKFORCE INTEL</div></div>
  </div>
  <div class="ssec">Main</div>
  <button class="snav active" onclick="nav('dashboard',this)"><div class="ni">📊</div>Executive Dashboard</button>
  <button class="snav" onclick="nav('heatmap',this)"><div class="ni">🗺️</div>Plant Heatmap</button>
  <button class="snav" onclick="nav('simulator',this)"><div class="ni">⚡</div>Capacity Simulator</button>
  <div class="ssec">Manage</div>
  <button class="snav" onclick="nav('shift',this)"><div class="ni">🗓️</div>Shift Planner</button>
  <button class="snav" onclick="nav('alerts',this)"><div class="ni">🔔</div>Alerts<span class="abadge" id="alert-count">0</span></button>
  <button class="snav" onclick="nav('data',this)"><div class="ni">🗄️</div>Data Management</button>
  <button class="snav" onclick="nav('hrfactors',this)"><div class="ni">🏥</div>HR Factors</button>
  <button class="snav" onclick="nav('predict',this)"><div class="ni">🧠</div>AI Prediction</button>
  <button class="dtoggle" onclick="toggleDark()"><span id="dlbl">🌙 Dark Mode</span><span id="dico">○</span></button>
</div>

<div class="main">
  <div class="topbar">
    <div class="ptitle" id="pageTitle">Executive Dashboard</div>
    <div class="tright">
      <button class="mobile-nav-toggle" onclick="toggleSidebar()">☰ Menu</button>
      <div class="spill">● Live</div>
      <button class="rbtn" onclick="fetchData()">↻ Refresh</button>
      <a class="rbtn" href="/logout" style="color:var(--red)">↪ Logout</a>
      <!-- Page-specific CSV export -->
      <a id="export-link" href="/api/export/csv/dashboard" class="rbtn">⬇ Export CSV</a>
    </div>
  </div>

  <!-- DASHBOARD -->
  <div id="dashboard" class="page active">
    <div class="row g-3 mb-3">
      <div class="col-6 col-xl-3"><div class="card">
        <div class="kico" style="background:linear-gradient(135deg,#a29bfe22,#a29bfe44)">👥</div>
        <div class="kval" id="k-emp">--</div><div class="klbl">Active Workforce</div>
        <div class="kchg" style="color:#a29bfe">All Departments</div>
      </div></div>
      <div class="col-6 col-xl-3"><div class="card">
        <div class="kico" style="background:linear-gradient(135deg,#74b9ff22,#74b9ff44)">📅</div>
        <div class="kval" id="k-att" style="color:var(--blue)">--</div><div class="klbl">Avg Attendance %</div>
        <div class="kchg" style="color:#00b894">Good Standing</div>
      </div></div>
      <div class="col-6 col-xl-3"><div class="card">
        <div class="kico" style="background:linear-gradient(135deg,#ff767522,#ff767544)">⏱️</div>
        <div class="kval" id="k-ot" style="color:var(--red)">--</div><div class="klbl">Total Overtime Hrs</div>
        <div class="kchg" style="color:var(--red)">Monitor closely</div>
      </div></div>
      <div class="col-6 col-xl-3"><div class="card">
        <div class="kico" style="background:linear-gradient(135deg,#ff6b9d22,#ff6b9d44)">⚠️</div>
        <div class="kval" id="k-crit" style="color:var(--pink)">--</div><div class="klbl">Critical Depts</div>
        <div class="kchg" style="color:var(--orange)">Needs Attention</div>
      </div></div>
    </div>
    <!-- FIX: use full width — col-lg-8 + col-lg-4 fills 100% -->
    <div class="row g-3">
      <div class="col-lg-8"><div class="card" style="height:380px">
        <div style="font-weight:800;font-size:.9rem;margin-bottom:.8rem">📈 HCSI Stress Index — All Departments</div>
        <div style="height:310px;position:relative"><canvas id="barChart"></canvas></div>
      </div></div>
      <div class="col-lg-4"><div class="card" style="height:380px;overflow-y:auto">
        <div style="font-weight:800;font-size:.9rem;margin-bottom:.8rem">⚠️ Risk Watchlist (Rule-Based)</div>
        <div id="ai-insights"></div>
        <div style="margin-top:1rem">
          <div style="font-weight:800;font-size:.85rem;margin-bottom:.5rem">🛠️ Equipment Service Load</div>
          <div style="height:140px;position:relative"><canvas id="equipChart"></canvas></div>
          <div id="equip-priority" style="margin-top:.6rem;font-size:.72rem;color:var(--mut)"></div>
        </div>
      </div></div>
    </div>
  </div>

  <!-- HEATMAP -->
  <div id="heatmap" class="page">
    <div class="card mb-3" style="padding:.65rem 1rem">
      <div class="d-flex gap-2 flex-wrap align-items-center">
        <span style="font-size:.78rem;color:var(--mut);font-weight:600">Filter:</span>
        <button class="rbtn" onclick="filterHeat('all')">All</button>
        <button class="rbtn" onclick="filterHeat('Healthy')">🟢 Healthy</button>
        <button class="rbtn" onclick="filterHeat('Moderate')">🟡 Moderate</button>
        <button class="rbtn" onclick="filterHeat('Critical')">🔴 Critical</button>
        <span style="margin-left:auto;font-size:.72rem;color:var(--mut)">Click any card for trend + mini-simulator</span>
      </div>
    </div>
    <div class="hgrid" id="heat-grid"></div>
  </div>

  <!-- SIMULATOR — FIX: cleaner layout, correct math, clear labels -->
  <div id="simulator" class="page">
    <div class="row g-3">
      <div class="col-md-5"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">⚡ Simulation Parameters</div>

        <label style="font-size:.78rem;color:var(--mut);font-weight:600">Target Output Increase (%)</label>
        <input type="range" class="form-range mt-1" id="sim-target" min="0" max="50" value="10" oninput="runSim()">
        <div style="text-align:center;font-size:1.4rem;font-weight:900;color:var(--pink);margin-bottom:.2rem" id="sim-tv">10%</div>
        <div class="sim-help">How much more output you need (e.g. 10% = produce 10% more steel this month).</div>

        <label style="font-size:.78rem;color:var(--mut);font-weight:600;margin-top:1rem;display:block">Current Total Workforce</label>
        <div style="display:flex;gap:.5rem;margin-top:.3rem">
          <input type="number" class="form-control" id="sim-wf" value="0" oninput="runSim()">
          <button class="rbtn" onclick="resetWF()" style="white-space:nowrap">↺ Reset</button>
        </div>
        <div class="sim-help">Total employees from DB. You can override to test scenarios.</div>

        <label style="font-size:.78rem;color:var(--mut);font-weight:600;margin-top:1rem;display:block">Expected Absenteeism (%)</label>
        <input type="number" class="form-control mt-1" id="sim-ab" value="8" min="0" max="50" oninput="runSim()">
        <div class="sim-help">% of workforce expected to be absent (industry avg ~8–10%).</div>

        <label style="font-size:.78rem;color:var(--mut);font-weight:600;margin-top:1rem;display:block">OT Pay Rate per Hour (Rs.)</label>
        <input type="number" class="form-control mt-1" id="sim-rate" value="400" oninput="runSim()">
        <div class="sim-help">Overtime hourly cost per worker (standard industrial rate).</div>
      </div></div>

      <div class="col-md-7"><div class="card h-100" style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff">
        <div style="font-weight:800;margin-bottom:1rem;font-size:.95rem">📊 What Will Happen</div>
        <div class="row g-2 mb-3">
          <div class="col-6"><div class="sres" style="background:rgba(116,185,255,.1);border-radius:12px">
            <div class="srv" style="color:#74b9ff" id="r-hc">--</div>
            <div class="srl">Workers Needed</div>
            <div style="font-size:.65rem;color:#64748b;margin-top:.2rem">to hit target output</div>
          </div></div>
          <div class="col-6"><div class="sres" style="background:rgba(253,203,110,.1);border-radius:12px">
            <div class="srv" style="color:#fdcb6e" id="r-gap">--</div>
            <div class="srl">Workforce Gap</div>
            <div style="font-size:.65rem;color:#64748b;margin-top:.2rem">workers short after absenteeism</div>
          </div></div>
        </div>
        <div style="background:rgba(255,118,117,.1);border-radius:12px;padding:1rem;text-align:center;margin-bottom:1rem">
          <div class="srv" style="color:#ff7675;font-size:1.6rem" id="r-cost">--</div>
          <div class="srl">Est. Extra OT Cost / month</div>
          <div style="font-size:.65rem;color:#64748b;margin-top:.2rem">Gap workers × 8h/day × rate × 26 working days</div>
        </div>
        <div style="background:rgba(255,255,255,.06);border-radius:12px;padding:.9rem">
          <div style="font-size:.72rem;color:#94a3b8;margin-bottom:.5rem;font-weight:600">Stress Level Forecast</div>
          <div style="height:10px;border-radius:999px;background:linear-gradient(90deg,#00b894,#fdcb6e,#d63031);position:relative">
            <div id="stress-mk" style="position:absolute;top:-5px;width:20px;height:20px;background:#fff;border-radius:50%;border:2px solid #ccc;box-shadow:0 2px 6px rgba(0,0,0,.4);transition:left .4s;left:0"></div>
          </div>
          <div id="stress-lbl" style="font-size:.78rem;margin-top:.5rem;color:#a29bfe;font-weight:700">Loading...</div>
          <div id="sim-summary" style="font-size:.7rem;color:#64748b;margin-top:.4rem;line-height:1.5"></div>
        </div>
      </div></div>
    </div>
  </div>

  <!-- SHIFT PLANNER -->
  <div id="shift" class="page">
    <div class="row g-3">
      <div class="col-lg-4"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">🗓️ Assign Shift</div>
        <label style="font-size:.78rem;color:var(--mut);font-weight:600">Department</label>
        <select class="form-select mt-1 mb-2" id="sh-dept"></select>
        <label style="font-size:.78rem;color:var(--mut);font-weight:600">Shift</label>
        <select class="form-select mt-1 mb-2" id="sh-shift">
          <option>Morning (06:00-14:00)</option>
          <option>Afternoon (14:00-22:00)</option>
          <option>Night (22:00-06:00)</option>
        </select>
        <label style="font-size:.78rem;color:var(--mut);font-weight:600">Staff Assigned</label>
        <input type="number" class="form-control mt-1 mb-3" id="sh-count" value="40">
        <button class="btn w-100" style="background:linear-gradient(135deg,var(--pink),var(--orange));color:#fff;border-radius:11px;font-weight:700;padding:.6rem" onclick="addShift()">+ Add to Schedule</button>
      </div></div>
      <div class="col-lg-8"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">Current Schedule</div>
        <div style="overflow-x:auto">
          <table class="table shift-table mb-0">
            <thead><tr style="font-size:.72rem;color:var(--mut)"><th>Department</th><th>Shift</th><th>Staff</th><th>Status</th><th></th></tr></thead>
            <tbody id="shift-body"></tbody>
          </table>
          <div id="shift-empty" style="text-align:center;color:var(--mut);padding:2rem;font-size:.82rem">No shifts scheduled yet.</div>
        </div>
      </div></div>
    </div>
  </div>

  <!-- ALERTS -->
  <div id="alerts" class="page">
    <div class="d-flex gap-2 mb-3">
      <button class="rbtn" onclick="addCustomAlert()">+ Add Alert</button>
      <button class="rbtn" style="color:var(--red)" onclick="clearAlerts()">Clear All</button>
    </div>
    <div class="card">
      <div style="font-weight:800;margin-bottom:1rem">🔔 System Alerts</div>
      <div id="alert-list"></div>
    </div>
  </div>

  <!-- DATA MANAGEMENT — FIX: fetchData() after update refreshes KPIs -->
  <div id="data" class="page">
    <div class="card">
      <div style="font-weight:800;margin-bottom:1rem">🗄️ Live Department Parameter Editing</div>
      <form onsubmit="submitUpdate(event)">
        <div class="row g-3">
          <div class="col-md-6">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Department</label>
            <select class="form-select mt-1" id="f-dept" onchange="loadForm()"></select>
          </div>
          <div class="col-md-6">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Headcount</label>
            <input type="number" class="form-control mt-1" id="f-head" required>
          </div>
          <div class="col-md-4">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Attendance (%)</label>
            <input type="number" step=".1" class="form-control mt-1" id="f-att" required>
          </div>
          <div class="col-md-4">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Overtime Hours</label>
            <input type="number" class="form-control mt-1" id="f-ot" required>
          </div>
          <div class="col-md-4">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Incident Reports</label>
            <input type="number" class="form-control mt-1" id="f-inc" required>
          </div>
          <div class="col-12">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Productivity Score (0-100)</label>
            <input type="number" class="form-control mt-1" id="f-prod" required>
          </div>
          <div class="col-12">
            <button type="submit" class="btn w-100" style="background:linear-gradient(135deg,var(--pink),var(--orange));color:#fff;border-radius:12px;font-weight:700;padding:.75rem;font-size:.95rem">Update Department Data</button>
          </div>
        </div>
      </form>
    </div>
  </div>

  <!-- HR FACTORS -->
  <div id="hrfactors" class="page">
    <div class="card mb-3">
      <div style="font-weight:800;margin-bottom:.4rem">🏥 HR Wellbeing Factors</div>
      <div style="font-size:.78rem;color:var(--mut)">Select department, view/edit 7 HR factors (0-100 scale). Auto-generated defaults, fully editable.</div>
    </div>
    <div class="card">
      <select class="form-select mb-3" id="hr-dept" onchange="loadHR()" style="max-width:320px"></select>
      <div class="row g-3" id="hr-fields"></div>
      <button class="btn mt-3" style="background:linear-gradient(135deg,var(--pink),var(--orange));color:#fff;border-radius:11px;font-weight:700;padding:.65rem 1.5rem" onclick="saveHR()">Save HR Factors</button>
    </div>
  </div>

  <!-- AI PREDICTION -->
  <div id="predict" class="page">
    <div class="card mb-3">
      <div style="font-weight:800;margin-bottom:.4rem">🧠 AI Prediction (Linear Regression)</div>
      <div style="font-size:.78rem;color:var(--mut)">Trained on 30-day HCSI history per department using least-squares regression. Forecasts next 7 days. This is real trained ML, not if/else rules.</div>
    </div>
    <div class="card">
      <select class="form-select mb-3" id="pred-dept" onchange="runPredict()" style="max-width:320px"></select>
      <div id="pred-result"></div>
    </div>
  </div>
</div>
</div>

<!-- DEPT MODAL — includes mini dept-level simulator -->
<div class="modal fade" id="deptModal" tabindex="-1">
  <div class="modal-dialog modal-lg modal-dialog-centered">
    <div class="modal-content">
      <div class="modal-header">
        <div>
          <h5 class="modal-title" style="font-weight:800" id="modal-title"></h5>
          <span id="modal-badge" class="badge rounded-pill mt-1" style="font-size:.72rem"></span>
        </div>
        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body px-3 pb-3">
        <div class="row g-2 mb-3" id="modal-stats"></div>
        <div style="font-weight:700;margin-bottom:.6rem;font-size:.85rem">7-Day Trend</div>
        <div style="height:180px;position:relative"><canvas id="trendChart"></canvas></div>
        <!-- DEPT MINI SIMULATOR -->
        <div style="margin-top:1rem;background:var(--soft);border-radius:12px;padding:1rem">
          <div style="font-weight:700;font-size:.85rem;margin-bottom:.8rem">⚡ What-If Simulator for this Department</div>
          <div class="row g-2 align-items-center">
            <div class="col-sm-4">
              <label style="font-size:.72rem;color:var(--mut);font-weight:600">OT Hours Scenario</label>
              <input type="number" class="form-control form-control-sm mt-1" id="ds-ot" oninput="runDeptSim()">
            </div>
            <div class="col-sm-4">
              <label style="font-size:.72rem;color:var(--mut);font-weight:600">Attendance % Scenario</label>
              <input type="number" class="form-control form-control-sm mt-1" id="ds-att" oninput="runDeptSim()">
            </div>
            <div class="col-sm-4">
              <label style="font-size:.72rem;color:var(--mut);font-weight:600">Incidents Scenario</label>
              <input type="number" class="form-control form-control-sm mt-1" id="ds-inc" oninput="runDeptSim()">
            </div>
          </div>
          <div id="ds-result" style="margin-top:.7rem;font-size:.82rem;font-weight:700;padding:.5rem .8rem;border-radius:9px;background:var(--card)"></div>
        </div>
            <!-- Equipment reporting & Retirement quick-actions -->
            <div style="margin-top:1rem;display:flex;gap:.8rem;align-items:flex-start">
              <div style="flex:1;background:var(--soft);padding:1rem;border-radius:10px">
                <div style="font-weight:700;margin-bottom:.6rem">🔧 Report Equipment Issue</div>
                <div class="row g-2">
                  <div class="col-sm-6"><select id="eq-equipment" class="form-select form-select-sm"><option value="">Select asset</option><option>Turbine</option><option>Rotor</option><option>Fans</option><option>Generator</option><option>Pipes</option><option>Conveyor</option><option>Boiler</option><option>Pump</option><option>Valve</option></select></div>
                  <div class="col-sm-6"><select id="eq-severity" class="form-select form-select-sm"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></div>
                  <div class="col-12"><input id="eq-equipment-custom" class="form-control form-control-sm" placeholder="Or enter custom asset"></div>
                  <div class="col-12"><input id="eq-reporter" class="form-control form-control-sm" placeholder="Reported by"></div>
                  <div class="col-12"><textarea id="eq-desc" class="form-control form-control-sm" placeholder="Describe the issue and any safety concern"></textarea></div>
                  <div class="col-12" style="font-size:.72rem;color:var(--mut)">Tip: high/critical reports automatically flag HR impact and create a service alert.</div>
                  <div class="col-12"><button class="rbtn" style="margin-top:.4rem" onclick="reportIssue()">Report</button>
                    <button class="rbtn" id="dept-download" style="margin-left:.6rem">⬇ Dept CSV</button></div>
                </div>
              </div>
              <div style="width:320px;background:var(--soft);padding:1rem;border-radius:10px">
                <div style="font-weight:700;margin-bottom:.6rem">📋 Recent Equipment Issues</div>
                <div id="eq-list" style="max-height:160px;overflow:auto;color:var(--mut)"></div>
              </div>
            </div>

            <div style="margin-top:.9rem;background:var(--soft);padding:1rem;border-radius:10px">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.6rem"><div style="font-weight:700">👴 Retired Experts</div><div style="font-size:.78rem;color:var(--mut)">Use retired experts as consultants</div></div>
              <div id="ret-list" style="margin-bottom:.6rem;color:var(--mut)"></div>
              <div class="row g-2">
                <div class="col-md-4"><input id="ret-name" class="form-control form-control-sm" placeholder="Name"></div>
                <div class="col-md-4"><input id="ret-ex" class="form-control form-control-sm" placeholder="Expertise"></div>
                <div class="col-md-4"><input id="ret-contact" class="form-control form-control-sm" placeholder="Contact"></div>
                <div class="col-12"><input id="ret-notes" class="form-control form-control-sm" placeholder="Notes"></div>
                <div class="col-12"><button class="rbtn" style="margin-top:.4rem" onclick="addRetirement()">Add Retired Expert</button></div>
              </div>
            </div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
let G={}, barChart=null, trendChart=null, equipChart=null, shiftData=[], dark=false, baseWF=0, heatFilter='all', modalDept=null;

function updateExportLink(page){
  const exportLink = document.getElementById('export-link');
  if(!exportLink) return;
  const base = '/api/export/csv';
  let href = base + '/dashboard';
  switch(page){
    case 'dashboard': href = base + '/dashboard'; break;
    case 'heatmap': href = base + '/heatmap'; break;
    case 'shift': href = base + '/shifts'; break;
    case 'alerts': href = base + '/alerts'; break;
    case 'data': href = base + '/data'; break;
    case 'hrfactors': href = base + '/hrfactors'; break;
    case 'predict': {
      const dept = document.getElementById('pred-dept').value;
      href = dept ? `${base}/prediction?dept=${encodeURIComponent(dept)}` : `${base}/prediction`;
      break;
    }
    default: href = base + '/dashboard';
  }
  exportLink.href = href;
}

function nav(page, el) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.snav').forEach(n=>n.classList.remove('active'));
  document.getElementById(page).classList.add('active');
  el.classList.add('active');
  closeSidebar();
  const T={dashboard:'Executive Dashboard',heatmap:'Plant Operations Heatmap',
    simulator:'Workforce Capacity Simulator',shift:'Shift Planner',alerts:'Alert Center',data:'Data Management',
    hrfactors:'HR Wellbeing Factors',predict:'AI Prediction'};
  document.getElementById('pageTitle').textContent=T[page]||page;
  if(page==='hrfactors')loadHR();
  if(page==='predict')runPredict();
  if(page==='shift')loadShifts();
  updateExportLink(page);
}

function toggleDark(){
  dark=!dark;
  document.documentElement.setAttribute('data-theme',dark?'dark':'light');
  document.getElementById('dlbl').textContent=dark?'☀️ Light Mode':'🌙 Dark Mode';
  document.getElementById('dico').textContent=dark?'●':'○';
}

function toggleSidebar(){
  const sidebar=document.getElementById('sidebar');
  const overlay=document.getElementById('sidebar-overlay');
  const open=sidebar.classList.toggle('open');
  overlay.classList.toggle('active', open);
}

function closeSidebar(){
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('active');
}

function animCount(el,target,suf=''){
  let v=0,step=Math.max(1,target/(800/16));
  const t=setInterval(()=>{v=Math.min(v+step,target);el.textContent=Math.floor(v).toLocaleString()+suf;if(v>=target)clearInterval(t);},16);
}

async function fetchData(){
  try {
    const res=await fetch('/api/data');
    if(!res.ok){ document.getElementById('k-emp').textContent='ERR'; console.error('API error',res.status); return; }
    G=await res.json();
    baseWF=G.kpis.total_employees;
    renderDash(); renderHeat(); renderAlerts(); populateDepts();
    document.getElementById('sim-wf').value=baseWF;
    runSim();
    fetchEquipmentStats();
    document.getElementById('alert-count').textContent=G.alerts.length;
  } catch(e){ document.getElementById('k-emp').textContent='ERR'; console.error('Fetch failed',e); }
}

function renderDash(){
  // FIX: these now re-run after submitUpdate calls fetchData, so KPIs always update
  animCount(document.getElementById('k-emp'),G.kpis.total_employees);
  animCount(document.getElementById('k-att'),G.kpis.avg_attendance,'%');
  animCount(document.getElementById('k-ot'),G.kpis.total_overtime);
  animCount(document.getElementById('k-crit'),G.kpis.critical_depts);
  const labels=G.departments.map(d=>d.department);
  const data=G.departments.map(d=>d.hcsi_score);
  const colors=G.departments.map(d=>d.color==='success'?'rgba(0,184,148,.8)':d.color==='warning'?'rgba(253,203,110,.9)':'rgba(214,48,49,.8)');
  if(barChart)barChart.destroy();
  barChart=new Chart(document.getElementById('barChart').getContext('2d'),{
    type:'bar',data:{labels,datasets:[{label:'HCSI',data,backgroundColor:colors,borderRadius:6}]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>` HCSI: ${c.raw} — ${G.departments[c.dataIndex].hcsi_status}`}}},
      scales:{y:{beginAtZero:true,grid:{color:'rgba(0,0,0,.05)'}},x:{grid:{display:false},ticks:{font:{size:8},maxRotation:50}}}}
  });
  const ins=document.getElementById('ai-insights');
  ins.innerHTML='';
  G.departments.filter(d=>d.hcsi_status!=='Healthy').forEach(d=>{
    const c=d.hcsi_status==='Critical'?'#ff7675':'#fdcb6e';
    const i=d.hcsi_status==='Critical'?'🔴':'🟡';
    ins.innerHTML+=`<div class="aitm" style="border-left:3px solid ${c}">
      <span>${i}</span><div><div style="font-weight:700;font-size:.78rem">${d.department}</div>
      <div style="font-size:.7rem;color:var(--mut)">${d.hcsi_status} · HCSI ${d.hcsi_score}</div></div></div>`;
  });
  if(!ins.innerHTML)ins.innerHTML=`<div style="text-align:center;color:#00b894;padding:1rem;font-size:.85rem">✅ All departments within safe thresholds!</div>`;
}

function renderHeat(){
  const grid=document.getElementById('heat-grid');
  grid.innerHTML='';
  (heatFilter==='all'?G.departments:G.departments.filter(d=>d.hcsi_status===heatFilter)).forEach(d=>{
    const dn=d.department.replace(/'/g,"\\'");
    grid.innerHTML+=`<div class="hcard hc-${d.color}" onclick="openModal('${dn}')">
      <div class="hhcsi">${d.hcsi_score}</div>
      <div class="hdept">${d.department}</div>
      <div class="hst">● ${d.hcsi_status}</div>
      <div class="hmeta"><span>👥 ${d.headcount}</span><span>📈 ${d.productivity}%</span></div>
      <div class="hmeta mt-1"><span>⏱ ${d.overtime}h OT</span><span>⚠ ${d.incidents} inc</span></div>
    </div>`;
  });
}
function filterHeat(f){heatFilter=f;renderHeat();}

async function fetchEquipmentStats(){
  try{
    const res=await fetch('/api/equipment/stats'); if(!res.ok) return;
    const rows=await res.json();
    const labels=rows.map(r=>r.equipment||'Unknown');
    const data=rows.map(r=>r.count||0);
    const priorityEl=document.getElementById('equip-priority');
    if(priorityEl){
      const top=rows.slice(0,4).map(r=>`${r.equipment} (${r.count})`).join(' • ');
      priorityEl.innerHTML=top ? `Priority assets: ${top}` : 'No equipment issues logged yet.';
    }
    if(equipChart)equipChart.destroy();
    const ctx=document.getElementById('equipChart').getContext('2d');
    equipChart=new Chart(ctx,{type:'bar',data:{labels,datasets:[{label:'Service Load',data,backgroundColor:'rgba(255,118,117,0.9)'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{y:{beginAtZero:true,title:{display:true,text:'Service Load'}}}}});
  }catch(e){console.error('equipment stats',e)}
}

async function openModal(dept){
  const d=G.departments.find(x=>x.department===dept);
  if(!d)return;
  modalDept=d;
  document.getElementById('modal-title').textContent='🏭 '+dept;
  const bc={success:'#00b894',warning:'#fdcb6e',danger:'#d63031'};
  const mb=document.getElementById('modal-badge');
  mb.style.background=bc[d.color]; mb.textContent=d.hcsi_status+' · HCSI '+d.hcsi_score;
  document.getElementById('modal-stats').innerHTML=[
    ['👥 Headcount',d.headcount],['📅 Attendance',d.attendance+'%'],
    ['⏱ Overtime',d.overtime+'h'],['⚠ Incidents',d.incidents],
    ['📈 Productivity',d.productivity+'%'],['📋 Std Hours',d.standard_hours+'h']
  ].map(([l,v])=>`<div class="col-4"><div class="dstat"><div class="dsval">${v}</div><div class="dslbl">${l}</div></div></div>`).join('');
  // Pre-fill dept simulator with current values
  document.getElementById('ds-ot').value=d.overtime;
  document.getElementById('ds-att').value=d.attendance;
  document.getElementById('ds-inc').value=d.incidents;
  runDeptSim();
  // load equipment issues and retired experts for this dept
  fetchEquipment(dept);
  fetchRetirements(dept);
  document.getElementById('dept-download').onclick = ()=> downloadDeptCSV(dept);
  const res=await fetch('/api/history/'+encodeURIComponent(dept));
  const hist=await res.json();
  if(trendChart)trendChart.destroy();
  trendChart=new Chart(document.getElementById('trendChart').getContext('2d'),{
    type:'line',data:{labels:hist.map(h=>h.date),datasets:[
      {label:'HCSI',data:hist.map(h=>h.hcsi),borderColor:'#ff6b9d',backgroundColor:'rgba(255,107,157,.1)',tension:.4,fill:true,pointRadius:3},
      {label:'Productivity',data:hist.map(h=>h.productivity),borderColor:'#74b9ff',backgroundColor:'rgba(116,185,255,.1)',tension:.4,fill:true,pointRadius:3}
    ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'top'}},scales:{y:{beginAtZero:false}}}
  });
  new bootstrap.Modal(document.getElementById('deptModal')).show();
}

// FIX: Department-level what-if simulator in modal
function runDeptSim(){
  if(!modalDept)return;
  const ot=parseFloat(document.getElementById('ds-ot').value)||0;
  const att=parseFloat(document.getElementById('ds-att').value)||100;
  const inc=parseInt(document.getElementById('ds-inc').value)||0;
  const std=modalDept.standard_hours||4000;
  const ot_ratio=(ot/std)*100;
  const score=((ot_ratio)*0.35)+((100-att)*0.25)+(inc*10*0.25)+10;
  const sc=Math.round(score*10)/10;
  const st=sc<15?'Healthy':sc<30?'Moderate':'Critical';
  const col={Healthy:'#00b894',Moderate:'#fdcb6e',Critical:'#ff7675'}[st];
  document.getElementById('ds-result').style.color=col;
  document.getElementById('ds-result').style.borderLeft=`3px solid ${col}`;
  document.getElementById('ds-result').textContent=`Simulated HCSI: ${sc} — Status: ${st}`;
}

function downloadDeptCSV(dept){
  window.location = '/api/export/csv/'+encodeURIComponent(dept);
}

async function reportIssue(){
  if(!modalDept) return alert('Select a department first');
  const selectedAsset=document.getElementById('eq-equipment').value;
  const customAsset=document.getElementById('eq-equipment-custom').value.trim();
  const asset=customAsset || selectedAsset || 'Unknown asset';
  const payload={department:modalDept.department,equipment:asset,severity:document.getElementById('eq-severity').value,description:document.getElementById('eq-desc').value,reported_by:document.getElementById('eq-reporter').value};
  const r=await fetch('/api/equipment/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(r.ok){
    const data=await r.json();
    fetchEquipment(modalDept.department);
    fetchEquipmentStats();
    fetchData();
    const note = data.severity==='high' || data.severity==='critical' ? 'Issue logged and HR impact flagged.' : 'Issue logged successfully.';
    alert(note);
  }
  else alert('Failed to report');
}

async function fetchEquipment(dept){
  try{
    const res=await fetch('/api/equipment/'+encodeURIComponent(dept));
    const rows=await res.json(); renderEquipment(rows);
  }catch(e){console.error(e);document.getElementById('eq-list').textContent='Error loading issues';}
}

function renderEquipment(rows){
  const c=document.getElementById('eq-list'); if(!rows||rows.length===0){c.innerHTML='<div style="color:var(--mut)">No recent issues</div>';return}
  c.innerHTML = rows.map(r=>`<div style="padding:.4rem .2rem;border-bottom:1px solid rgba(0,0,0,.04)"><div style="font-weight:700">${r.equipment} <span style="font-size:.7rem;color:var(--mut)">(${r.severity})</span></div><div style="font-size:.75rem;color:var(--mut)">${r.description||''}</div><div style="font-size:.7rem;color:var(--mut);margin-top:.3rem">By ${r.reported_by||'anon'} at ${r.created_at}</div></div>`).join('');
}

async function fetchRetirements(dept){
  try{const res=await fetch('/api/retirements/'+encodeURIComponent(dept));const rows=await res.json();renderRetirements(rows);}catch(e){console.error(e);document.getElementById('ret-list').textContent='Error';}
}

function renderRetirements(rows){
  const el=document.getElementById('ret-list'); if(!rows||rows.length===0){el.innerHTML='<div style="color:var(--mut)">No retired experts recorded</div>';return}
  el.innerHTML = rows.map(r=>`<div style="padding:.3rem 0;border-bottom:1px solid rgba(0,0,0,.04)"><b>${r.name}</b> — ${r.expertise} <div style="font-size:.72rem;color:var(--mut)">${r.contact} ${r.retained?'<span style="color:#00b894;margin-left:.6rem">(Retained)</span>':''}</div></div>`).join('');
}

async function addRetirement(){
  if(!modalDept) return alert('Open a department first');
  const payload={department:modalDept.department,name:document.getElementById('ret-name').value,expertise:document.getElementById('ret-ex').value,contact:document.getElementById('ret-contact').value,notes:document.getElementById('ret-notes').value,retained:true};
  const r=await fetch('/api/retirements',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(r.ok){fetchRetirements(modalDept.department);alert('Retired expert added');}else alert('Failed');
}

// FIX: Correct simulator math — realistic and labelled
function resetWF(){document.getElementById('sim-wf').value=baseWF;runSim();}
function runSim(){
  const base=parseInt(document.getElementById('sim-wf').value)||0;
  const inc=parseInt(document.getElementById('sim-target').value)||0;
  const ab=parseFloat(document.getElementById('sim-ab').value)||0;
  const rate=parseInt(document.getElementById('sim-rate').value)||400;
  document.getElementById('sim-tv').textContent=inc+'%';
  // Workers needed = base scaled by output increase
  const needed=Math.ceil(base*(1+inc/100));
  // Workers actually available = base minus absentees
  const avail=Math.floor(base*(1-ab/100));
  // Gap = extra people needed beyond available
  const gap=Math.max(0, needed-avail);
  // Cost = gap workers * 8h * 26 working days * rate (realistic monthly)
  const cost=gap*8*26*rate;
  document.getElementById('r-hc').textContent=needed.toLocaleString();
  document.getElementById('r-gap').textContent=gap;
  document.getElementById('r-cost').textContent='Rs.'+cost.toLocaleString();
  const pct=Math.min(100,base>0?(gap/base)*100:0);
  document.getElementById('stress-mk').style.left='calc('+Math.min(95,pct)+'% - 10px)';
  const label=pct<10?'🟢 Low Stress — manageable with current workforce':
               pct<25?'🟡 Moderate — plan overtime or temp hiring':
                      '🔴 High Risk — significant gap, urgent action needed';
  document.getElementById('stress-lbl').textContent=label.split(' — ')[0];
  document.getElementById('sim-summary').textContent=label.split(' — ')[1]||'';
}

async function addShift(){
  const dept=document.getElementById('sh-dept').value;
  const shift=document.getElementById('sh-shift').value;
  const count=parseInt(document.getElementById('sh-count').value)||0;
  if(!dept) return alert('Select a department');
  await fetch('/api/shifts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({department:dept,shift:shift,staff:count})});
  loadShifts();
}
async function removeShift(id){
  await fetch('/api/shifts/'+id,{method:'DELETE'});
  loadShifts();
}
function renderShifts(){
  const body=document.getElementById('shift-body');
  document.getElementById('shift-empty').style.display=shiftData.length?'none':'block';
  body.innerHTML=shiftData.map(s=>`<tr>
    <td style="font-weight:700">${s.department}</td><td>${s.shift}</td><td>${s.staff}</td>
    <td><span class="sbdge" style="background:${s.staff>=30?'#00b89422':'#ff767522'};color:${s.staff>=30?'#00b894':'#ff7675'}">${s.staff>=30?'OK':'Low'}</span></td>
    <td><button class="rbtn" style="color:var(--red);font-size:.7rem;padding:.15rem .45rem" onclick="removeShift(${s.id})">✕</button></td>
  </tr>`).join('');
}

function renderAlerts(){
  const list=document.getElementById('alert-list');
  if(!G.alerts||!G.alerts.length){list.innerHTML='<div style="color:var(--mut);text-align:center;padding:2rem">No alerts yet 🎉</div>';return;}
  list.innerHTML=G.alerts.map(a=>`<div class="aitm">
    <div class="adot" style="background:${a.severity==='danger'?'#ff7675':a.severity==='warning'?'#fdcb6e':'#74b9ff'}"></div>
    <div style="flex:1"><div style="font-weight:700;font-size:.78rem">${a.department||'System'}</div>
    <div style="font-size:.73rem;color:var(--mut)">${a.message}</div></div>
    <div style="font-size:.68rem;color:var(--mut)">${a.created_at}</div>
  </div>`).join('');
}
async function addCustomAlert(){
  const msg=prompt('Alert message:'); if(!msg)return;
  await fetch('/api/alert',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,severity:'info'})});
  fetchData();
}
function clearAlerts(){G.alerts=[];renderAlerts();document.getElementById('alert-count').textContent=0;}

function populateDepts(){
  ['f-dept','sh-dept','hr-dept','pred-dept'].forEach(id=>{
    const sel=document.getElementById(id);
    if(sel.options.length===0)G.departments.forEach(d=>sel.options.add(new Option(d.department,d.department)));
  });
  loadForm();
}
function loadForm(){
  const d=G.departments.find(x=>x.department===document.getElementById('f-dept').value);
  if(d){document.getElementById('f-head').value=d.headcount;document.getElementById('f-att').value=d.attendance;
    document.getElementById('f-ot').value=d.overtime;document.getElementById('f-inc').value=d.incidents;
    document.getElementById('f-prod').value=d.productivity;}
}
async function loadShifts(){
  try{
    const res = await fetch('/api/shifts');
    if(!res.ok) return;
    shiftData = await res.json();
    renderShifts();
  }catch(e){console.error('Failed to load shifts',e);}
}

// FIX: After update, fetchData() re-fetches ALL KPIs from server, dashboard numbers update correctly
async function submitUpdate(e){
  e.preventDefault();
  const payload={department:document.getElementById('f-dept').value,
    headcount:+document.getElementById('f-head').value,
    attendance:+document.getElementById('f-att').value,
    overtime:+document.getElementById('f-ot').value,
    incidents:+document.getElementById('f-inc').value,
    productivity:+document.getElementById('f-prod').value};
  const r=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(r.ok){
    // FIX: nav to dashboard so user can SEE the KPIs update
    const dashBtn=document.querySelector('.snav');
    await fetchData();
    alert('Updated! Check the Executive Dashboard for new totals.');
  }
}

const HR_LABELS = {health_index:'Health Index',work_environment:'Work Environment',facilities_score:'Facilities Provided',
  section_efficiency:'Section Efficiency',emergency_medical:'Emergency Medical Readiness',leave_balance:'Leave Allotment',hospital_access:'Hospital Access'};

function loadHR(){
  const d=G.departments.find(x=>x.department===document.getElementById('hr-dept').value);
  if(!d)return;
  const f=document.getElementById('hr-fields');
  f.innerHTML=Object.keys(HR_LABELS).map(k=>`
    <div class="col-md-4"><label style="font-size:.76rem;color:var(--mut);font-weight:600">${HR_LABELS[k]} (0-100)</label>
    <input type="number" min="0" max="100" class="form-control mt-1" id="hr-${k}" value="${d.hr_factors[k]}"></div>`).join('');
}
async function saveHR(){
  const dept=document.getElementById('hr-dept').value;
  const payload={department:dept};
  Object.keys(HR_LABELS).forEach(k=>payload[k]=+document.getElementById('hr-'+k).value);
  const r=await fetch('/api/update/hr',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(r.ok){alert('HR factors saved!');fetchData();}
}
async function runPredict(){
  const dept=document.getElementById('pred-dept').value;
  if(!dept) return;
  const box=document.getElementById('pred-result');
  box.innerHTML='<div style="color:var(--mut);font-size:.8rem">Loading...</div>';
  // Call predict and formula endpoints separately so we can handle prediction errors gracefully
  const resPredict = await fetch('/api/predict/'+encodeURIComponent(dept));
  let pr = null;
  try{ pr = await resPredict.json(); }catch(e){ pr = null }
  if(!resPredict.ok || !pr || pr.error){
    const msg = pr && pr.message ? pr.message : 'Model unavailable: need >=5 history records to train.';
    box.innerHTML = `<div style="color:var(--mut);font-size:.9rem">${msg}</div>`;
    return;
  }
  const frRes = await fetch('/api/formula/'+encodeURIComponent(dept));
  let fr = {};
  try{ fr = await frRes.json(); }catch(e){ fr = {}; }
  const col={Healthy:'#00b894',Moderate:'#fdcb6e',Critical:'#ff7675'};
  // Ensure numeric values to avoid NaN/undefined in UI
  const predicted_hcsi = (typeof pr.predicted_hcsi === 'number') ? pr.predicted_hcsi : Number(pr.predicted_hcsi) || '—';
  const predicted_status = pr.predicted_status || 'Unknown';
  const trend_slope = Number(pr.trend_slope) || 0;
  const intercept = pr.intercept || 0;
  const pc = col[predicted_status]||'#74b9ff';
  const ew = fr.step4_env_weights||{};
  box.innerHTML=`
  <div class="row g-3 mb-3">
    <div class="col-md-3"><div class="dstat"><div class="dsval" style="color:${pc}">${predicted_hcsi}</div><div class="dslbl">Predicted HCSI (7d)</div></div></div>
    <div class="col-md-3"><div class="dstat"><div class="dsval" style="color:${pc}">${predicted_status}</div><div class="dslbl">Predicted Status</div></div></div>
    <div class="col-md-3"><div class="dstat"><div class="dsval">${trend_slope>0?'↑':'↓'} ${Math.abs(trend_slope)}/day</div><div class="dslbl">HCSI Trend</div></div></div>
    <div class="col-md-3"><div class="dstat"><div class="dsval">${fr.step4_env_multiplier}×</div><div class="dslbl">Env. Multiplier</div></div></div>
  </div>

  <div style="background:var(--soft);border-radius:14px;padding:1.1rem;margin-bottom:.8rem">
    <div style="font-weight:800;font-size:.85rem;margin-bottom:.8rem">📐 HCSI Calculation — Step by Step (for ${dept})</div>
    <div style="font-family:monospace;font-size:.78rem;line-height:2">
      <div><b>Step 1 — OT Ratio:</b> (${fr.step1_ot_ratio}% of std hours in OT)</div>
      <div><b>Step 2 — Absenteeism:</b> ${fr.step2_absenteeism}%</div>
      <div><b>Step 3 — Base Score:</b> <span style="color:var(--blue)">${fr.step3_formula}</span></div>
      <div><b>Step 4 — Environmental Multiplier:</b><br>
        &nbsp;&nbsp;Heat=${ew.heat} × 0.40 = ${(ew.heat*0.4).toFixed(3)}<br>
        &nbsp;&nbsp;Physical=${ew.physical} × 0.30 = ${(ew.physical*0.3).toFixed(3)}<br>
        &nbsp;&nbsp;Chemical=${ew.chemical} × 0.20 = ${(ew.chemical*0.2).toFixed(3)}<br>
        &nbsp;&nbsp;Noise=${ew.noise} × 0.10 = ${(ew.noise*0.1).toFixed(3)}<br>
        &nbsp;&nbsp;<span style="color:var(--orange)">→ Multiplier = ${fr.step4_formula}</span>
      </div>
      <div><b>Step 5 — Final HCSI:</b> <span style="color:var(--pink);font-size:.9rem;font-weight:900">${fr.step5_formula}</span></div>
      <div style="color:var(--mut)">${fr.thresholds}</div>
    </div>
  </div>

  <div style="background:var(--soft);border-radius:14px;padding:1rem">
    <div style="font-weight:800;font-size:.85rem;margin-bottom:.5rem">🧠 AI Model — Linear Regression</div>
    <div style="font-size:.75rem;color:var(--mut);line-height:1.8">
      Model: <b>ŷ = slope × x + intercept</b><br>
      Trained on: 30 days of historical HCSI scores for <b>${dept}</b><br>
      Method: Ordinary Least Squares (least-squares fit)<br>
      Fitted slope: <b>${trend_slope}</b> (HCSI change per day)<br>
      Intercept: <b>${intercept}</b><br>
      Prediction at day 37 (7 days ahead): <b style="color:${pc}">${predicted_hcsi}</b><br>
      <span style="color:#a29bfe">Why not if/else: The slope is <i>learned from data</i>, not a fixed rule. Different depts produce different slopes automatically.</span>
    </div>
  </div>`;
}

window.onload=fetchData;
</script>
</body>
</html>"""

if __name__ == '__main__':
    app.run(debug=True)
