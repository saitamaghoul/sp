from flask import Flask, render_template_string, request, jsonify, Response, send_file
import sqlite3, os, json, io, csv
from datetime import datetime, timedelta
import random
import numpy as np
from collections import defaultdict
import base64
from io import BytesIO

app = Flask(__name__)
DATABASE = 'rinl_enterprise.db'

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

# Equipment data per department
EQUIPMENT = {
    'Blast Furnace': [('Blast Furnace', 85), ('Hot Blast Stove', 78), ('Cast House', 82)],
    'Steel Melt Shop': [('BOF Converter', 80), ('Continuous Caster', 75), ('Ladle Furnace', 88)],
    'Rolling Mill': [('Roughing Stand', 90), ('Finishing Stand', 85), ('Coiler', 82)],
    'Power Plant': [('Boiler', 78), ('Turbine', 85), ('Generator', 88)],
    'Maintenance': [('Cranes', 75), ('Compressors', 80), ('Pumps', 82)],
}

def env_multiplier(dept):
    w = ENV_WEIGHTS.get(dept, {'heat':0.3,'noise':0.3,'chemical':0.2,'physical':0.3})
    score = w['heat']*0.4 + w['physical']*0.3 + w['chemical']*0.2 + w['noise']*0.1
    return round(1.0 + score, 3)

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

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
        hospital_access REAL DEFAULT 80,
        retirement_eligible INTEGER DEFAULT 0,
        technical_expertise TEXT DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT,
        message TEXT, severity TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, department TEXT,
        hcsi_score REAL, productivity REAL, recorded_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS equipment_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department TEXT, equipment TEXT, issue TEXT,
        severity TEXT, reported_at TEXT, status TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS retirement_consultants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, department TEXT, expertise TEXT,
        years_experience INTEGER, availability TEXT)''')
    
    c.execute('SELECT COUNT(*) FROM workforce_data')
    if c.fetchone()[0] == 0:
        for d in DEPARTMENTS:
            em = env_multiplier(d[0])
            base_hr = round(90 - (em - 1.0)*40, 1)
            hi  = round(base_hr + random.uniform(-5,5), 1)
            we  = round(max(30, 95 - (em-1.0)*60 + random.uniform(-5,5)), 1)
            fs  = round(base_hr + random.uniform(-5,5), 1)
            se  = round(base_hr + random.uniform(-3,3), 1)
            emd = round(base_hr + random.uniform(-5,5), 1)
            lb  = round(base_hr + random.uniform(-5,5), 1)
            ha  = round(base_hr + random.uniform(-5,5), 1)
            ret_eligible = 1 if random.random() < 0.15 else 0
            expertise = random.choice(['', 'Process Optimization', 'Safety Systems', 'Quality Control', 'Equipment Maintenance'])
            c.execute('''INSERT INTO workforce_data
                (department,headcount,attendance_pct,standard_hours,overtime_hours,incidents,productivity,
                 health_index,work_environment,facilities_score,section_efficiency,
                 emergency_medical,leave_balance,hospital_access,retirement_eligible,technical_expertise)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (d[0],d[1],d[2],d[3],d[4],d[5],d[6],hi,we,fs,se,emd,lb,ha,ret_eligible,expertise))
        for d in DEPARTMENTS:
            em = env_multiplier(d[0])
            base_hcsi = 8 + (em - 1.0) * 25
            for i in range(30):
                day = (datetime.now()-timedelta(days=29-i)).strftime('%Y-%m-%d')
                noise = random.uniform(-2, 2)
                trend = i * (0.1 if em > 1.4 else 0.05)
                hcsi = round(max(5, base_hcsi + noise + trend), 1)
                prod = round(d[6] + random.uniform(-3, 3), 1)
                c.execute('INSERT INTO history (department,hcsi_score,productivity,recorded_at) VALUES (?,?,?,?)',
                          (d[0], hcsi, prod, day))
        
        # Add sample retirement consultants
        consultants = [
            ('Dr. S. Kumar', 'Blast Furnace', 'Process Optimization', 32, 'Available'),
            ('Mr. R. Sharma', 'Steel Melt Shop', 'Safety Systems', 28, 'Available'),
            ('Ms. P. Reddy', 'Rolling Mill', 'Quality Control', 25, 'On Request'),
            ('Mr. A. Singh', 'Power Plant', 'Equipment Maintenance', 30, 'Available'),
        ]
        for name, dept, exp, years, avail in consultants:
            c.execute('INSERT INTO retirement_consultants (name,department,expertise,years_experience,availability) VALUES (?,?,?,?,?)',
                      (name, dept, exp, years, avail))
    
    conn.commit(); conn.close()

init_db()

def migrate_db():
    conn = get_db(); c = conn.cursor()
    cols = [r[1] for r in c.execute("PRAGMA table_info(workforce_data)").fetchall()]
    needed = {
        'standard_hours':'REAL DEFAULT 4000',
        'health_index':'REAL DEFAULT 80',
        'work_environment':'REAL DEFAULT 80',
        'facilities_score':'REAL DEFAULT 80',
        'section_efficiency':'REAL DEFAULT 80',
        'emergency_medical':'REAL DEFAULT 80',
        'leave_balance':'REAL DEFAULT 80',
        'hospital_access':'REAL DEFAULT 80',
        'retirement_eligible':'INTEGER DEFAULT 0',
        'technical_expertise':'TEXT DEFAULT \'\''
    }
    for col,typ in needed.items():
        if col not in cols:
            c.execute(f"ALTER TABLE workforce_data ADD COLUMN {col} {typ}")
    conn.commit(); conn.close()

migrate_db()

HR_COLS = ['health_index','work_environment','facilities_score','section_efficiency',
           'emergency_medical','leave_balance','hospital_access']

def calc_hcsi(dept, ot, std, att, inc):
    ot_ratio = (ot/std)*100 if std else 0
    absenteeism = 100 - att
    base = (ot_ratio*0.35) + (absenteeism*0.25) + (inc*10*0.25) + 10
    em = env_multiplier(dept)
    final = round(base * em, 1)
    if final < 20:  return final, "Healthy", "success"
    elif final < 40: return final, "Moderate", "warning"
    else: return final, "Critical", "danger"

@app.route('/api/data')
def get_data():
    conn = get_db()
    rows = conn.execute('SELECT * FROM workforce_data').fetchall()
    alerts = conn.execute('SELECT * FROM alerts ORDER BY id DESC LIMIT 20').fetchall()
    equipment = conn.execute('SELECT * FROM equipment_reports ORDER BY id DESC LIMIT 50').fetchall()
    consultants = conn.execute('SELECT * FROM retirement_consultants').fetchall()
    conn.close()
    depts=[]; total_emp=0; total_ot=0
    for r in rows:
        sc,st,cl = calc_hcsi(r['department'],r['overtime_hours'],r['standard_hours'],r['attendance_pct'],r['incidents'])
        risk,factors = hr_risk_score(r)
        ew = ENV_WEIGHTS.get(r['department'],{'heat':0.3,'noise':0.3,'chemical':0.2,'physical':0.3})
        equip = EQUIPMENT.get(r['department'], [])
        depts.append({"id":r['id'],"department":r['department'],"headcount":r['headcount'],
            "attendance":r['attendance_pct'],"overtime":r['overtime_hours'],
            "incidents":r['incidents'],"productivity":r['productivity'],
            "standard_hours":r['standard_hours'],"hcsi_score":sc,"hcsi_status":st,"color":cl,
            "env_multiplier":env_multiplier(r['department']),"env_weights":ew,
            "hr_risk":risk,"hr_factors":factors,
            "retirement_eligible":r['retirement_eligible'],"technical_expertise":r['technical_expertise'] or 'None',
            "equipment":equip})
        total_emp+=r['headcount']; total_ot+=r['overtime_hours']
    avg_att=round(sum(d['attendance'] for d in depts)/len(depts),1) if depts else 0
    return jsonify({"departments":depts,
        "kpis":{"total_employees":total_emp,"total_overtime":int(total_ot),
                "avg_attendance":avg_att,"critical_depts":sum(1 for d in depts if d['hcsi_status']=='Critical'),
                "retirement_eligible":sum(1 for d in depts if d['retirement_eligible'])},
        "alerts":[{"id":a['id'],"department":a['department'],"message":a['message'],
                   "severity":a['severity'],"created_at":a['created_at']} for a in alerts],
        "equipment": [{"id":e['id'],"department":e['department'],"equipment":e['equipment'],
                      "issue":e['issue'],"severity":e['severity'],"status":e['status'],
                      "reported_at":e['reported_at']} for e in equipment],
        "consultants":[{"id":c['id'],"name":c['name'],"department":c['department'],
                       "expertise":c['expertise'],"years":c['years_experience'],
                       "availability":c['availability']} for c in consultants]})

def hr_risk_score(r):
    factors = {c: r[c] for c in HR_COLS}
    avg = sum(factors.values())/len(factors)
    return round(100-avg, 1), factors

@app.route('/api/history/<dept>')
def get_history(dept):
    conn = get_db()
    rows = conn.execute('SELECT * FROM history WHERE department=? ORDER BY recorded_at',(dept,)).fetchall()
    conn.close()
    return jsonify([{"date":r['recorded_at'],"hcsi":r['hcsi_score'],"productivity":r['productivity']} for r in rows])

@app.route('/api/predict/<dept>')
def predict(dept):
    conn = get_db()
    rows = conn.execute('SELECT hcsi_score FROM history WHERE department=? ORDER BY recorded_at',(dept,)).fetchall()
    conn.close()
    y = np.array([r['hcsi_score'] for r in rows])
    if len(y) < 5: return jsonify({"error":"not enough history"}), 400
    x = np.arange(len(y))
    A = np.vstack([x, np.ones(len(x))]).T
    slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
    next_val = round(float(max(5, min(80, slope*(len(y)+7)+intercept))), 1)
    status = "Healthy" if next_val<20 else "Moderate" if next_val<40 else "Critical"
    return jsonify({"predicted_hcsi":next_val,"predicted_status":status,
                    "trend_slope":round(float(slope),3),"intercept":round(float(intercept),2)})

@app.route('/api/export/csv')
def export_csv():
    """Export full plant data insights as CSV"""
    conn = get_db()
    rows = conn.execute('SELECT * FROM workforce_data').fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Department', 'Headcount', 'Attendance%', 'Std Hours', 'OT Hours', 
                     'Incidents', 'Productivity', 'HCSI Score', 'Status', 
                     'Health Index', 'Work Environment', 'Facilities Score',
                     'Section Efficiency', 'Emergency Medical', 'Leave Balance',
                     'Hospital Access', 'Retirement Eligible', 'Technical Expertise'])
    
    for r in rows:
        sc, st, _ = calc_hcsi(r['department'], r['overtime_hours'], r['standard_hours'], 
                             r['attendance_pct'], r['incidents'])
        writer.writerow([r['department'], r['headcount'], r['attendance_pct'], 
                        r['standard_hours'], r['overtime_hours'], r['incidents'],
                        r['productivity'], sc, st, r['health_index'], r['work_environment'],
                        r['facilities_score'], r['section_efficiency'], r['emergency_medical'],
                        r['leave_balance'], r['hospital_access'], r['retirement_eligible'],
                        r['technical_expertise']])
    
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=rinl_workforce_insights.csv",
                            "Cache-Control": "no-cache"})

@app.route('/api/export/department/<dept>')
def export_department_csv(dept):
    """Export individual department data insights as CSV"""
    conn = get_db()
    r = conn.execute('SELECT * FROM workforce_data WHERE department=?', (dept,)).fetchone()
    if not r:
        return jsonify({"error": "Department not found"}), 404
    
    history = conn.execute('SELECT * FROM history WHERE department=? ORDER BY recorded_at', (dept,)).fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['Department', 'Headcount', 'Attendance%', 'Std Hours', 'OT Hours', 
                     'Incidents', 'Productivity', 'Health Index', 'Work Environment',
                     'Facilities Score', 'Section Efficiency', 'Emergency Medical',
                     'Leave Balance', 'Hospital Access', 'Retirement Eligible', 'Technical Expertise'])
    sc, st, _ = calc_hcsi(r['department'], r['overtime_hours'], r['standard_hours'], 
                         r['attendance_pct'], r['incidents'])
    writer.writerow([r['department'], r['headcount'], r['attendance_pct'], 
                    r['standard_hours'], r['overtime_hours'], r['incidents'],
                    r['productivity'], r['health_index'], r['work_environment'],
                    r['facilities_score'], r['section_efficiency'], r['emergency_medical'],
                    r['leave_balance'], r['hospital_access'], r['retirement_eligible'],
                    r['technical_expertise']])
    
    writer.writerow([])
    writer.writerow(['History Data'])
    writer.writerow(['Date', 'HCSI Score', 'Productivity'])
    for h in history:
        writer.writerow([h['recorded_at'], h['hcsi_score'], h['productivity']])
    
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": f"attachment;filename={dept}_insights.csv",
                            "Cache-Control": "no-cache"})

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
    row = conn.execute('SELECT standard_hours FROM workforce_data WHERE department=?',(d['department'],)).fetchone()
    std_hours = row['standard_hours'] if row else 4000
    conn.execute('UPDATE workforce_data SET headcount=?,attendance_pct=?,overtime_hours=?,incidents=?,productivity=? WHERE department=?',
                 (d['headcount'],d['attendance'],d['overtime'],d['incidents'],d['productivity'],d['department']))
    sc,st,_ = calc_hcsi(d['department'],d['overtime'],std_hours,d['attendance'],d['incidents'])
    conn.execute('INSERT INTO history (department,hcsi_score,productivity,recorded_at) VALUES (?,?,?,?)',
                 (d['department'],sc,d['productivity'],datetime.now().strftime('%Y-%m-%d')))
    if st=='Critical':
        conn.execute('INSERT INTO alerts (department,message,severity,created_at) VALUES (?,?,?,?)',
                     (d['department'],f"{d['department']} is now Critical stress level!",'danger',datetime.now().strftime('%H:%M')))
    conn.commit(); conn.close()
    return jsonify({"status":"success"})

@app.route('/api/alert', methods=['POST'])
def add_alert():
    d=request.json; conn=get_db()
    conn.execute('INSERT INTO alerts (department,message,severity,created_at) VALUES (?,?,?,?)',
                 (d.get('department','System'),d['message'],d.get('severity','info'),datetime.now().strftime('%H:%M')))
    conn.commit(); conn.close()
    return jsonify({"status":"ok"})

@app.route('/api/equipment/report', methods=['POST'])
def report_equipment():
    d = request.json
    conn = get_db()
    conn.execute('''INSERT INTO equipment_reports 
                   (department, equipment, issue, severity, reported_at, status)
                   VALUES (?,?,?,?,?,?)''',
                 (d['department'], d['equipment'], d['issue'], d['severity'],
                  datetime.now().strftime('%Y-%m-%d %H:%M'), 'Open'))
    conn.commit()
    
    # Create alert for critical equipment issues
    if d['severity'] in ['Critical', 'High']:
        conn.execute('INSERT INTO alerts (department,message,severity,created_at) VALUES (?,?,?,?)',
                     (d['department'], f"⚠️ {d['equipment']} - {d['issue']} ({d['severity']})", 
                      d['severity'].lower(), datetime.now().strftime('%H:%M')))
    conn.commit()
    conn.close()
    return jsonify({"status":"success"})

@app.route('/api/retirement/consult', methods=['POST'])
def consult_retirement():
    d = request.json
    conn = get_db()
    conn.execute('''INSERT INTO retirement_consultants 
                   (name, department, expertise, years_experience, availability)
                   VALUES (?,?,?,?,?)''',
                 (d['name'], d['department'], d['expertise'], d['years_experience'], d['availability']))
    conn.commit()
    conn.close()
    return jsonify({"status":"success"})

@app.route('/')
def index():
    return render_template_string(HTML)

HTML = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RINL Workforce Intelligence V5</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root{--pink:#ff6b9d;--orange:#ff9f43;--purple:#a29bfe;--blue:#74b9ff;--red:#ff7675;
  --soft:#f8f4f0;--card:#ffffff;--sid:#1a1a2e;--sid2:#16213e;--txt:#2d3436;--mut:#636e72;
  --shad:0 8px 32px rgba(0,0,0,.08);--rad:16px;}
[data-theme="dark"]{--soft:#0f0f1a;--card:#1e1e2e;--txt:#e2e8f0;--mut:#94a3b8;}
*{box-sizing:border-box;transition:background .3s,color .3s;}
body{background:var(--soft);color:var(--txt);font-family:'Segoe UI',system-ui,sans-serif;margin:0;overflow-x:hidden;}
.wrapper{display:flex;min-height:100vh;}
.sidebar{width:240px;background:var(--sid);min-height:100vh;position:fixed;top:0;left:0;z-index:100;display:flex;flex-direction:column;overflow-y:auto;}
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
.main{margin-left:240px;padding:1.2rem 1.4rem;min-height:100vh;width:calc(100vw - 240px);}
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
.modal-content{border-radius:18px;border:none;box-shadow:0 20px 60px rgba(0,0,0,.2);}
.modal-header{border-bottom:none;padding:1.3rem 1.3rem .4rem;}
.dstat{background:var(--soft);border-radius:11px;padding:.7rem;text-align:center;}
.dsval{font-size:1.25rem;font-weight:800;}
.dslbl{font-size:.65rem;color:var(--mut);text-transform:uppercase;}
.aitm{display:flex;align-items:center;gap:10px;padding:.75rem;border-radius:10px;margin-bottom:.45rem;background:var(--soft);}
[data-theme="dark"] .aitm{background:rgba(255,255,255,.05);}
.adot{width:9px;height:9px;border-radius:50%;flex-shrink:0;}
.shift-table td,.shift-table th{padding:.5rem .7rem;font-size:.78rem;vertical-align:middle;}
.sbdge{border-radius:7px;padding:.2rem .5rem;font-size:.68rem;font-weight:700;}
.equip-card{background:var(--soft);border-radius:11px;padding:.8rem;margin-bottom:.6rem;}
.equip-card .badge{font-size:.7rem;}
.equip-status{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;}
@media(max-width:768px){.sidebar{width:190px;}.main{margin-left:190px;width:calc(100vw - 190px);padding:.9rem;}}
@media(max-width:576px){.sidebar{position:fixed;left:-220px;transition:left .3s;}.sidebar.open{left:0;}.main{margin-left:0;width:100vw;}}
</style>
</head>
<body>
<div class="wrapper">

<div class="sidebar" id="sidebar">
  <div class="sbrand">
    <div class="sicon">🏭</div>
    <div><div class="stxt">RINL Analytics</div><div class="ssub">TECHNO-HR INTEL</div></div>
  </div>
  <div class="ssec">Main</div>
  <button class="snav active" onclick="nav('dashboard',this)"><div class="ni">📊</div>Executive Dashboard</button>
  <button class="snav" onclick="nav('heatmap',this)"><div class="ni">🗺️</div>Plant Heatmap</button>
  <button class="snav" onclick="nav('simulator',this)"><div class="ni">⚡</div>Capacity Simulator</button>
  <div class="ssec">HR & Operations</div>
  <button class="snav" onclick="nav('equipment',this)"><div class="ni">🔧</div>Equipment Health</button>
  <button class="snav" onclick="nav('retirement',this)"><div class="ni">🎯</div>Retirement Consultancy</button>
  <button class="snav" onclick="nav('hrfactors',this)"><div class="ni">🏥</div>HR Factors</button>
  <button class="snav" onclick="nav('predict',this)"><div class="ni">🧠</div>AI Prediction</button>
  <div class="ssec">Management</div>
  <button class="snav" onclick="nav('alerts',this)"><div class="ni">🔔</div>Alerts<span class="abadge" id="alert-count">0</span></button>
  <button class="snav" onclick="nav('data',this)"><div class="ni">🗄️</div>Data Management</button>
  <button class="dtoggle" onclick="toggleDark()"><span id="dlbl">🌙 Dark Mode</span><span id="dico">○</span></button>
</div>

<div class="main">
  <div class="topbar">
    <div class="ptitle" id="pageTitle">Executive Dashboard</div>
    <div class="tright">
      <div class="spill">● Live</div>
      <button class="rbtn" onclick="fetchData()">↻ Refresh</button>
      <button class="rbtn" onclick="exportFullData()">⬇ Full Plant CSV</button>
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
    <div class="row g-3">
      <div class="col-lg-8"><div class="card" style="height:380px">
        <div style="font-weight:800;font-size:.9rem;margin-bottom:.8rem">📈 HCSI Stress Index — All Departments</div>
        <div style="height:310px;position:relative"><canvas id="barChart"></canvas></div>
      </div></div>
      <div class="col-lg-4"><div class="card" style="height:380px;overflow-y:auto">
        <div style="font-weight:800;font-size:.9rem;margin-bottom:.8rem">⚠️ Risk Watchlist</div>
        <div id="ai-insights"></div>
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
        <span style="margin-left:auto;font-size:.72rem;color:var(--mut)">Click card for details & export</span>
      </div>
    </div>
    <div class="hgrid" id="heat-grid"></div>
  </div>

  <!-- SIMULATOR -->
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
        <label style="font-size:.78rem;color:var(--mut);font-weight:600;margin-top:1rem;display:block">Expected Absenteeism (%)</label>
        <input type="number" class="form-control mt-1" id="sim-ab" value="8" min="0" max="50" oninput="runSim()">
        <label style="font-size:.78rem;color:var(--mut);font-weight:600;margin-top:1rem;display:block">OT Pay Rate per Hour (Rs.)</label>
        <input type="number" class="form-control mt-1" id="sim-rate" value="400" oninput="runSim()">
      </div></div>
      <div class="col-md-7"><div class="card h-100" style="background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff">
        <div style="font-weight:800;margin-bottom:1rem;font-size:.95rem">📊 What Will Happen</div>
        <div class="row g-2 mb-3">
          <div class="col-6"><div class="sres" style="background:rgba(116,185,255,.1);border-radius:12px">
            <div class="srv" style="color:#74b9ff" id="r-hc">--</div>
            <div class="srl">Workers Needed</div>
          </div></div>
          <div class="col-6"><div class="sres" style="background:rgba(253,203,110,.1);border-radius:12px">
            <div class="srv" style="color:#fdcb6e" id="r-gap">--</div>
            <div class="srl">Workforce Gap</div>
          </div></div>
        </div>
        <div style="background:rgba(255,118,117,.1);border-radius:12px;padding:1rem;text-align:center;margin-bottom:1rem">
          <div class="srv" style="color:#ff7675;font-size:1.6rem" id="r-cost">--</div>
          <div class="srl">Est. Extra OT Cost / month</div>
        </div>
        <div style="background:rgba(255,255,255,.06);border-radius:12px;padding:.9rem">
          <div style="font-size:.72rem;color:#94a3b8;margin-bottom:.5rem;font-weight:600">Stress Level Forecast</div>
          <div style="height:10px;border-radius:999px;background:linear-gradient(90deg,#00b894,#fdcb6e,#d63031);position:relative">
            <div id="stress-mk" style="position:absolute;top:-5px;width:20px;height:20px;background:#fff;border-radius:50%;border:2px solid #ccc;box-shadow:0 2px 6px rgba(0,0,0,.4);transition:left .4s;left:0"></div>
          </div>
          <div id="stress-lbl" style="font-size:.78rem;margin-top:.5rem;color:#a29bfe;font-weight:700">Loading...</div>
        </div>
      </div></div>
    </div>
  </div>

  <!-- EQUIPMENT HEALTH -->
  <div id="equipment" class="page">
    <div class="row g-3">
      <div class="col-lg-5"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">🔧 Report Equipment Issue</div>
        <form onsubmit="reportEquipment(event)">
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Department</label>
            <select class="form-select mt-1" id="eq-dept" required></select>
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Equipment</label>
            <input type="text" class="form-control mt-1" id="eq-name" required placeholder="e.g., Turbine, Boiler, Caster">
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Issue Description</label>
            <input type="text" class="form-control mt-1" id="eq-issue" required placeholder="Brief description">
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Severity</label>
            <select class="form-select mt-1" id="eq-severity">
              <option value="Critical">🔥 Critical</option>
              <option value="High">⚡ High</option>
              <option value="Medium">🟡 Medium</option>
              <option value="Low">🟢 Low</option>
            </select>
          </div>
          <button type="submit" class="btn w-100" style="background:linear-gradient(135deg,var(--pink),var(--orange));color:#fff;border-radius:11px;font-weight:700;padding:.65rem">Report Issue</button>
        </form>
      </div></div>
      <div class="col-lg-7"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">📊 Equipment Health Status</div>
        <div id="equipment-list"></div>
        <div style="margin-top:1rem;height:200px"><canvas id="equipChart"></canvas></div>
      </div></div>
    </div>
  </div>

  <!-- RETIREMENT CONSULTANCY -->
  <div id="retirement" class="page">
    <div class="row g-3">
      <div class="col-lg-5"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">🎯 Register Retirement Consultant</div>
        <form onsubmit="registerConsultant(event)">
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Full Name</label>
            <input type="text" class="form-control mt-1" id="rc-name" required>
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Department Expertise</label>
            <select class="form-select mt-1" id="rc-dept" required></select>
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Expertise Area</label>
            <select class="form-select mt-1" id="rc-expertise">
              <option value="Process Optimization">Process Optimization</option>
              <option value="Safety Systems">Safety Systems</option>
              <option value="Quality Control">Quality Control</option>
              <option value="Equipment Maintenance">Equipment Maintenance</option>
              <option value="Production Planning">Production Planning</option>
              <option value="Supply Chain">Supply Chain</option>
            </select>
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Years of Experience</label>
            <input type="number" class="form-control mt-1" id="rc-years" value="25" min="10" max="45" required>
          </div>
          <div class="mb-2">
            <label style="font-size:.78rem;color:var(--mut);font-weight:600">Availability</label>
            <select class="form-select mt-1" id="rc-avail">
              <option value="Available">Available</option>
              <option value="On Request">On Request</option>
              <option value="Part-time">Part-time</option>
            </select>
          </div>
          <button type="submit" class="btn w-100" style="background:linear-gradient(135deg,var(--purple),var(--blue));color:#fff;border-radius:11px;font-weight:700;padding:.65rem">Register Consultant</button>
        </form>
        <div style="margin-top:.8rem;font-size:.72rem;color:var(--mut);padding:.5rem;background:var(--soft);border-radius:9px">
          💡 RINL can leverage retired experts' deep technical knowledge to drive efficiency, reduce costs, and mentor junior staff.
        </div>
      </div></div>
      <div class="col-lg-7"><div class="card">
        <div style="font-weight:800;margin-bottom:1rem">👨‍🏫 Available Technical Consultants</div>
        <div id="consultant-list"></div>
        <div style="margin-top:1rem;padding:.8rem;background:linear-gradient(135deg,#a29bfe22,#74b9ff22);border-radius:11px;border-left:3px solid var(--purple)">
          <div style="font-size:.78rem;color:var(--mut)">📈 <b>Strategic Value:</b> Retired experts bring 25+ years of domain knowledge, reducing training costs and improving operational excellence.</div>
        </div>
      </div></div>
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
      <div style="font-size:.78rem;color:var(--mut)">Trained on 30-day HCSI history per department using least-squares regression (numpy). Forecasts next 7 days.</div>
    </div>
    <div class="card">
      <select class="form-select mb-3" id="pred-dept" onchange="runPredict()" style="max-width:320px"></select>
      <div id="pred-result"></div>
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

  <!-- DATA MANAGEMENT -->
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
</div>
</div>

<!-- DEPT MODAL -->
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
        <div style="margin-top:.8rem;display:flex;gap:.5rem;flex-wrap:wrap">
          <button class="rbtn" onclick="exportDeptCSV()" style="background:var(--pink);color:#fff;border-color:var(--pink)">⬇ Export Dept CSV</button>
          <span style="font-size:.7rem;color:var(--mut);align-self:center">Download detailed department insights</span>
        </div>
        <div style="margin-top:1rem;background:var(--soft);border-radius:12px;padding:1rem">
          <div style="font-weight:700;font-size:.85rem;margin-bottom:.8rem">⚡ What-If Simulator</div>
          <div class="row g-2 align-items-center">
            <div class="col-sm-4">
              <label style="font-size:.72rem;color:var(--mut);font-weight:600">OT Hours</label>
              <input type="number" class="form-control form-control-sm mt-1" id="ds-ot" oninput="runDeptSim()">
            </div>
            <div class="col-sm-4">
              <label style="font-size:.72rem;color:var(--mut);font-weight:600">Attendance %</label>
              <input type="number" class="form-control form-control-sm mt-1" id="ds-att" oninput="runDeptSim()">
            </div>
            <div class="col-sm-4">
              <label style="font-size:.72rem;color:var(--mut);font-weight:600">Incidents</label>
              <input type="number" class="form-control form-control-sm mt-1" id="ds-inc" oninput="runDeptSim()">
            </div>
          </div>
          <div id="ds-result" style="margin-top:.7rem;font-size:.82rem;font-weight:700;padding:.5rem .8rem;border-radius:9px;background:var(--card)"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
let G={}, barChart=null, trendChart=null, equipChart=null, dark=false, baseWF=0, heatFilter='all', modalDept=null, currentDeptForExport=null;

function nav(page, el) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.snav').forEach(n=>n.classList.remove('active'));
  document.getElementById(page).classList.add('active');
  if(el) el.classList.add('active');
  const T={dashboard:'Executive Dashboard',heatmap:'Plant Operations Heatmap',
    simulator:'Workforce Capacity Simulator',equipment:'Equipment Health Monitor',
    retirement:'Retirement Consultancy',hrfactors:'HR Wellbeing Factors',
    predict:'AI Prediction',alerts:'Alert Center',data:'Data Management'};
  document.getElementById('pageTitle').textContent=T[page]||page;
  if(page==='hrfactors')loadHR();
  if(page==='predict')runPredict();
  if(page==='equipment')renderEquipment();
  if(page==='retirement')renderConsultants();
}

function toggleDark(){
  dark=!dark;
  document.documentElement.setAttribute('data-theme',dark?'dark':'light');
  document.getElementById('dlbl').textContent=dark?'☀️ Light Mode':'🌙 Dark Mode';
  document.getElementById('dico').textContent=dark?'●':'○';
}

function animCount(el,target,suf=''){
  let v=0,step=Math.max(1,target/(800/16));
  const t=setInterval(()=>{v=Math.min(v+step,target);el.textContent=Math.floor(v).toLocaleString()+suf;if(v>=target)clearInterval(t);},16);
}

async function fetchData(){
  try {
    const res=await fetch('/api/data');
    if(!res.ok){ document.getElementById('k-emp').textContent='ERR'; return; }
    G=await res.json();
    baseWF=G.kpis.total_employees;
    renderDash(); renderHeat(); renderAlerts(); populateDepts();
    document.getElementById('sim-wf').value=baseWF;
    runSim();
    document.getElementById('alert-count').textContent=G.alerts.length;
    renderEquipment();
    renderConsultants();
  } catch(e){ console.error('Fetch failed',e); }
}

function renderDash(){
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
  const depts=heatFilter==='all'?G.departments:G.departments.filter(d=>d.hcsi_status===heatFilter);
  depts.forEach(d=>{
    const dn=d.department.replace(/'/g,"\\\\'");
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

async function openModal(dept){
  const d=G.departments.find(x=>x.department===dept);
  if(!d)return;
  modalDept=d; currentDeptForExport=dept;
  document.getElementById('modal-title').textContent='🏭 '+dept;
  const bc={success:'#00b894',warning:'#fdcb6e',danger:'#d63031'};
  const mb=document.getElementById('modal-badge');
  mb.style.background=bc[d.color]; mb.textContent=d.hcsi_status+' · HCSI '+d.hcsi_score;
  document.getElementById('modal-stats').innerHTML=[
    ['👥 Headcount',d.headcount],['📅 Attendance',d.attendance+'%'],
    ['⏱ Overtime',d.overtime+'h'],['⚠ Incidents',d.incidents],
    ['📈 Productivity',d.productivity+'%'],['📋 Std Hours',d.standard_hours+'h']
  ].map(([l,v])=>`<div class="col-4"><div class="dstat"><div class="dsval">${v}</div><div class="dslbl">${l}</div></div></div>`).join('');
  document.getElementById('ds-ot').value=d.overtime;
  document.getElementById('ds-att').value=d.attendance;
  document.getElementById('ds-inc').value=d.incidents;
  runDeptSim();
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

function exportDeptCSV(){
  if(!currentDeptForExport) return;
  window.location.href='/api/export/department/'+encodeURIComponent(currentDeptForExport);
}

function exportFullData(){
  window.location.href='/api/export/csv';
}

function runDeptSim(){
  if(!modalDept)return;
  const ot=parseFloat(document.getElementById('ds-ot').value)||0;
  const att=parseFloat(document.getElementById('ds-att').value)||100;
  const inc=parseInt(document.getElementById('ds-inc').value)||0;
  const std=modalDept.standard_hours||4000;
  const ot_ratio=(ot/std)*100;
  const score=((ot_ratio)*0.35)+((100-att)*0.25)+(inc*10*0.25)+10;
  const sc=Math.round(score*modalDept.env_multiplier*10)/10;
  const st=sc<15?'Healthy':sc<30?'Moderate':'Critical';
  const col={Healthy:'#00b894',Moderate:'#fdcb6e',Critical:'#ff7675'}[st];
  document.getElementById('ds-result').style.color=col;
  document.getElementById('ds-result').style.borderLeft=`3px solid ${col}`;
  document.getElementById('ds-result').textContent=`Simulated HCSI: ${sc} — Status: ${st}`;
}

function resetWF(){document.getElementById('sim-wf').value=baseWF;runSim();}
function runSim(){
  const base=parseInt(document.getElementById('sim-wf').value)||0;
  const inc=parseInt(document.getElementById('sim-target').value)||0;
  const ab=parseFloat(document.getElementById('sim-ab').value)||0;
  const rate=parseInt(document.getElementById('sim-rate').value)||400;
  document.getElementById('sim-tv').textContent=inc+'%';
  const needed=Math.ceil(base*(1+inc/100));
  const avail=Math.floor(base*(1-ab/100));
  const gap=Math.max(0, needed-avail);
  const cost=gap*8*26*rate;
  document.getElementById('r-hc').textContent=needed.toLocaleString();
  document.getElementById('r-gap').textContent=gap;
  document.getElementById('r-cost').textContent='Rs.'+cost.toLocaleString();
  const pct=Math.min(100,base>0?(gap/base)*100:0);
  document.getElementById('stress-mk').style.left='calc('+Math.min(95,pct)+'% - 10px)';
  const label=pct<10?'🟢 Low Stress — manageable':
               pct<25?'🟡 Moderate — plan overtime':
                      '🔴 High Risk — urgent action';
  document.getElementById('stress-lbl').textContent=label;
}

function renderEquipment(){
  const list=document.getElementById('equipment-list');
  if(!G.equipment || G.equipment.length===0){
    list.innerHTML='<div style="color:var(--mut);text-align:center;padding:1rem">No equipment reports yet</div>';
    return;
  }
  const severityColors={Critical:'#ff7675',High:'#fdcb6e',Medium:'#74b9ff',Low:'#00b894'};
  const statusColors={Open:'#ff7675','In Progress':'#fdcb6e',Resolved:'#00b894'};
  list.innerHTML=G.equipment.slice(0,10).map(e=>`
    <div class="equip-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-weight:700;font-size:.85rem">${e.equipment}</span>
          <span class="badge" style="background:${severityColors[e.severity]||'#94a3b8'}">${e.severity}</span>
          <span class="badge" style="background:${statusColors[e.status]||'#94a3b8'}">${e.status}</span>
        </div>
        <span style="font-size:.7rem;color:var(--mut)">${e.reported_at}</span>
      </div>
      <div style="font-size:.75rem;color:var(--mut);margin-top:.2rem">${e.department}: ${e.issue}</div>
    </div>
  `).join('');

  // Equipment Health Chart
  const labels = G.equipment.map(e => e.equipment);
  const data = G.equipment.map(e => {
    const severityScore = {Critical:30, High:60, Medium:80, Low:95};
    return severityScore[e.severity] || 80;
  });
  if(equipChart) equipChart.destroy();
  equipChart = new Chart(document.getElementById('equipChart').getContext('2d'), {
    type: 'bar',
    data: {labels: labels.slice(0,10), datasets:[{label:'Health Score',data:data.slice(0,10),
              backgroundColor: data.slice(0,10).map(v=>v<40?'#ff7675':v<70?'#fdcb6e':'#00b894'), borderRadius:6}]},
    options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
              scales:{y:{min:0,max:100,grid:{color:'rgba(0,0,0,.05)'}},x:{grid:{display:false}}}}
  });
}

function renderConsultants(){
  const list=document.getElementById('consultant-list');
  if(!G.consultants || G.consultants.length===0){
    list.innerHTML='<div style="color:var(--mut);text-align:center;padding:1rem">No consultants registered yet</div>';
    return;
  }
  list.innerHTML=G.consultants.map(c=>`
    <div class="equip-card" style="border-left:3px solid ${c.availability==='Available'?'#00b894':'#fdcb6e'}">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <span style="font-weight:700;font-size:.85rem">${c.name}</span>
          <span class="badge" style="background:${c.availability==='Available'?'#00b894':'#fdcb6e'}">${c.availability}</span>
        </div>
        <span style="font-size:.7rem;color:var(--mut)">${c.years} years</span>
      </div>
      <div style="font-size:.75rem;color:var(--mut)">${c.department} · ${c.expertise}</div>
    </div>
  `).join('');
}

async function reportEquipment(e){
  e.preventDefault();
  const payload={
    department: document.getElementById('eq-dept').value,
    equipment: document.getElementById('eq-name').value,
    issue: document.getElementById('eq-issue').value,
    severity: document.getElementById('eq-severity').value
  };
  await fetch('/api/equipment/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  document.getElementById('eq-name').value='';
  document.getElementById('eq-issue').value='';
  fetchData();
  alert('Equipment issue reported successfully!');
}

async function registerConsultant(e){
  e.preventDefault();
  const payload={
    name: document.getElementById('rc-name').value,
    department: document.getElementById('rc-dept').value,
    expertise: document.getElementById('rc-expertise').value,
    years_experience: parseInt(document.getElementById('rc-years').value),
    availability: document.getElementById('rc-avail').value
  };
  await fetch('/api/retirement/consult',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  document.getElementById('rc-name').value='';
  fetchData();
  alert('Consultant registered successfully!');
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
  ['f-dept','eq-dept','rc-dept','hr-dept','pred-dept'].forEach(id=>{
    const sel=document.getElementById(id);
    if(sel && sel.options.length===0) G.departments.forEach(d=>sel.options.add(new Option(d.department,d.department)));
  });
  loadForm();
}
function loadForm(){
  const d=G.departments.find(x=>x.department===document.getElementById('f-dept').value);
  if(d){document.getElementById('f-head').value=d.headcount;document.getElementById('f-att').value=d.attendance;
    document.getElementById('f-ot').value=d.overtime;document.getElementById('f-inc').value=d.incidents;
    document.getElementById('f-prod').value=d.productivity;}
}

async function submitUpdate(e){
  e.preventDefault();
  const payload={department:document.getElementById('f-dept').value,
    headcount:+document.getElementById('f-head').value,
    attendance:+document.getElementById('f-att').value,
    overtime:+document.getElementById('f-ot').value,
    incidents:+document.getElementById('f-inc').value,
    productivity:+document.getElementById('f-prod').value};
  const r=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(r.ok){await fetchData();alert('Updated successfully!');}
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
  if(!dept)return;
  const box=document.getElementById('pred-result');
  box.innerHTML='<div style="color:var(--mut);font-size:.8rem">Loading...</div>';
  const res=await fetch('/api/predict/'+encodeURIComponent(dept));
  const pr=await res.json();
  if(pr.error){box.innerHTML=`<div style="color:var(--mut)">${pr.error}</div>`;return;}
  const col={Healthy:'#00b894',Moderate:'#fdcb6e',Critical:'#ff7675'};
  const pc=col[pr.predicted_status]||'#74b9ff';
  box.innerHTML=`
  <div class="row g-3 mb-3">
    <div class="col-md-4"><div class="dstat"><div class="dsval" style="color:${pc}">${pr.predicted_hcsi}</div><div class="dslbl">Predicted HCSI (7d)</div></div></div>
    <div class="col-md-4"><div class="dstat"><div class="dsval" style="color:${pc}">${pr.predicted_status}</div><div class="dslbl">Predicted Status</div></div></div>
    <div class="col-md-4"><div class="dstat"><div class="dsval">${pr.trend_slope>0?'↑':'↓'} ${Math.abs(pr.trend_slope)}/day</div><div class="dslbl">HCSI Trend</div></div></div>
  </div>
  <div style="background:var(--soft);border-radius:14px;padding:1rem">
    <div style="font-weight:800;font-size:.85rem;margin-bottom:.5rem">🧠 AI Model — Linear Regression</div>
    <div style="font-size:.75rem;color:var(--mut);line-height:1.8">
      Model: <b>ŷ = slope × x + intercept</b><br>
      Trained on: 30 days of historical HCSI scores for <b>${dept}</b><br>
      Fitted slope: <b>${pr.trend_slope}</b> (HCSI change per day)<br>
      Intercept: <b>${pr.intercept}</b><br>
      Prediction at day 37: <b style="color:${pc}">${pr.predicted_hcsi}</b>
    </div>
  </div>`;
}

window.onload=fetchData;
</script>
</body>
</html>"""

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
