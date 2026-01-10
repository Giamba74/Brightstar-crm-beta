import streamlit as st
import pandas as pd
import numpy as np
from geopy.distance import geodesic
from datetime import datetime, timedelta
import urllib.parse
import requests
import gspread
from google.oauth2.service_account import Credentials
import pytz

# --- 1. CONFIGURAZIONE & DESIGN ---
st.set_page_config(page_title="Brightstar CRM PRO", page_icon="💼", layout="wide")
TZ_ITALY = pytz.timezone('Europe/Rome')

st.markdown("""
    <style>
    .stApp { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); font-family: 'Segoe UI', sans-serif; color: #e2e8f0; }
    
    /* Meteo */
    .meteo-card { padding: 15px; border-radius: 12px; color: white; margin-bottom: 25px; text-align: center; font-weight: bold; border: 1px solid rgba(255,255,255,0.2); }
    
    /* Card Cliente CRM */
    .client-card { 
        background: rgba(30, 41, 59, 0.7); 
        backdrop-filter: blur(10px); 
        border: 1px solid rgba(255, 255, 255, 0.1); 
        border-radius: 16px; 
        padding: 20px; 
        margin-bottom: 5px; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; }
    .client-name { font-size: 1.4rem; font-weight: 700; color: #f8fafc; }
    .arrival-time { background: linear-gradient(90deg, #3b82f6, #2563eb); color: white; padding: 4px 12px; border-radius: 20px; font-weight: bold; }
    
    /* Box Agenti */
    .strategy-box { padding: 10px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; color: white; border-left: 4px solid; background: rgba(0,0,0,0.2); }
    
    .info-row { display: flex; gap: 15px; color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px; }
    .highlight { color: #38bdf8; font-weight: 600; }
    .real-traffic { color: #f59e0b; font-size: 0.8rem; font-style: italic; }
    .ai-badge { font-size: 0.75rem; background-color: #334155; color: #cbd5e1; padding: 2px 8px; border-radius: 4px; }
    .forced-badge { font-size: 0.8rem; color: #fbbf24; font-weight: bold; border: 1px solid #fbbf24; padding: 2px 6px; border-radius: 4px; margin-right: 10px;}

    .stCheckbox label { color: #e2e8f0 !important; font-weight: 500; }
    .streamlit-expanderHeader { background-color: rgba(255,255,255,0.05) !important; color: white !important; border-radius: 8px; }
    </style>
    """, unsafe_allow_html=True)

# --- DATI ---
COORDS = { "Chianti": (43.661888, 11.305728), "Firenze": (43.7696, 11.2558), "Arezzo": (43.4631, 11.8781) }
SEDE_COORDS = COORDS["Chianti"]
API_KEY = st.secrets.get("GOOGLE_MAPS_API_KEY")

# ==============================================================================
# 👇 MODIFICA SOLO QUI SOTTO CON IL TUO ID FOGLIO GOOGLE 👇
ID_DEL_FOGLIO = "1E9Fv9xOvGGumWGB7MjhAMbV5yzOqPtS1YRx-y4dypQ0" 
# ==============================================================================

# --- AGENTI INTELLIGENTI ---
def agente_strategico(note_precedenti):
    if not note_precedenti: 
        return "ℹ️ COACH: Nessuno storico recente. Raccogli info.", "border-left-color: #64748b;"
    txt = str(note_precedenti).lower()
    if any(x in txt for x in ['arrabbiato', 'reclamo', 'ritardo', 'problema', 'rotto']):
        return "🛡️ COACH: Cliente a rischio. Empatia massima.", "border-left-color: #f87171; background: rgba(153, 27, 27, 0.2);"
    if any(x in txt for x in ['prezzo', 'costoso', 'sconto', 'caro']):
        return "💎 COACH: Difendi il valore. Non svendere.", "border-left-color: #fb923c; background: rgba(146, 64, 14, 0.2);"
    if any(x in txt for x in ['interessato', 'preventivo', 'forse']):
        return "🎯 COACH: È caldo! Oggi devi chiudere.", "border-left-color: #4ade80; background: rgba(22, 101, 52, 0.2);"
    return f"ℹ️ MEMO: {note_precedenti[:60]}...", "border-left-color: #94a3b8;"

def agente_meteo_territoriale():
    try:
        lats, lons = f"{COORDS['Chianti'][0]},{COORDS['Firenze'][0]},{COORDS['Arezzo'][0]}", f"{COORDS['Chianti'][1]},{COORDS['Firenze'][1]},{COORDS['Arezzo'][1]}"
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}&hourly=temperature_2m,precipitation_probability&timezone=Europe%2FRome&forecast_days=1"
        res = requests.get(url).json()
        res = res if isinstance(res, list) else [res]
        bad_weather = False
        details = []
        for i, z in enumerate(res):
            nome = ["Chianti", "Firenze", "Arezzo"][i]
            rain = max(z['hourly']['precipitation_probability'][9:18])
            temp = sum(z['hourly']['temperature_2m'][9:18]) / 9
            details.append(f"{nome}: {int(temp)}°C/Pioggia {rain}%")
            if rain > 30 or temp < 8: bad_weather = True
        msg = f"AUTO 🚗 (Meteo Incerto: {', '.join(details)})" if bad_weather else "ZONTES 350 🛵 (Via Libera!)"
        style = "background: linear-gradient(90deg, #b91c1c, #ef4444);" if bad_weather else "background: linear-gradient(90deg, #15803d, #22c55e);"
        return msg, style
    except: return "METEO N/D", "background: #64748b;"

# --- FUNZIONI CORE ---
def get_real_travel_time(origin_coords, dest_coords):
    if not API_KEY: 
        dist = geodesic(origin_coords, dest_coords).km
        return int(((dist * 1.5) / 40) * 60)
    try:
        url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={origin_coords[0]},{origin_coords[1]}&destinations={dest_coords[0]},{dest_coords[1]}&departure_time=now&mode=driving&key={API_KEY}"
        res = requests.get(url).json()
        if res['status'] == 'OK' and res['rows'][0]['elements'][0]['status'] == 'OK':
            seconds = res['rows'][0]['elements'][0]['duration_in_traffic']['value']
            return int(seconds / 60)
    except: pass
    dist = geodesic(origin_coords, dest_coords).km
    return int(((dist * 1.5) / 45) * 60)

def get_google_data(query_list):
    if not API_KEY: return None
    for q in query_list:
        try:
            res = requests.get(f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={urllib.parse.quote(q)}&key={API_KEY}").json()
            if res.get('results'):
                r = res['results'][0]
                pid = r['place_id']
                det = requests.get(f"https://maps.googleapis.com/maps/api/place/details/json?place_id={pid}&fields=opening_hours,formatted_phone_number&key={API_KEY}").json()
                return {
                    "coords": (r['geometry']['location']['lat'], r['geometry']['location']['lng']),
                    "tel": det.get('result', {}).get('formatted_phone_number', ''),
                    "periods": det.get('result', {}).get('opening_hours', {}).get('periods', []),
                    "found": True
                }
        except: continue
    return None

@st.cache_resource
def connect_db():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(ID_DEL_FOGLIO)
        return sh.get_worksheet(0), sh.worksheet("LOG_AI") if "LOG_AI" in [w.title for w in sh.worksheets()] else None
    except: return None, None

def get_ai_duration(ws_log, cliente):
    if not ws_log: return 20, False
    try:
        df = pd.DataFrame(ws_log.get_all_records())
        if not df.empty:
            hist = df[df['CLIENTE'] == cliente]
            if not hist.empty: return int(hist['DURATA_MIN'].mean()), True
    except: pass
    return 20, False

def log_visit(ws_log, cliente, durata, note_extra=""):
    if ws_log:
        if not ws_log.get_all_values(): ws_log.append_row(["CLIENTE", "DATA", "ORA", "DURATA_MIN", "NOTE_ATTIVITA"])
        now = datetime.now(TZ_ITALY)
        ws_log.append_row([cliente, now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), durata, note_extra])

# --- INTERFACCIA ---
ws, ws_ai = connect_db()

if ws:
    data = ws.get_all_values()
    df = pd.DataFrame(data[1:], columns=[h.strip().upper() for h in data[0]])
    c_nom = next(c for c in df.columns if "CLIENTE" in c)
    c_ind = next(c for c in df.columns if "INDIRIZZO" in c or "VIA" in c)
    c_com = next(c for c in df.columns if "COMUNE" in c)
    c_cap = next((c for c in df.columns if "CAP" in c), "CAP")
    c_vis = next(c for c in df.columns if "VISITATO" in c)
    c_tel = next((c for c in df.columns if "TELEFONO" in c), "TELEFONO")
    c_att = next((c for c in df.columns if "ATTIVIT" in c), None)
    c_canv = next((c for c in df.columns if "CANVASS" in c or "PROMO" in c), None)
    c_note_sto = next((c for c in df.columns if "STORICO" in c or "NOTE" in c), None)
    
    if c_cap in df.columns: df[c_cap] = df[c_cap].astype(str).str.replace('.0','').str.zfill(5)

    with st.sidebar:
        st.title("💼 CRM Filters")
        sel_zona = st.multiselect("Zona", sorted(df[c_com].unique()))
        sel_cap = st.multiselect("CAP", sorted(df[c_cap].unique()) if c_cap in df.columns else [])
        st.divider()
        st.markdown("### ⭐ Forzature (VIP)")
        all_clients = sorted(df[c_nom].unique().tolist())
        sel_forced = st.multiselect("Clienti Prioritari:", all_clients)

    st.markdown("### 🚀 Brightstar CRM Dashboard")
    msg, style = agente_meteo_territoriale()
    st.markdown(f"<div class='meteo-card' style='{style}'>{msg}</div>", unsafe_allow_html=True)

    if st.button("CALCOLA GIRO (ORARIO ITALIA)", type="primary", use_container_width=True):
        mask_standard = ~df[c_vis].str.contains('SI|SÌ', case=False, na=False)
        if sel_zona: mask_standard &= df[c_com].isin(sel_zona)
        if sel_cap: mask_standard &= df[c_cap].isin(sel_cap)
        mask_forced = df[c_nom].isin(sel_forced)
        df_final = pd.concat([df[mask_forced], df[mask_standard]]).drop_duplicates(subset=[c_nom])
        raw = df_final.to_dict('records')
        
        if not raw: st.warning("Nessun cliente da visitare.")
        else:
            with st.spinner("⏳ Elaborazione Strategia CRM..."):
                rotta = []
                now = datetime.now(TZ_ITALY)
                start_t = now if (7 <= now.hour < 19) else now.replace(hour=7, minute=30) + timedelta(days=(1 if now.hour>=19 else 0))
                limit = start_t.replace(hour=19, minute=30)
                curr_t, curr_loc, pool = start_t, SEDE_COORDS, raw.copy()

                while pool and curr_t < limit:
                    best = None
                    best_score = float('inf')
                    for p in pool:
                        if 'g_data' not in p:
                            p['g_data'] = get_google_data([f"{p[c_ind]}, {p[c_com]}, Italy", f"{p[c_nom]}, {p[c_com]}"]) or {'coords': None, 'found': False, 'periods': []}
                        if not p['g_data']['found']: continue
                        dist_air = geodesic(curr_loc, p['g_data']['coords']).km
                        est_arr = curr_t + timedelta(minutes=(dist_air*1.5/40)*60)
                        if est_arr > limit: continue
                        score = dist_air
                        if p[c_nom] in sel_forced: score -= 100000 
                        if c_att and p.get(c_att) and str(p[c_att]).strip(): score -= 5
                        if score < best_score: best_score, best = score, p
                    
                    if best:
                        real_mins = get_real_travel_time(curr_loc, best['g_data']['coords'])
                        arrival_real = curr_t + timedelta(minutes=real_mins)
                        if arrival_real > limit: pool.remove(best); continue
                        dur_visita, learned = get_ai_duration(ws_ai, best[c_nom])
                        best['arr'], best['travel_time'], best['duration'], best['learned'] = arrival_real, real_mins, dur_visita, learned
                        rotta.append(best); curr_t = arrival_real + timedelta(minutes=dur_visita); curr_loc = best['g_data']['coords']; pool.remove(best)
                    else: break
                st.session_state.master_route = rotta
                st.rerun()

    if 'master_route' in st.session_state:
        route = st.session_state.master_route
        end_time = route[-1]['arr'].strftime("%H:%M") if route else "--:--"
        st.caption(f"🏁 Rientro previsto: {end_time}")
        
        for i, p in enumerate(route):
            ai_lbl = "AI" if p.get('learned') else "Std"
            tel = p.get('g_data', {}).get('tel') or p.get(c_tel) or ''
            ora_str = p['arr'].strftime('%H:%M')
            note_old = p.get(c_note_sto, '') if c_note_sto else ''
            msg_coach, style_coach = agente_strategico(note_old)
            forced_html = "<span class='forced-badge'>⭐ PRIORITARIO</span>" if p[c_nom] in sel_forced else ""

            # --- BOX CANVASS VERDE SMERALDO (VISIBILE SOPRA) ---
            canvass_html = ""
            valore_canvass = p.get(c_canv, '') if c_canv else ''
            if valore_canvass and str(valore_canvass).strip():
                canvass_html = f"<div style='background:linear-gradient(90deg, #059669, #10b981); color:white; padding:10px; border-radius:8px; margin-bottom:10px; font-weight:bold; border:1px solid #34d399;'>📢 CANVASS ATTIVO: {valore_canvass}</div>"

            # --- CARD HTML ---
            html_card = f"""
<div class="client-card">
<div class="card-header">
<div style="display:flex; align-items:center;">
{forced_html}
<span class="client-name">{i+1}. {p[c_nom]}</span>
</div>
<div class="arrival-time">{ora_str}</div>
</div>
{canvass_html}
<div class="strategy-box" style="{style_coach}">
{msg_coach}
</div>
<div class="info-row">
<span>📍 {p[c_ind]}, {p[c_com]}</span>
<span class="real-traffic">🚗 Guida: {p['travel_time']} min</span>
</div>
<div class="info-row">
<span class="ai-badge">⏱️ {p['duration']} min ({ai_lbl})</span>
<span class="highlight">{tel}</span>
</div>
</div>
"""
            st.markdown(html_card, unsafe_allow_html=True)

            with st.expander("📂 Dati Completi & CRM"):
                dati_clean = {k:v for k,v in p.items() if k not in ['g_data', 'arr', 'learned', 'travel_time', 'duration', 'NOTE_SESSION']}
                st.dataframe(pd.DataFrame([dati_clean]).T, use_container_width=True)

            tasks_done = []
            if c_att and p.get(c_att):
                task_list = [t.strip() for t in str(p[c_att]).split(',') if t.strip()]
                if task_list:
                    st.markdown("**📋 Attività da svolgere:**")
                    for t_idx, task in enumerate(task_list):
                        if st.checkbox(task, key=f"chk_{i}_{t_idx}"): tasks_done.append(task)
            
            p['NOTE_SESSION'] = st.text_area(f"🎤 Esito Visita {p[c_nom]}:", value=p.get('NOTE_SESSION', ''), key=f"note_{i}", height=70)
            
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                lnk = f"https://www.google.com/maps/dir/?api=1&destination={p['g_data']['coords'][0]},{p['g_data']['coords'][1]}&travelmode=driving"
                st.link_button("🚙 NAVIGA", lnk, use_container_width=True)
            with c2:
                if tel: st.link_button("📞 CHIAMA", f"tel:{tel}", use_container_width=True)
            with c3:
                if st.button("✅ FATTO", key=f"d_{i}", use_container_width=True):
                    try:
                        cell = ws.find(p[c_nom])
                        ws.update_cell(cell.row, list(df.columns).index(c_vis)+1, "SI")
                        report_extra = (f"[ATTIVITÀ: {', '.join(tasks_done)}] " if tasks_done else "") + (f"[NOTE: {p['NOTE_SESSION']}]" if p['NOTE_SESSION'] else "")
                        log_visit(ws_ai, p[c_nom], p['duration'], report_extra)
                        st.session_state.master_route.pop(i); st.rerun()
                    except: st.error("Errore Salvataggio")

        st.divider()
        if st.button("📧 INVIA REPORT CRM", type="secondary", use_container_width=True):
            report_lines = []
            for p_rep in route:
                note = p_rep.get('NOTE_SESSION', '')
                if note: report_lines.append(f"• {p_rep[c_nom]}: {note}")
            if report_lines: st.success("Report Generato:"); st.code("\n".join(report_lines))
            else: st.warning("Nessuna nota registrata.")

