import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile
import copy
import re

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v21.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Zoneamento VIP (V21)")
st.markdown("""
**Lógica V21:**
1.  **Zoneamento VIP:** Apenas cursos estruturais ganham Sala Base Fixa.
2.  **Idiomas Nômades:** Inglês/Espanhol entram nos buracos deixados pelos VIPs.
3.  **Garantia Matemática:** Há salas suficientes para todos os VIPs simultaneamente.
""")

# --- CONSTANTES ---
DIAS = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
CURSOS_SEM_SEXTA = ['EVENTOS', 'GUIA REGIONAL', 'GUIA NACIONAL']
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]
SALAS_TEORICAS = [f"Sala {i}" for i in range(1, 13) if i != 6]
SALAS_BACKUP = ["Lab. Informática 1", "Lab. Informática 2", "Restaurante 1"]

# Palavras-chave que definem uma turma como "VIP" (Dona de Sala)
KEYWORDS_VIP = ["PANIF", "CONF", "COZINHA", "GUIA", "EVENTOS", "PATRIMONIO"]
# Palavras-chave que definem uma turma como "Nômade" (Idiomas)
KEYWORDS_NOMADE = ["INGLÊS", "INGLES", "ESPANHOL", "LIBRAS", "CONVERSAÇÃO", "CONVERSACAO"]

# --- FUNÇÕES AUXILIARES ---
def gerar_template():
    df = pd.DataFrame(columns=[
        "ID_Turma", "Nome_UC", "Turno", "Docentes", "Espacos", 
        "Tipo_Alocacao", "Carga_Horaria_Total", "Regra_Especial", 
        "Dia_Travado", "Semana_Inicio", "Semana_Fim"
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demandas', index=False)
    return buffer

def converter_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

# --- CLASSE DO MOTOR ---
class MotorAlocacao:
    def __init__(self, df_demandas, df_docentes):
        self.demandas = df_demandas.fillna("")
        self.restricoes = df_docentes.fillna("")
        self.grade = []
        self.erros = []
        self.matriz = {} 
        self.sala_base = {} 

    def normalizar(self, texto):
        return str(texto).strip().upper()

    def verificar_conflito(self, recursos, dia, turno, sem_ini, sem_fim):
        for rec in recursos:
            rec_norm = self.normalizar(rec)
            for sem in range(sem_ini, sem_fim + 1):
                chave = f"{rec_norm}|{dia}|{turno}|{sem}"
                if self.matriz.get(chave): return True
        return False

    def reservar(self, recursos, dia, turno, sem_ini, sem_fim):
        for rec in recursos:
            rec_norm = self.normalizar(rec)
            for sem in range(sem_ini, sem_fim + 1):
                chave = f"{rec_norm}|{dia}|{turno}|{sem}"
                self.matriz[chave] = True

    def buscar_sala(self, turma, dia, turno, sem_ini, sem_fim, modo_nomade=False):
        # 1. Se for VIP, tenta Sala Base
        if not modo_nomade:
            base = self.sala_base.get(turma)
            if base and not self.verificar_conflito([base], dia, turno, sem_ini, sem_fim):
                return base
        
        # 2. Se for Nômade (ou VIP sem base), tenta qualquer Teórica
        for sala in SALAS_TEORICAS:
            if not self.verificar_conflito([sala], dia, turno, sem_ini, sem_fim):
                return sala
                
        # 3. Backups
        for sala in SALAS_BACKUP:
            if not self.verificar_conflito([sala], dia, turno, sem_ini, sem_fim):
                return sala
                
        return None

    def verificar_bloqueio_docente(self, docente, dia, turno, sem_ini, sem_fim):
        try:
            regra = self.restricoes[self.restricoes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return True
                
                if 'Bloqueio_Semana_Inicio' in regra.columns:
                    b_ini = int(regra.iloc[0]['Bloqueio_Semana_Inicio'] or 0)
                    b_fim = int(regra.iloc[0]['Bloqueio_Semana_Fim'] or 0)
                    if b_ini > 0 and b_fim > 0:
                        if not (sem_fim < b_ini or sem_ini > b_fim): return True
                
                obs = str(regra.iloc[0]['Restricoes_Extras']).lower()
                if "licença" in obs and sem_fim > 15: return True
        except: pass
        return False

    def otimizar_dados_entrada(self):
        df = self.demandas.copy()
        def limpar_nome(nome):
            return re.sub(r'\s*\(parte \d+\)', '', str(nome), flags=re.IGNORECASE).strip()
        df['Nome_Base'] = df['Nome_UC'].apply(limpar_nome)
        grupos = df.groupby(['ID_Turma', 'Nome_Base'])
        novas_demandas = []
        for (turma, nome), grupo in grupos:
            if len(grupo) > 1 and "PROEJA" in turma: 
                ch_total = grupo['Carga_Horaria_Total'].sum()
                if ch_total > 80: ch_total = 80
                docentes_concat = ", ".join(grupo['Docentes'].unique())
                espacos_concat = " + ".join(grupo['Espacos'].unique())
                espacos_unicos = list(set([e.strip() for e in espacos_concat.split('+')]))
                espacos_final = " + ".join(espacos_unicos)
                item = grupo.iloc[0].to_dict()
                item['Nome_UC'] = nome 
                item['Carga_Horaria_Total'] = ch_total
                item['Docentes'] = docentes_concat
                item['Espacos'] = espacos_final
                item['Tipo'] = "FUSAO"
                novas_demandas.append(item)
            else:
                for _, row in grupo.iterrows():
                    row['Tipo'] = "SIMPLE"
                    novas_demandas.append(row.to_dict())
        return novas_demandas

    def definir_zoneamento(self):
        """
        CAMADA 1: Atribui Sala Base APENAS para Turmas VIP
        """
        turmas = self.demandas['ID_Turma'].unique()
        
        # Filtro VIP Rigoroso
        turmas_vip = []
        for t in turmas:
            t_upper = str(t).upper()
            eh_vip = any(k in t_upper for k in KEYWORDS_VIP)
            eh_nomade = any(k in t_upper for k in KEYWORDS_NOMADE)
            if eh_vip and not eh_nomade:
                turmas_vip.append(t)
        
        # Distribui salas sequencialmente para os VIPs
        # Reinicia contagem de salas para cada turno para otimizar? 
        # Não, melhor garantir sala exclusiva global para simplificar (temos salas suficientes)
        # Ou melhor: Sala 1 é do Proeja Panif 1 (Noturno) E da Confeitaria 2 (Matutino).
        
        # Mapa: Turno -> Lista de Turmas VIP
        vip_por_turno = {'Matutino': [], 'Vespertino': [], 'Noturno': []}
        
        # Tenta adivinhar o turno da turma (pega o turno da maioria das UCs)
        for t in turmas_vip:
            mode_turno = self.demandas[self.demandas['ID_Turma'] == t]['Turno'].mode()[0]
            if mode_turno in vip_por_turno:
                vip_por_turno[mode_turno].append(t)
            else:
                vip_por_turno['Noturno'].append(t) # Default

        # Aloca Salas por Turno (Reutilizando salas em turnos diferentes)
        for turno, lista_turmas in vip_por_turno.items():
            idx_sala = 0
            for turma in lista_turmas:
                if idx_sala < len(SALAS_TEORICAS):
                    self.sala_base[turma] = SALAS_TEORICAS[idx_sala]
                    idx_sala += 1
                else:
                    idx_bkp = (idx_sala - len(SALAS_TEORICAS)) % len(SALAS_BACKUP)
                    self.sala_base[turma] = SALAS_BACKUP[idx_bkp]
                    idx_sala += 1

    def preparar_demandas(self):
        lista_bruta = self.otimizar_dados_entrada()
        lista_final = []
        df_temp = pd.DataFrame(lista_bruta)
        turmas = df_temp['ID_Turma'].unique()
        for turma in turmas:
            ucs_turma = [d for d in lista_bruta if d['ID_Turma'] == turma]
            ucs_60 = [d for d in ucs_turma if d['Carga_Horaria_Total'] == 60]
            ucs_20 = [d for d in ucs_turma if d['Carga_Horaria_Total'] == 20]
            outras = [d for d in ucs_turma if d['Carga_Horaria_Total'] not in [20, 60]]
            while ucs_60 and ucs_20:
                u60 = ucs_60.pop(0)
                u20 = ucs_20.pop(0)
                item = {
                    "Tipo": "PAREO", "Componentes": [u60, u20],
                    "ID_Turma": turma, "Nome_UC": f"{u60['Nome_UC']} + {u20['Nome_UC']}",
                    "Turno": u60['Turno'], "Dia_Travado": u60['Dia_Travado'] or u20['Dia_Travado'],
                    "Semana_Inicio": u60['Semana_Inicio'], "Regra_Especial": u60['Regra_Especial']
                }
                lista_final.append(item)
            for u in ucs_60 + ucs_20 + outras:
                lista_final.append(u)
        return lista_final

    def simular_alocacao(self, item, dia, forcar_sexta=False, modo_nomade=False):
        # Bypass EAD
        espacos_str = str(item.get('Espacos', '')).upper()
        if "EAD" in espacos_str or "100% EAD" in str(item.get('Regra_Especial', '')).upper():
            ch = float(item.get('Carga_Horaria_Total', 0) or 0)
            return (True, ch, {
                "rec": [str(item['ID_Turma'])], "sem_ini": 1, "sem_fim": 20, 
                "sala": "EAD", "ch_total": ch, "meta": ch, "is_ead": True
            })

        eh_curso_sem_sexta = any(c in str(item['ID_Turma']).upper() for c in CURSOS_SEM_SEXTA)
        if dia == 'Sexta-Feira' and eh_curso_sem_sexta and not forcar_sexta:
            return (False, 0, None)

        if item['Tipo'] == "PAREO":
            u1, u2 = item['Componentes'][0], item['Componentes'][1]
            ordens = [[(u1, 15), (u2, 5)], [(u2, 5), (u1, 15)]]
            for config in ordens:
                p1, dur1 = config[0]
                p2, dur2 = config[1]
                inicio_base = 1
                if item['Semana_Inicio']: inicio_base = int(item['Semana_Inicio'])
                if "FIC" in str(item['ID_Turma']): inicio_base = 4
                inicio_real = 2 if (inicio_base == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']) else inicio_base
                for shift in range(15):
                    s1_ini, s1_fim = inicio_real + shift, inicio_real + shift + dur1 - 1
                    s2_ini, s2_fim = s1_fim + 1, s1_fim + dur2
                    if s2_fim > 22: continue
                    
                    docs1 = [d.strip() for d in str(p1['Docentes']).split(',')]
                    if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s1_ini, s1_fim) for d in docs1): continue
                    
                    esp1 = str(p1['Espacos'])
                    sala1 = ""
                    if "Sala Teórica" in esp1: 
                        sala1 = self.buscar_sala(item['ID_Turma'], dia, item['Turno'], s1_ini, s1_fim, modo_nomade)
                    elif any(l in esp1 for l in LABS_AB) and s1_ini < 4:
                        sala1 = self.buscar_sala(item['ID_Turma'], dia, item['Turno'], s1_ini, min(3, s1_fim), modo_nomade)
                    
                    if ("Sala Teórica" in esp1 or (any(l in esp1 for l in LABS_AB) and s1_ini < 4)) and not sala1: continue

                    rec1 = docs1 + [str(item['ID_Turma'])] + ([sala1] if sala1 else [])
                    if any(l in esp1 for l in LABS_AB) and s1_fim >= 4: rec1 += [e.strip() for e in esp1.split('+') if e.strip() in LABS_AB]
                    if self.verificar_conflito(rec1, dia, item['Turno'], s1_ini, s1_fim): continue

                    docs2 = [d.strip() for d in str(p2['Docentes']).split(',')]
                    if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s2_ini, s2_fim) for d in docs2): continue
                    
                    esp2 = str(p2['Espacos'])
                    sala2 = ""
                    if "Sala Teórica" in esp2:
                        sala2 = self.buscar_sala(item['ID_Turma'], dia, item['Turno'], s2_ini, s2_fim, modo_nomade)
                    
                    if "Sala Teórica" in esp2 and not sala2: continue

                    rec2 = docs2 + [str(item['ID_Turma'])] + ([sala2] if sala2 else [])
                    if any(l in esp2 for l in LABS_AB) and s2_fim >= 4: rec2 += [e.strip() for e in esp2.split('+') if e.strip() in LABS_AB]
                    if self.verificar_conflito(rec2, dia, item['Turno'], s2_ini, s2_fim): continue

                    return (True, 80, {
                        "p1": p1, "s1_ini": s1_ini, "s1_fim": s1_fim, "rec1": rec1, "sala1": sala1,
                        "p2": p2, "s2_ini": s2_ini, "s2_fim": s2_fim, "rec2": rec2, "sala2": sala2
                    })
        else: # SIMPLE
            ch_total = float(item['Carga_Horaria_Total'] or 0)
            dur_ideal = int(np.ceil(ch_total / 4))
            meta_presencial = ch_total
            if "80%" in str(item['Regra_Especial']):
                meta_presencial = ch_total * 0.8
                dur_ideal = int(np.ceil(meta_presencial / 4))
            inicio_base = 1
            if item['Semana_Inicio']: inicio_base = int(item['Semana_Inicio'])
            if "FIC" in str(item['ID_Turma']): inicio_base = 4
            inicio_real = 2 if (inicio_base == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']) else inicio_base
            for shift in range(15):
                sem_ini = inicio_real + shift
                sem_fim = sem_ini + dur_ideal - 1
                if sem_fim > 22: sem_fim = 22
                dur_real = sem_fim - sem_ini + 1
                if dur_real <= 0: break
                docs = [d.strip() for d in str(item['Docentes']).split(',')]
                if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], sem_ini, sem_fim) for d in docs): continue
                
                esp = str(item['Espacos'])
                sala = ""
                rec = docs + [str(item['ID_Turma'])]
                
                if "Sala Teórica" in esp:
                    sala = self.buscar_sala(item['ID_Turma'], dia, item['Turno'], sem_ini, sem_fim, modo_nomade)
                    if not sala: continue 
                    rec.append(sala)
                elif any(l in esp for l in LABS_AB):
                    if sem_ini < 4:
                        sala = self.buscar_sala(item['ID_Turma'], dia, item['Turno'], sem_ini, min(3, sem_fim), modo_nomade)
                        if not sala: continue
                        rec.append(sala)
                    if sem_fim >= 4: rec += [e.strip() for e in esp.split('+') if e.strip() in LABS_AB]
                
                if not self.verificar_conflito(rec, dia, item['Turno'], sem_ini, sem_fim):
                    ch_presencial = dur_real * 4
                    return (True, ch_presencial, {
                        "rec": rec, "sem_ini": sem_ini, "sem_fim": sem_fim, "sala": sala, "ch_total": ch_total, "meta": meta_presencial
                    })
        return (False, 0, None)

    def executar(self):
        self.definir_zoneamento()
        lista_demandas = self.preparar_demandas()
        
        # Separação VIP vs Nômade
        vips = []
        nomades = []
        
        for item in lista_demandas:
            t_upper = str(item['ID_Turma']).upper()
            eh_nomade = any(k in t_upper for k in KEYWORDS_NOMADE)
            if eh_nomade: nomades.append(item)
            else: vips.append(item)
            
        # Ordena VIPs (Pareados primeiro)
        vips.sort(key=lambda x: 0 if x['Tipo'] == "PAREO" else 1)
        
        total = len(lista_demandas)
        bar = st.progress(0)
        repescagem = []

        # FASE 1: VIPs (Usam Sala Base)
        for idx, item in enumerate(vips):
            melhor = (False, -1, None, None)
            dias_teste = DIAS
            if item['Dia_Travado']: dias_teste = [item['Dia_Travado']]

            for dia in dias_teste:
                sucesso, ch, config = self.simular_alocacao(item, dia, modo_nomade=False)
                if sucesso:
                    score = ch
                    s_ini = config.get('s1_ini', config.get('sem_ini', 99))
                    if s_ini <= 2: score += 50 
                    if score > melhor[1]: melhor = (True, score, dia, config)
            
            if melhor[0]:
                self.aplicar_resultado(item, melhor[2], melhor[3])
            else:
                if item['Tipo'] == "PAREO":
                    repescagem.append(copy.deepcopy(item['Componentes'][0]) | {"Tipo": "SIMPLE"})
                    repescagem.append(copy.deepcopy(item['Componentes'][1]) | {"Tipo": "SIMPLE"})
                else:
                    repescagem.append(copy.deepcopy(item))
            bar.progress((idx + 1) / total)

        # FASE 2: NÔMADES + REPESCAGEM (Usam qualquer sala)
        fila_final = nomades + repescagem
        for item in fila_final:
            melhor = (False, -1, None, None)
            dias_teste = DIAS
            if item.get('Dia_Travado'): dias_teste = [item['Dia_Travado']]
            
            for dia in dias_teste:
                sucesso, ch, config = self.simular_alocacao(item, dia, modo_nomade=True) 
                if sucesso:
                    score = ch
                    s_ini = config.get('sem_ini', 99)
                    if s_ini <= 2: score += 50
                    if score > melhor[1]: melhor = (True, score, dia, config)
            
            if not melhor[0]:
                sucesso, ch, config = self.simular_alocacao(item, 'Sexta-Feira', forcar_sexta=True, modo_nomade=True)
                if sucesso: melhor = (True, ch, 'Sexta-Feira', config)

            if melhor[0]:
                self.aplicar_resultado(item, melhor[2], melhor[3], " (Flex)")
            else:
                # Tetris Final
                ch_total = float(item.get('Carga_Horaria_Total', 0) or 0)
                split_ok = False
                if ch_total >= 40:
                    for d1 in DIAS:
                        if d1 == 'Sexta-Feira' and any(c in str(item['ID_Turma']) for c in CURSOS_SEM_SEXTA): continue
                        for s1_ini in [17, 13, 9, 5, 1]: 
                            s1_fim = s1_ini + 3
                            if s1_fim > 22: continue
                            docs = [d.strip() for d in str(item['Docentes']).split(',')]
                            if any(self.verificar_bloqueio_docente(d, d1, item['Turno'], s1_ini, s1_fim) for d in docs): continue
                            sala1 = self.buscar_sala(item['ID_Turma'], d1, item['Turno'], s1_ini, s1_fim, modo_nomade=True)
                            if not sala1: continue
                            rec1 = docs + [str(item['ID_Turma']), sala1]
                            if self.verificar_conflito(rec1, d1, item['Turno'], s1_ini, s1_fim): continue
                            
                            for d2 in DIAS:
                                if d2 == 'Sexta-Feira' and any(c in str(item['ID_Turma']) for c in CURSOS_SEM_SEXTA): continue
                                for s2_ini in [17, 13, 9, 5, 1]:
                                    s2_fim = s2_ini + 3
                                    if s2_fim > 22: continue
                                    if d1 == d2 and not (s2_ini > s1_fim or s2_fim < s1_ini): continue
                                    if any(self.verificar_bloqueio_docente(d, d2, item['Turno'], s2_ini, s2_fim) for d in docs): continue
                                    sala2 = self.buscar_sala(item['ID_Turma'], d2, item['Turno'], s2_ini, s2_fim, modo_nomade=True)
                                    if not sala2: continue
                                    rec2 = docs + [str(item['ID_Turma']), sala2]
                                    if self.verificar_conflito(rec2, d2, item['Turno'], s2_ini, s2_fim): continue
                                    self.reservar(rec1, d1, item['Turno'], s1_ini, s1_fim)
                                    self.reservar(rec2, d2, item['Turno'], s2_ini, s2_fim)
                                    self.grade.append({
                                        "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": ch_total,
                                        "Dia": f"{d1} e {d2}", "Turno": item['Turno'], "Docentes": item['Docentes'],
                                        "Espacos": f"Split ({sala1}/{sala2})", "Semana_Inicio": f"{s1_ini}/{s2_ini}", 
                                        "Semana_Fim": f"{s1_fim}/{s2_fim}", "Status": "✅ Alocado (Tetris 16h+16h)"
                                    })
                                    split_ok = True
                                    break
                                if split_ok: break
                            if split_ok: break
                        if split_ok: break
                
                if not split_ok:
                    self.grade.append({
                        "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], 
                        "CH_Total": ch_total, "Dia": "EAD", "Turno": "EAD",
                        "Docentes": item['Docentes'], "Espacos": "EAD",
                        "Status": "⚠️ Alocado EAD (Falta de Espaço)", "Obs": "Sem sala presencial disponível"
                    })

        return pd.DataFrame(self.grade), self.erros

    def aplicar_resultado(self, item, dia, config, obs_extra=""):
        if config.get('is_ead'):
            self.grade.append({
                "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": config['ch_total'],
                "Dia": "Sexta-Feira (EAD)", "Turno": "EAD", "Docentes": item['Docentes'],
                "Espacos": "EAD", "Semana_Inicio": 1, "Semana_Fim": 20, "Status": "✅ Alocado (100% EAD)"
            })
            return

        if item['Tipo'] == "PAREO":
            self.reservar(config['rec1'], dia, item['Turno'], config['s1_ini'], config['s1_fim'])
            self.reservar(config['rec2'], dia, item['Turno'], config['s2_ini'], config['s2_fim'])
            self.grade.append({
                "ID_Turma": item['ID_Turma'], "UC": config['p1']['Nome_UC'], "CH_Total": config['p1']['Carga_Horaria_Total'],
                "Dia": dia, "Turno": item['Turno'], "Docentes": config['p1']['Docentes'],
                "Espacos": f"{config['p1']['Espacos']} ({config['sala1']})", 
                "Semana_Inicio": config['s1_ini'], "Semana_Fim": config['s1_fim'], "Status": "✅ Alocado (Pareado)" + obs_extra
            })
            self.grade.append({
                "ID_Turma": item['ID_Turma'], "UC": config['p2']['Nome_UC'], "CH_Total": config['p2']['Carga_Horaria_Total'],
                "Dia": dia, "Turno": item['Turno'], "Docentes": config['p2']['Docentes'],
                "Espacos": f"{config['p2']['Espacos']} ({config['sala2']})", 
                "Semana_Inicio": config['s2_ini'], "Semana_Fim": config['s2_fim'], "Status": "✅ Alocado (Pareado)" + obs_extra
            })
        else:
            self.reservar(config['rec'], dia, item['Turno'], config['sem_ini'], config['sem_fim'])
            status = "✅ Alocado" + obs_extra
            eh_curso_sem_sexta = any(c in str(item['ID_Turma']).upper() for c in CURSOS_SEM_SEXTA)
            if config['ch_total'] > config['meta'] and eh_curso_sem_sexta: status += " (Híbrido - EAD Sexta)"
            if dia == 'Sexta-Feira' and eh_curso_sem_sexta: status = "⚠️ Forçado na Sexta"

            self.grade.append({
                "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": config['ch_total'],
                "Dia": dia, "Turno": item['Turno'], "Docentes": item['Docentes'],
                "Espacos": f"{item['Espacos']} ({config['sala']})", 
                "Semana_Inicio": config['sem_ini'], "Semana_Fim": config['sem_fim'], 
                "Status": status, "Obs": f"{config['meta']}h Presenciais"
            })

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V21"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Alocação Finalizada!")
        
        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade_Geral.csv", converter_csv(df_res))
            z.writestr("02_Erros.csv", converter_csv(pd.DataFrame(erros, columns=["Erro"])))
            z.writestr("05_Dados_Brutos.json", df_res.to_json(orient='records', indent=4))
            
            if not df_res.empty:
                rows = []
                for _, row in df_res[df_res['Status'].str.contains("Alocado", na=False)].iterrows():
                    for d in str(row['Docentes']).split(','):
                        rows.append(row.to_dict() | {"Docente_Individual": d.strip()})
                z.writestr("04_Agenda_Docentes.csv", converter_csv(pd.DataFrame(rows)))
                z.writestr("03_Ocupacao_Espacos.csv", converter_csv(df_res[['Dia', 'Turno', 'Espacos', 'ID_Turma', 'Semana_Inicio', 'Semana_Fim']]))

        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V21.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
