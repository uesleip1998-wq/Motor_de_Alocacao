import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile
import copy
import re

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v19.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Zoneamento (V19)")
st.markdown("""
**Arquitetura em Camadas V19:**
1.  **Zoneamento:** Define Sala Base fixa para cada turma regular.
2.  **Regulares:** Aloca UCs usando Sala Base (Sem 1-3) + Lab (Sem 4+).
3.  **FIC:** Preenche os buracos nas salas teóricas deixados pelos regulares.
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
        self.sala_base = {} # Mapa: Turma -> Sala Fixa

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
        # Fusão de UCs fragmentadas
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
        CAMADA 1: Atribui Sala Base para cada Turma Regular
        """
        turmas = self.demandas['ID_Turma'].unique()
        turmas_regulares = [t for t in turmas if "FIC" not in str(t).upper()]
        
        # Distribui salas sequencialmente
        idx_sala = 0
        for turma in turmas_regulares:
            if idx_sala < len(SALAS_TEORICAS):
                self.sala_base[turma] = SALAS_TEORICAS[idx_sala]
                idx_sala += 1
            else:
                # Se acabaram as salas teóricas, usa backup
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

    def simular_alocacao(self, item, dia, forcar_sexta=False, modo_fic=False):
        # Bypass EAD
        espacos_str = str(item.get('Espacos', '')).upper()
        if "EAD" in espacos_str or "100% EAD" in str(item.get('Regra_Especial', '')).upper():
            ch = float(item.get('Carga_Horaria_Total', 0) or 0)
            return (True, ch, {
                "rec": [str(item['ID_Turma'])], "sem_ini": 1, "sem_fim": 20, 
                "sala": "EAD", "ch_total": ch, "meta": ch, "is_ead": True
            })

        # Filtro Sexta
        eh_curso_sem_sexta = any(c in str(item['ID_Turma']).upper() for c in CURSOS_SEM_SEXTA)
        if dia == 'Sexta-Feira' and eh_curso_sem_sexta and not forcar_sexta:
            return (False, 0, None)

        # Definição da Sala
        sala_alvo = ""
        if modo_fic:
            # FIC procura qualquer buraco
            pass # Lógica de busca dinâmica abaixo
        else:
            # Regular usa Sala Base
            sala_alvo = self.sala_base.get(item['ID_Turma'], "PENDENTE")

        # Lógica de Alocação (Simplificada para brevidade, mas com Sala Base)
        if item['Tipo'] == "PAREO":
            u1, u2 = item['Componentes'][0], item['Componentes'][1]
            ordens = [[(u1, 15), (u2, 5)], [(u2, 5), (u1, 15)]]
            for config in ordens:
                p1, dur1 = config[0]
                p2, dur2 = config[1]
                
                # Início sempre na semana 1 ou 2 para Regulares
                inicio_real = 2 if dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira'] else 1
                
                # Verifica P1
                docs1 = [d.strip() for d in str(p1['Docentes']).split(',')]
                if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], inicio_real, inicio_real+dur1) for d in docs1): continue
                
                # Sala P1
                sala1 = sala_alvo
                if modo_fic: # Busca dinâmica para FIC
                    for s in SALAS_TEORICAS:
                        if not self.verificar_conflito([s], dia, item['Turno'], inicio_real, inicio_real+dur1): 
                            sala1 = s; break
                
                if self.verificar_conflito([sala1] + docs1 + [str(item['ID_Turma'])], dia, item['Turno'], inicio_real, inicio_real+dur1): continue

                # Verifica P2 (similar)
                s2_ini = inicio_real + dur1
                docs2 = [d.strip() for d in str(p2['Docentes']).split(',')]
                if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s2_ini, s2_ini+dur2) for d in docs2): continue
                
                sala2 = sala_alvo
                if modo_fic:
                    for s in SALAS_TEORICAS:
                        if not self.verificar_conflito([s], dia, item['Turno'], s2_ini, s2_ini+dur2): 
                            sala2 = s; break

                if self.verificar_conflito([sala2] + docs2 + [str(item['ID_Turma'])], dia, item['Turno'], s2_ini, s2_ini+dur2): continue

                return (True, 80, {
                    "p1": p1, "s1_ini": inicio_real, "s1_fim": inicio_real+dur1-1, "rec1": docs1+[str(item['ID_Turma']), sala1], "sala1": sala1,
                    "p2": p2, "s2_ini": s2_ini, "s2_fim": s2_ini+dur2-1, "rec2": docs2+[str(item['ID_Turma']), sala2], "sala2": sala2
                })

        else: # SIMPLE
            ch_total = float(item['Carga_Horaria_Total'] or 0)
            dur_ideal = int(np.ceil(ch_total / 4))
            meta_presencial = ch_total
            if "80%" in str(item['Regra_Especial']):
                meta_presencial = ch_total * 0.8
                dur_ideal = int(np.ceil(meta_presencial / 4))

            # Regra de Início: Se for Regular, tenta começar na Sem 1. Se for FIC, Sem 4.
            inicio_base = 1
            if modo_fic: inicio_base = 4
            elif item['Semana_Inicio']: inicio_base = int(item['Semana_Inicio'])
            
            inicio_real = 2 if (inicio_base == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']) else inicio_base

            # Verifica Lab
            usa_lab = any(l in str(item['Espacos']) for l in LABS_AB)
            
            # Se usa Lab, precisa de Sala Base (Sem 1-3) + Lab (Sem 4+)
            # Se não usa Lab, precisa de Sala Base (Sem 1-Fim)
            
            sem_ini = inicio_real
            sem_fim = sem_ini + dur_ideal - 1
            if sem_fim > 22: sem_fim = 22
            
            docs = [d.strip() for d in str(item['Docentes']).split(',')]
            if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], sem_ini, sem_fim) for d in docs): return (False, 0, None)

            rec_total = docs + [str(item['ID_Turma'])]
            
            # Fase A: Sala Teórica
            fim_teorica = min(3, sem_fim) if usa_lab else sem_fim
            sala_teorica = sala_alvo
            if modo_fic:
                sala_teorica = ""
                for s in SALAS_TEORICAS:
                    if not self.verificar_conflito([s], dia, item['Turno'], sem_ini, fim_teorica):
                        sala_teorica = s; break
            
            if not sala_teorica or self.verificar_conflito([sala_teorica], dia, item['Turno'], sem_ini, fim_teorica): return (False, 0, None)
            rec_total.append(sala_teorica)

            # Fase B: Lab (se houver)
            if usa_lab and sem_fim >= 4:
                labs = [e.strip() for e in str(item['Espacos']).split('+') if e.strip() in LABS_AB]
                if self.verificar_conflito(labs, dia, item['Turno'], 4, sem_fim): return (False, 0, None)
                rec_total += labs

            # Validação Final
            if self.verificar_conflito(docs + [str(item['ID_Turma'])], dia, item['Turno'], sem_ini, sem_fim): return (False, 0, None)

            return (True, dur_ideal*4, {
                "rec": rec_total, "sem_ini": sem_ini, "sem_fim": sem_fim, "sala": sala_teorica, "ch_total": ch_total, "meta": meta_presencial
            })

        return (False, 0, None)

    def executar(self):
        # 1. Zoneamento
        self.definir_zoneamento()
        
        lista_demandas = self.preparar_demandas()
        
        # Separa Regulares e FIC
        regulares = [i for i in lista_demandas if "FIC" not in str(i['ID_Turma']).upper()]
        fics = [i for i in lista_demandas if "FIC" in str(i['ID_Turma']).upper()]
        
        # Ordena Regulares (Pareados primeiro)
        regulares.sort(key=lambda x: 0 if x['Tipo'] == "PAREO" else 1)
        
        total = len(lista_demandas)
        bar = st.progress(0)
        repescagem = []

        # CAMADA 2: REGULARES
        for idx, item in enumerate(regulares):
            melhor = (False, -1, None, None)
            dias_teste = DIAS
            if item['Dia_Travado']: dias_teste = [item['Dia_Travado']]

            for dia in dias_teste:
                sucesso, ch, config = self.simular_alocacao(item, dia, modo_fic=False)
                if sucesso and ch > melhor[1]: melhor = (True, ch, dia, config)
            
            if melhor[0]:
                self.aplicar_resultado(item, melhor[2], melhor[3])
            else:
                if item['Tipo'] == "PAREO":
                    repescagem.append(copy.deepcopy(item['Componentes'][0]) | {"Tipo": "SIMPLE"})
                    repescagem.append(copy.deepcopy(item['Componentes'][1]) | {"Tipo": "SIMPLE"})
                else:
                    repescagem.append(copy.deepcopy(item))
            bar.progress((idx + 1) / total)

        # CAMADA 3: FIC + REPESCAGEM
        fila_final = fics + repescagem
        for item in fila_final:
            modo_fic = "FIC" in str(item['ID_Turma']).upper()
            melhor = (False, -1, None, None)
            dias_teste = DIAS
            if item.get('Dia_Travado'): dias_teste = [item['Dia_Travado']]
            
            for dia in dias_teste:
                sucesso, ch, config = self.simular_alocacao(item, dia, modo_fic=True) # Modo FIC permite qualquer sala
                if sucesso and ch > melhor[1]: melhor = (True, ch, dia, config)
            
            if not melhor[0]: # Válvula de Escape
                sucesso, ch, config = self.simular_alocacao(item, 'Sexta-Feira', forcar_sexta=True, modo_fic=True)
                if sucesso: melhor = (True, ch, 'Sexta-Feira', config)

            if melhor[0]:
                self.aplicar_resultado(item, melhor[2], melhor[3], " (Flex)")
            else:
                # Último recurso EAD
                self.grade.append({
                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], 
                    "CH_Total": item.get('Carga_Horaria_Total'), "Dia": "EAD", "Turno": "EAD",
                    "Docentes": item['Docentes'], "Espacos": "EAD", "Status": "⚠️ Alocado EAD (Falta de Espaço)"
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

if up and st.button("🚀 Rodar Otimizador V19"):
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

        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V19.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
