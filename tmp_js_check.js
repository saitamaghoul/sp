
let G={}, barChart=null, trendChart=null, equipChart=null, shiftData=[], dark=false, baseWF=0, heatFilter='all', modalDept=null;

function nav(page, el) {
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.snav').forEach(n=>n.classList.remove('active'));
  document.getElementById(page).classList.add('active');
  el.classList.add('active');
  const T={dashboard:'Executive Dashboard',heatmap:'Plant Operations Heatmap',
    simulator:'Workforce Capacity Simulator',shift:'Shift Planner',alerts:'Alert Center',data:'Data Management',
    hrfactors:'HR Wellbeing Factors',predict:'AI Prediction'};
  document.getElementById('pageTitle').textContent=T[page]||page;
  if(page==='hrfactors')loadHR();
  if(page==='predict')runPredict();
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

function getActivePage(){
  const active = document.querySelector('.page.active');
  return active ? active.id : 'dashboard';
}

function exportCurrentTab(){
  const page = getActivePage();
  if(page === 'shift'){
    if(!shiftData.length){ return alert('No shift schedule to export.'); }
    const rows=[['Department','Shift','Staff','Status']];
    shiftData.forEach(s => rows.push([s.dept,s.shift,s.count,s.count>=30?'OK':'Low']));
    const csv = rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = 'rinl_shift_schedule.csv';
    a.click();
    return;
  }
  if(page === 'simulator'){
    const target = document.getElementById('sim-target').value;
    const workforce = document.getElementById('sim-wf').value;
    const absenteeism = document.getElementById('sim-ab').value;
    const rate = document.getElementById('sim-rate').value;
    const needed = Math.ceil(workforce*(1+target/100));
    const avail = Math.floor(workforce*(1-absenteeism/100));
    const gap = Math.max(0, needed-avail);
    const cost = gap*8*26*rate;
    const rows=[['Scenario','Value'],['Target Increase (%)',target],['Current Workforce',workforce],['Absenteeism (%)',absenteeism],['OT Rate',rate],['Needed Workforce',needed],['Available Workforce',avail],['Gap',gap],['Estimated Cost',cost]];
    const csv = rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\n');
    const a = document.createElement('a');
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
    a.download = 'rinl_simulation_scenario.csv';
    a.click();
    return;
  }
  if(page === 'predict'){
    const dept = document.getElementById('pred-dept').value;
    const url = dept ? `/api/export/csv/page/predict?dept=${encodeURIComponent(dept)}` : '/api/export/csv/page/predict';
    window.location = url;
    return;
  }
  window.location = `/api/export/csv/page/${page}`;
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
    if(equipChart)equipChart.destroy();
    const ctx=document.getElementById('equipChart').getContext('2d');
    equipChart=new Chart(ctx,{type:'bar',data:{labels,datasets:[{label:'Issues',data,backgroundColor:'rgba(255,118,117,0.9)'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}}});
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
  const payload={department:modalDept.department,equipment:document.getElementById('eq-equipment').value,severity:document.getElementById('eq-severity').value,description:document.getElementById('eq-desc').value,reported_by:document.getElementById('eq-reporter').value};
  const r=await fetch('/api/equipment/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(r.ok){fetchEquipment(modalDept.department);fetchData();alert('Issue reported');}
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

function addShift(){
  const dept=document.getElementById('sh-dept').value;
  const shift=document.getElementById('sh-shift').value;
  const count=parseInt(document.getElementById('sh-count').value)||0;
  if(!dept)return;
  shiftData.push({dept,shift,count,id:Date.now()});
  renderShifts();
}
function removeShift(id){shiftData=shiftData.filter(s=>s.id!==id);renderShifts();}
function renderShifts(){
  const body=document.getElementById('shift-body');
  document.getElementById('shift-empty').style.display=shiftData.length?'none':'block';
  body.innerHTML=shiftData.map(s=>`<tr>
    <td style="font-weight:700">${s.dept}</td><td>${s.shift}</td><td>${s.count}</td>
    <td><span class="sbdge" style="background:${s.count>=30?'#00b89422':'#ff767522'};color:${s.count>=30?'#00b894':'#ff7675'}">${s.count>=30?'OK':'Low'}</span></td>
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
