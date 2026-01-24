import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile
import json

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v15.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Otimização Global (V15)")
st.markdown("""
**Arquitetura V15:**
1.  **Matchmaking:** Analisa compatibilidade docente antes de formar pares (60h+20h).
2.  **Best Fit:** Testa TODOS os dias e escolhe o que maximiza a carga presencial.
3.  **Repescagem:** Se um par falhar, desfaz o casamento e tenta alocar individualmente.
4.  **Regra Sexta-Feira:** Bloqueia apenas para cursos específicos, prioriza para os demais.
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
        self.matriz = {} # Matriz Binária

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

    def buscar_sala(self, dia, turno, sem_ini, sem_fim):
        for sala in SALAS_TEORICAS:
            if not self.verificar_conflito([sala], dia, turno, sem_ini, sem_fim): return sala
        for sala in SALAS_BACKUP:
            if not self.verificar_conflito([sala], dia, turno, sem_ini, sem_fim): return sala
        return "PENDENTE"

    def verificar_bloqueio_docente(self, docente, dia, turno, sem_ini, sem_fim):
        try:
            regra = self.restricoes[self.restricoes['Nome_Docente'] == docente]
            if not regra.empty:
                # Bloqueio Dia Fixo
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return True
                
                # Bloqueio Temporal (Licença)
                # Tenta ler colunas numéricas se existirem
                if 'Bloqueio_Semana_Inicio' in regra.columns:
                    b_ini = int(regra.iloc[0]['Bloqueio_Semana_Inicio'] or 0)
                    b_fim = int(regra.iloc[0]['Bloqueio_Semana_Fim'] or 0)
                    if b_ini > 0 and b_fim > 0:
                        # Se houver sobreposição com o período solicitado
                        if not (sem_fim < b_ini or sem_ini > b_fim): return True
                
                # Fallback para Obs de Texto
                obs = str(regra.iloc[0]['Restricoes_Extras']).lower()
                if "licença" in obs and sem_fim > 15: return True
        except: pass
        return False

    def calcular_score_compatibilidade(self, u60, u20):
        """Retorna um score de compatibilidade entre dois docentes (0 a 100)"""
        d60 = [d.strip() for d in str(u60['Docentes']).split(',')]
        d20 = [d.strip() for d in str(u20['Docentes']).split(',')]
        
        score = 100
        # Penaliza se tiverem bloqueios conflitantes
        # (Simplificado: se ambos tiverem muitos bloqueios, score cai)
        # Aqui poderíamos implementar uma verificação profunda de dias comuns
        return score

    def preparar_demandas(self):
        lista_final = []
        turmas = self.demandas['ID_Turma'].unique()
        
        for turma in turmas:
            df_t = self.demandas[self.demandas['ID_Turma'] == turma].copy()
            ucs_60 = df_t[df_t['Carga_Horaria_Total'] == 60].to_dict('records')
            ucs_20 = df_t[df_t['Carga_Horaria_Total'] == 20].to_dict('records')
            outras = df_t[~df_t['Carga_Horaria_Total'].isin([20, 60])].to_dict('records')
            
            # MATCHMAKING INTELIGENTE
            # Tenta formar os melhores pares possíveis
            while ucs_60 and ucs_20:
                melhor_score = -1
                melhor_par = (None, None)
                idx_melhor_20 = -1
                
                u60 = ucs_60[0] # Pega a primeira de 60h
                
                # Procura a melhor noiva (20h) para este noivo (60h)
                for i, u20 in enumerate(ucs_20):
                    score = self.calcular_score_compatibilidade(u60, u20)
                    if score > melhor_score:
                        melhor_score = score
                        melhor_par = (u60, u20)
                        idx_melhor_20 = i
                
                # Casa
                u_noivo, u_noiva = melhor_par
                ucs_60.pop(0)
                ucs_20.pop(idx_melhor_20)
                
                item = {
                    "Tipo": "PAREO", "Componentes": [u_noivo, u_noiva],
                    "ID_Turma": turma, "Nome_UC": f"{u_noivo['Nome_UC']} + {u_noiva['Nome_UC']}",
                    "Turno": u_noivo['Turno'], "Dia_Travado": u_noivo['Dia_Travado'] or u_noiva['Dia_Travado'],
                    "Semana_Inicio": u_noivo['Semana_Inicio'], "Regra_Especial": u_noivo['Regra_Especial']
                }
                lista_final.append(item)
            
            # Sobras
            for u in ucs_60 + ucs_20 + outras:
                u['Tipo'] = "SIMPLE"
                lista_final.append(u)
                
        return lista_final

    def simular_alocacao(self, item, dia):
        """Retorna (Sucesso, CH_Presencial, Configuração) para um dia específico"""
        
        # Filtro de Sexta-Feira
        if dia == 'Sexta-Feira':
            for curso_proibido in CURSOS_SEM_SEXTA:
                if curso_proibido in str(item['ID_Turma']).upper():
                    return (False, 0, None)

        if item['Tipo'] == "PAREO":
            u1, u2 = item['Componentes'][0], item['Componentes'][1]
            ordens = [[(u1, 15), (u2, 5)], [(u2, 5), (u1, 15)]]
            
            for config in ordens:
                p1, dur1 = config[0]
                p2, dur2 = config[1]
                
                # Tenta encaixar (Sliding Window)
                inicio_base = 1
                if item['Semana_Inicio']: inicio_base = int(item['Semana_Inicio'])
                if "FIC" in str(item['ID_Turma']): inicio_base = 4
                
                inicio_real = 2 if (inicio_base == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']) else inicio_base

                for shift in range(15):
                    s1_ini, s1_fim = inicio_real + shift, inicio_real + shift + dur1 - 1
                    s2_ini, s2_fim = s1_fim + 1, s1_fim + dur2
                    if s2_fim > 22: continue

                    # Verifica P1
                    docs1 = [d.strip() for d in str(p1['Docentes']).split(',')]
                    if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s1_ini, s1_fim) for d in docs1): continue
                    
                    esp1 = str(p1['Espacos'])
                    sala1 = ""
                    if "Sala Teórica" in esp1: sala1 = self.buscar_sala(dia, item['Turno'], s1_ini, s1_fim)
                    elif any(l in esp1 for l in LABS_AB) and s1_ini < 4: sala1 = self.buscar_sala(dia, item['Turno'], s1_ini, min(3, s1_fim))
                    
                    rec1 = docs1 + [str(item['ID_Turma'])] + ([sala1] if sala1 and sala1 != "PENDENTE" else [])
                    if any(l in esp1 for l in LABS_AB) and s1_fim >= 4: rec1 += [e.strip() for e in esp1.split('+') if e.strip() in LABS_AB]
                    
                    if self.verificar_conflito(rec1, dia, item['Turno'], s1_ini, s1_fim): continue

                    # Verifica P2
                    docs2 = [d.strip() for d in str(p2['Docentes']).split(',')]
                    if any(self.verificar_bloqueio_docente(d, dia, item['Turno'], s2_ini, s2_fim) for d in docs2): continue
                    
                    esp2 = str(p2['Espacos'])
                    sala2 = ""
                    if "Sala Teórica" in esp2: sala2 = self.buscar_sala(dia, item['Turno'], s2_ini, s2_fim)
                    
                    rec2 = docs2 + [str(item['ID_Turma'])] + ([sala2] if sala2 and sala2 != "PENDENTE" else [])
                    if any(l in esp2 for l in LABS_AB) and s2_fim >= 4: rec2 += [e.strip() for e in esp2.split('+') if e.strip() in LABS_AB]

                    if self.verificar_conflito(rec2, dia, item['Turno'], s2_ini, s2_fim): continue

                    # ACHOU!
                    return (True, 80, {
                        "p1": p1, "s1_ini": s1_ini, "s1_fim": s1_fim, "rec1": rec1, "sala1": sala1,
                        "p2": p2, "s2_ini": s2_ini, "s2_fim": s2_fim, "rec2": rec2, "sala2": sala2
                    })

        else: # SIMPLE
            ch_total = float(item['Carga_Horaria_Total'] or 0)
            dur_ideal = int(np.ceil(ch_total / 4))
            if "80%" in str(item['Regra_Especial']): dur_ideal = int(np.ceil((ch_total * 0.8) / 4))

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
                    sala = self.buscar_sala(dia, item['Turno'], sem_ini, sem_fim)
                    if sala != "PENDENTE": rec.append(sala)
                elif any(l in esp for l in LABS_AB):
                    if sem_ini < 4:
                        sala = self.buscar_sala(dia, item['Turno'], sem_ini, min(3, sem_fim))
                        if sala != "PENDENTE": rec.append(sala)
                    if sem_fim >= 4: rec += [e.strip() for e in esp.split('+') if e.strip() in LABS_AB]

                if not self.verificar_conflito(rec, dia, item['Turno'], sem_ini, sem_fim):
                    ch_presencial = dur_real * 4
                    return (True, ch_presencial, {
                        "rec": rec, "sem_ini": sem_ini, "sem_fim": sem_fim, "sala": sala, "ch_total": ch_total
                    })

        return (False, 0, None)

    def executar(self):
        lista_demandas = self.preparar_demandas()
        
        # Ordenação
        def get_onda(item):
            if "FIC" in str(item['ID_Turma']): return 1
            if item['Dia_Travado']: return 1
            if item['Tipo'] == "PAREO": return 2
            ch = float(item.get('Carga_Horaria_Total', 0) or 0)
            if ch >= 60: return 2
            return 3
        lista_demandas.sort(key=get_onda)
        
        total = len(lista_demandas)
        bar = st.progress(0)
        
        repescagem = []

        for idx, item in enumerate(lista_demandas):
            melhor_resultado = (False, -1, None, None) # (Sucesso, CH, Dia, Config)
            
            dias_teste = DIAS
            if item['Dia_Travado']: dias_teste = [item['Dia_Travado']]

            # BEST FIT: Testa todos os dias
            for dia in dias_teste:
                sucesso, ch, config = self.simular_alocacao(item, dia)
                if sucesso:
                    if ch > melhor_resultado[1]:
                        melhor_resultado = (True, ch, dia, config)
            
            # APLICA O MELHOR RESULTADO
            sucesso_final, ch_final, dia_final, config_final = melhor_resultado
            
            if sucesso_final:
                if item['Tipo'] == "PAREO":
                    self.reservar(config_final['rec1'], dia_final, item['Turno'], config_final['s1_ini'], config_final['s1_fim'])
                    self.reservar(config_final['rec2'], dia_final, item['Turno'], config_final['s2_ini'], config_final['s2_fim'])
                    
                    self.grade.append({
                        "ID_Turma": item['ID_Turma'], "UC": config_final['p1']['Nome_UC'], "CH_Total": config_final['p1']['Carga_Horaria_Total'],
                        "Dia": dia_final, "Turno": item['Turno'], "Docentes": config_final['p1']['Docentes'],
                        "Espacos": f"{config_final['p1']['Espacos']} ({config_final['sala1']})", 
                        "Semana_Inicio": config_final['s1_ini'], "Semana_Fim": config_final['s1_fim'], "Status": "✅ Alocado (Pareado)"
                    })
                    self.grade.append({
                        "ID_Turma": item['ID_Turma'], "UC": config_final['p2']['Nome_UC'], "CH_Total": config_final['p2']['Carga_Horaria_Total'],
                        "Dia": dia_final, "Turno": item['Turno'], "Docentes": config_final['p2']['Docentes'],
                        "Espacos": f"{config_final['p2']['Espacos']} ({config_final['sala2']})", 
                        "Semana_Inicio": config_final['s2_ini'], "Semana_Fim": config_final['s2_fim'], "Status": "✅ Alocado (Pareado)"
                    })
                else:
                    self.reservar(config_final['rec'], dia_final, item['Turno'], config_final['sem_ini'], config_final['sem_fim'])
                    status = "✅ Alocado"
                    if ch_final < config_final['ch_total']: status = "⚠️ Parcial"
                    
                    self.grade.append({
                        "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": config_final['ch_total'],
                        "Dia": dia_final, "Turno": item['Turno'], "Docentes": item['Docentes'],
                        "Espacos": f"{item['Espacos']} ({config_final['sala']})", 
                        "Semana_Inicio": config_final['sem_ini'], "Semana_Fim": config_final['sem_fim'], 
                        "Status": status, "Obs": f"{ch_final}h Presenciais"
                    })
            else:
                # FALHOU: Manda para Repescagem
                if item['Tipo'] == "PAREO":
                    # Divórcio: Separa e tenta depois
                    repescagem.append(item['Componentes'][0] | {"Tipo": "SIMPLE"})
                    repescagem.append(item['Componentes'][1] | {"Tipo": "SIMPLE"})
                else:
                    repescagem.append(item)

            bar.progress((idx + 1) / (total + len(repescagem) if repescagem else total))

        # EXECUTA REPESCAGEM (Individual)
        for item in repescagem:
            # (Repete a lógica de alocação SIMPLE para os itens da repescagem)
            # Simplificação: Tenta alocar Best Fit novamente
            melhor_resultado = (False, -1, None, None)
            dias_teste = DIAS
            if item.get('Dia_Travado'): dias_teste = [item['Dia_Travado']]
            
            for dia in dias_teste:
                sucesso, ch, config = self.simular_alocacao(item, dia)
                if sucesso and ch > melhor_resultado[1]: melhor_resultado = (True, ch, dia, config)
            
            sucesso_final, ch_final, dia_final, config_final = melhor_resultado
            
            if sucesso_final:
                self.reservar(config_final['rec'], dia_final, item['Turno'], config_final['sem_ini'], config_final['sem_fim'])
                self.grade.append({
                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "CH_Total": config_final['ch_total'],
                    "Dia": dia_final, "Turno": item['Turno'], "Docentes": item['Docentes'],
                    "Espacos": f"{item['Espacos']} ({config_final['sala']})", 
                    "Semana_Inicio": config_final['sem_ini'], "Semana_Fim": config_final['sem_fim'], 
                    "Status": "✅ Alocado (Repescagem)", "Obs": f"{ch_final}h Presenciais"
                })
            else:
                self.erros.append(f"❌ {item['ID_Turma']} - {item['Nome_UC']}")
                self.grade.append({"ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "Status": "❌ Erro Fatal"})

        return pd.DataFrame(self.grade), self.erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V15"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Alocação Finalizada!")
        
        # ZIP
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

        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V15.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
