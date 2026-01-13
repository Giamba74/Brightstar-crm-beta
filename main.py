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
import json
import copy
import time

# --- 1. CONFIGURAZIONE & DESIGN ---
st.set_page_config(page_title="Brightstar CRM PRO", page_icon="💎", layout="wide")
TZ_ITALY = pytz.timezone('Europe/Rome')

st.markdown("""
    <style>
    .stApp { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); font-family: 'Segoe UI', sans-serif; color: #e2e8f0; }
    .meteo-card { padding: 15px; border-radius: 12px; color: white; margin-bottom: 25px; text-align: center; font-weight: bold; border: 1px solid rgba(255,255,255,0.2); }
    .client-card { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 16px; padding: 20px; margin-bottom: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; }
    .client-name { font-size: 1.4rem; font-weight: 700; color: #f8fafc; }
    .arrival-time { background: linear-gradient(90deg, #3b82f6, #2563eb); color: white; padding: 4px 12px; border-radius: 20px; font-weight: bold; }
    .strategy-box { padding: 10px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9em; color: white; border-left: 4px solid; background: rgba(0,0,0,0.2); }
    .info-row { display: flex; gap: 15px; color: #94a3b8; font-size: 0.9rem; margin-bottom: 5px; }
    .highlight { color: #38bdf8; font-weight: 600; }
    .real-traffic { color: #f59e0b; font-size: 0.8rem; font-style: italic; }
    .ai-badge { font-size: 0.75rem; background-color: #334155; color: #cbd5e1; padding: 2px 8px; border-radius: 4px; }
    .forced-badge { font-size: 0.8rem; color: #fbbf24; font-weight: bold; border: 1px solid #fbbf24; padding: 2px 6px; border-radius: 4px; margin-right: 10px;}
    .prem-badge { font-size: 0.8rem; color: #a855f7; font-weight: bold; border: 1px solid #a855f7; padding: 2px 6px; border-radius: 4px; margin-right: 5px;}
    .stCheckbox label { color: #e2e8f0 !important; font-weight: 500; }
    .streamlit-expanderHeader { background-color: rgba(255,255,255,0.05) !important; color: white !important; border-radius: 8px; }
    .stButton button { width: 100%; border-radius: 8px; font-weight: bold; transition: all 0.2s; }
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

@st.cache_resource
def connect_db():
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
        client = gspread.authorize(creds)
        sh = client.open_by_key(ID_DEL_FOGLIO)
        ws_main = sh.get_worksheet(0)
        
        ws_log = None
        if "LOG_AI" in [w.title for w in sh.worksheets()]:
             ws_log = sh.worksheet("LOG_AI")
        
        ws_mem = None
        if "MEMORIA_GIRO" in [w.title for w in sh.worksheets()]:
             ws_mem = sh.worksheet("MEMORIA_GIRO")
             # Inizializza Headers se vuoto
             if not ws_mem.acell("A1").value:
                 ws_mem.update_acell("A1", "DATA"); ws_mem.update_acell("B1", "JSON_DATA")
                 ws_mem.update_acell("D1", "DB_CLIENTE"); ws_mem.update_acell("E1", "DB_TASKS")
        
        return ws_main, ws_log, ws_mem
    except Exception as e:
        st.error(f"Errore DB: {e}")
        return None, None, None

# --- GESTIONE MEMORIA PERSISTENTE ---
def salva_giro_solo_rotta(sh_memoria, rotta_data):
    try:
        dati_export = copy.deepcopy(rotta_data)
        now_str = datetime.now(TZ_ITALY).strftime("%d-%m-%Y") # Formato Europeo
        for p in dati_export:
            if isinstance(p.get('arr'), datetime): p['arr'] = p['arr'].strftime("%Y-%m-%d %H:%M:%S")
        
        json_dump = json.dumps(dati_export)
        sh_memoria.update_acell("A2", now_str)
        time.sleep(0.5) 
        sh_memoria.update_acell("B2", json_dump)
    except: pass 

def carica_giro_da_foglio(sh_memoria):
    try:
        # Legge la memoria senza preoccuparsi della data
        json_data = sh_memoria.acell("B2").value
        
        if json_data:
            # Abbiamo trovato dei dati! Li carichiamo sempre.
            rotta = json.loads(json_data)
            for p in rotta:
                if p.get('arr'): p['arr'] = datetime.strptime(p['arr'], "%Y-%m-%d %H:%M:%S")
                if 'tasks_completed' not in p: p['tasks_completed'] = []
            return rotta
    except: pass
    return None

def resetta_solo_rotta(sh_memoria):
    try: sh_memoria.batch_clear(["A2:B2"])
    except: pass

def carica_storico_attivita(sh_memoria):
    try:
        raw = sh_memoria.get("D:E") 
        db_tasks = {}
        if not raw: return {}
        for row in raw[1:]: 
            if len(row) >= 2: db_tasks[row[0]] = json.loads(row[1])
        return db_tasks
    except: return {}

def aggiorna_attivita_cliente(sh_memoria, cliente, tasks_list):
    try:
        records = sh_memoria.get_all_values()
        row_idx = -1
        for i, row in enumerate(records):
            if len(row) > 3 and row[3] == cliente:
                row_idx = i + 1; break
        
        json_tasks = json.dumps(tasks_list)
        if row_idx != -1: sh_memoria.update_cell(row_idx, 5, json_tasks)
        else:
            col_d = sh_memoria.col_values(4)
            next_row = len(col_d) + 1
            sh_memoria.update_cell(next_row, 4, cliente)
            sh_memoria.update_cell(next_row, 5, json_tasks)
        st.toast(f"Dati Salvati: {cliente}", icon="💾")
    except: st.error("Errore Salvataggio Parziale (Server Busy)")

def pulisci_attivita_cliente(sh_memoria, cliente):
    try:
        records = sh_memoria.get_all_values()
        row_idx = -1
        for i, row in enumerate(records):
            if len(row) > 3 and row[3] == cliente:
                row_idx = i + 1; break
        if row_idx != -1:
            sh_memoria.update_cell(row_idx, 4, "")
            sh_memoria.update_cell(row_idx, 5, "")
    except: pass

# --- CORE FUNCTIONS ---
def agente_strategico(note_precedenti):
    if not note_precedenti: return "ℹ️ COACH: Nessuno storico recente. Raccogli info.", "border-left-color: #64748b;"
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
        needs_auto = False
        details = []
        for i, z in enumerate(res):
            nome = ["Chianti", "Firenze", "Arezzo"][i]
            rain_prob = max(z['hourly']['precipitation_probability'][9:18])
            temp_media = sum(z['hourly']['temperature_2m'][9:18]) / 9
            details.append(f"{nome}: {int(temp_media)}°C/Pioggia {rain_prob}%")
            if rain_prob > 25 or temp_media < 3: needs_auto = True
        
        veicolo = "AUTO 🚗" if needs_auto else "ZONTES 350 🛵"
        msg = f"{veicolo} (Algoritmo Meteo)<br><span style='font-size:0.8em; font-weight:normal'>{', '.join(details)}</span>"
        style = "background: linear-gradient(90deg, #b91c1c, #ef4444);" if needs_auto else "background: linear-gradient(90deg, #15803d, #22c55e);"
        return msg, style
    except: return "METEO N/D", "background: #64748b;"

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
                return {"coords": (r['geometry']['location']['lat'], r['geometry']['location']['lng']), "tel": det.get('result', {}).get('formatted_phone_number', ''), "found": True}
        except: continue
    return None

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
        now = datetime.now(TZ_ITALY)
        ws_log.append_row([cliente, now.strftime("%d-%m-%Y"), now.strftime("%H:%M"), durata, note_extra])

# --- APP START ---
ws, ws_ai, ws_mem = connect_db()

if ws:
    data = ws.get_all_values()
    df = pd.DataFrame(data[1:], columns=[h.strip().upper() for h in data[0]])
    c_nom = next(c for c in df.columns if "CLIENTE" in c)
    c_ind = next(c for c in df.columns if "INDIRIZZO" in c or "VIA" in c)
    c_com = next(c for c in df.columns if "COMUNE" in c)
    c_cap = next((c for c in df.columns if "CAP" in c), "CAP")
    c_vis = next(c for c in df.columns if "VISITATO" in c)
    
    if "TELEFONO" in df.columns: c_tel = "TELEFONO"
    else: c_tel = next((c for c in df.columns if "TELEFONO" in c or "CELL" in c or "TEL" in c), "TELEFONO")
    if c_tel in df.columns: df[c_tel] = df[c_tel].astype(str).replace('nan', '').replace('None', '')

    c_att = next((c for c in df.columns if "ATTIVIT" in c), None)
    c_canv = next((c for c in df.columns if "CANVASS" in c or "PROMO" in c), None)
    c_note_sto = next((c for c in df.columns if "STORICO" in c or "NOTE" in c), None)
    c_prem = next((c for c in df.columns if "PREMIUM" in c), None)

    if "CAP" in df.columns: df[c_cap] = df[c_cap].astype(str).str.replace('.0','').str.zfill(5)

    # --- 🔄 AUTO-LOADING AGGRESSIVO ---
    if 'master_route' not in st.session_state and ws_mem:
        with st.spinner("🔄 Ripristino memoria..."):
            rotta_salvata = carica_giro_da_foglio(ws_mem)
            if rotta_salvata:
                st.session_state.master_route = rotta_salvata
                st.success("🔄 GIRO RECUPERATO DALLA MEMORIA (Permanente)")
    
    if 'db_tasks' not in st.session_state and ws_mem:
        st.session_state.db_tasks = carica_storico_attivita(ws_mem)

    with st.sidebar:
        st.title("💼 CRM Filters")
        indirizzo_start = st.text_input("📍 Partenza:", value="Chianti, Sede")
        st.divider()
        num_visite = st.slider("Numero visite:", 1, 15, 8)
        only_premium = st.toggle("💎 Solo Clienti PREMIUM", value=True)
        sel_zona = st.multiselect("Zona", sorted(df[c_com].unique()))
        sel_cap = st.multiselect("CAP", sorted(df[c_cap].unique()) if c_cap in df.columns else [])
        st.divider()
        st.markdown("### ⭐ Forzature (VIP)")
        all_clients_list = sorted(df[c_nom].unique().tolist())
        sel_forced = st.multiselect("Clienti Prioritari:", all_clients_list)
        st.divider()
        if st.button("🗑️ RESETTA GIRO", type="secondary"):
             if ws_mem: resetta_solo_rotta(ws_mem)
             if 'master_route' in st.session_state: del st.session_state.master_route
             st.rerun()

    st.markdown("### 🚀 Brightstar CRM Dashboard")
    msg, style = agente_meteo_territoriale()
    col_meteo_1, col_meteo_2 = st.columns([3, 1])
    with col_meteo_1: st.markdown(f"<div class='meteo-card' style='{style}'>{msg}</div>", unsafe_allow_html=True)
    with col_meteo_2: st.link_button("🌤️ LaMMA", "https://www.lamma.rete.toscana.it/", use_container_width=True)

    if st.button("CALCOLA NUOVO GIRO", type="primary", use_container_width=True):
        if not ws_mem: st.error("Errore: Manca foglio MEMORIA_GIRO!")
        else:
            start_coords = SEDE_COORDS
            if indirizzo_start:
                with st.spinner(f"🔍 Cerco: {indirizzo_start}..."):
                    loc_data = get_google_data([indirizzo_start])
                    if loc_data and loc_data['found']: start_coords = loc_data['coords']
            
            mask_standard = ~df[c_vis].str.contains('SI|SÌ', case=False, na=False)
            if sel_zona: mask_standard &= df[c_com].isin(sel_zona)
            if sel_cap: mask_standard &= df[c_cap].isin(sel_cap)
            if only_premium and c_prem: mask_standard &= df[c_prem].astype(str).str.upper().str.contains('SI', na=False)

            df_final = pd.concat([df[df[c_nom].isin(sel_forced)], df[mask_standard]]).drop_duplicates(subset=[c_nom])
            raw = df_final.to_dict('records')
            
            if not raw: st.warning("Nessun cliente trovato.")
            else:
                with st.spinner("⏳ Ottimizzazione..."):
                    rotta = []
                    now = datetime.now(TZ_ITALY)
                    start_t = now if (7 <= now.hour < 19) else now.replace(hour=7, minute=30) + timedelta(days=(1 if now.hour>=19 else 0))
                    limit = start_t.replace(hour=19, minute=30)
                    curr_t, curr_loc, pool = start_t, start_coords, raw.copy()

                    while pool and curr_t < limit and len(rotta) < num_visite:
                        best = None; best_score = float('inf')
                        for p in pool:
                            if 'g_data' not in p:
                                p['g_data'] = get_google_data([f"{p[c_ind]}, {p[c_com]}, Italy", f"{p[c_nom]}, {p[c_com]}"]) or {'coords': None, 'found': False}
                            if not p['g_data']['found']: continue
                            dist_air = geodesic(curr_loc, p['g_data']['coords']).km
                            score = dist_air
                            if p[c_nom] in sel_forced: score -= 100
