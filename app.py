import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile
import time

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v12.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Estratégico (V12)")
st.markdown("""
**Novas Inteligências V12:**
1.  **Pareamento Automático:** UCs de 60h + 20h são fundidas e alocadas no mesmo dia.
2.  **Regra 80/20:** Lê a coluna 'Regra_Especial' e calcula a meta presencial correta.
3.  **Relatórios Completos:** Gera Grade, Erros, Ocupação de Salas e Horário de Docentes.
""")

# --- DADOS DE CONTEXTO ---
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]
SALAS_TEORICAS = [f"Sala {i}" for i in range(1, 13) if i != 6]
SALAS_BACKUP = ["Lab. Informática 1", "Lab. Informática 2", "Restaurante 1"]

# --- FUNÇÕES ---
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

def converter_df_para_csv(df):
    return df.to_csv(index=False).encode('utf-8-sig')

# --- MOTOR V12 ---
class MotorAlocacao:
    def __init__(self, df_demandas, df_docentes_restricoes):
        self.demandas = df_demandas.fillna("")
        self.restricoes_docentes = df_docentes_restricoes.fillna("")
        self.grade = []
        self.log_erros = []
        self.ocupacao = {} 

    def verificar_bloqueio_docente(self, docente, dia, turno):
        try:
            regra = self.restricoes_docentes[self.restricoes_docentes['Nome_Docente'] == docente]
            if not regra.empty:
                dias_indisp = str(regra.iloc[0]['Dias_Indisponiveis'])
                if dia in dias_indisp and turno in dias_indisp: return True
        except: pass
        return False

    def verificar_disponibilidade(self, recursos, dia, turno, sem_ini, sem_fim):
        conflitos = []
        semanas_solicitadas = set(range(sem_ini, sem_fim + 1))
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave in self.ocupacao:
                if not semanas_solicitadas.isdisjoint(self.ocupacao[chave]):
                    conflitos.append(recurso)
        return conflitos

    def reservar(self, recursos, dia, turno, sem_ini, sem_fim):
        semanas = set(range(sem_ini, sem_fim + 1))
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave not in self.ocupacao: self.ocupacao[chave] = set()
            self.ocupacao[chave].update(semanas)

    def buscar_sala_inteligente(self, dia, turno, sem_ini, sem_fim):
        for sala in SALAS_TEORICAS:
            if not self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim):
                return sala
        for sala in SALAS_BACKUP:
            if not self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim):
                return sala
        return "Sala Pendente (A Definir)"

    def agrupar_demandas(self):
        """
        Lógica de Pareamento (Estratégia C):
        Agrupa UCs de 60h e 20h da mesma turma em um 'Super-Bloco'.
        """
        demandas_processadas = []
        turmas = self.demandas['ID_Turma'].unique()
        
        for turma in turmas:
            df_turma = self.demandas[self.demandas['ID_Turma'] == turma].copy()
            
            # Separa UCs candidatas a pareamento (60h e 20h)
            ucs_60 = df_turma[df_turma['Carga_Horaria_Total'] == 60].to_dict('records')
            ucs_20 = df_turma[df_turma['Carga_Horaria_Total'] == 20].to_dict('records')
            outras = df_turma[~df_turma['Carga_Horaria_Total'].isin([20, 60])].to_dict('records')
            
            # Tenta casar 60+20
            while ucs_60 and ucs_20:
                u60 = ucs_60.pop(0)
                u20 = ucs_20.pop(0)
                
                # Cria Super-Bloco
                super_bloco = {
                    "ID_Turma": turma,
                    "Nome_UC": f"[PAREO] {u60['Nome_UC']} + {u20['Nome_UC']}",
                    "Turno": u60['Turno'], # Assume turno da maior
                    "Docentes": f"{u60['Docentes']}, {u20['Docentes']}",
                    "Espacos": f"{u60['Espacos']} | {u20['Espacos']}",
                    "Carga_Horaria_Total": 80,
                    "Dia_Travado": u60['Dia_Travado'] or u20['Dia_Travado'],
                    "Semana_Inicio": u60['Semana_Inicio'],
                    "Regra_Especial": u60['Regra_Especial'],
                    "Tipo": "PAREO",
                    "Componentes": [u60, u20]
                }
                demandas_processadas.append(super_bloco)

            # O que sobrou (20h com 20h, ou 60h sozinhas)
            # Implementar lógica de 20+20 se necessário, por enquanto processa individual
            for u in ucs_60 + ucs_20 + outras:
                u['Tipo'] = "SIMPLE"
                demandas_processadas.append(u)
                
        return demandas_processadas

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        
        # 1. Agrupamento Inteligente
        lista_demandas = self.agrupar_demandas()
        
        # 2. Ordenação (Prioridade FIC > Dia Travado > Pareados > Resto)
        def get_score(item):
            if "FIC" in str(item['ID_Turma']): return 0
            if item['Dia_Travado']: return 1
            if item['Tipo'] == "PAREO": return 2
            return 3
            
        lista_demandas.sort(key=get_score)
        
        total = len(lista_demandas)
        bar = st.progress(0)

        for idx, item in enumerate(lista_demandas):
            alocado = False
            
            # --- PARSING INTELIGENTE ---
            # Se for PAREO, temos que tratar diferente
            if item['Tipo'] == "PAREO":
                u60 = item['Componentes'][0]
                u20 = item['Componentes'][1]
                ch_total = 80
                duracao_ideal = 20 # 80h / 4h = 20 semanas
            else:
                ch_total = float(item['Carga_Horaria_Total'] or 0)
                duracao_ideal = int(np.ceil(ch_total / 4))
                
            # Regra 80/20 (Estratégia A)
            meta_presencial = ch_total
            if "80%" in str(item['Regra_Especial']):
                meta_presencial = ch_total * 0.8
                duracao_ideal = int(np.ceil(meta_presencial / 4))

            # Definição de Dias
            dias_tentativa = dias_uteis
            if item['Dia_Travado']:
                dias_tentativa = [item['Dia_Travado']]
                # Se não for FIC, permite backup
                if "FIC" not in str(item['ID_Turma']):
                    dias_tentativa += [d for d in dias_uteis if d != item['Dia_Travado']]

            # --- LOOP DE ALOCAÇÃO ---
            for dia in dias_tentativa:
                if alocado: break

                # Verifica Bloqueio Docente (Para todos os docentes do item)
                docs_check = [d.strip() for d in str(item['Docentes']).split(',')]
                if any(self.verificar_bloqueio_docente(d, dia, item['Turno']) for d in docs_check):
                    continue

                # Pontos de Partida (Sempre tenta Sem 1 e Sem 11)
                starts = [1]
                if duracao_ideal <= 11: starts.append(11)
                if item['Semana_Inicio']: starts = [int(item['Semana_Inicio'])]
                
                # Estratégia D (FIC começa na Sem 4)
                if "FIC" in str(item['ID_Turma']) and not item['Semana_Inicio']:
                    starts = [4]

                for inicio in starts:
                    if alocado: break
                    
                    # Ajuste Calendário
                    inicio_real = inicio
                    if inicio_real == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']:
                        inicio_real = 2

                    # Sliding Window
                    for shift in range(15): # Tenta deslizar bastante
                        sem_ini = inicio_real + shift
                        sem_fim = sem_ini + duracao_ideal - 1
                        if sem_fim > 22: sem_fim = 22
                        duracao_real = sem_fim - sem_ini + 1
                        if duracao_real <= 0: break
                        
                        # --- VERIFICAÇÃO DE RECURSOS ---
                        # Se for PAREO, precisamos verificar recursos para a parte 60h e parte 20h
                        # Simplificação V12: Verifica recursos para o bloco todo
                        # (Refinamento futuro: verificar recursos separados por semana)
                        
                        espacos_str = str(item['Espacos'])
                        usa_lab = any(e in LABS_AB for e in espacos_str)
                        
                        # Monta lista de recursos
                        recursos_base = docs_check + [str(item['ID_Turma'])]
                        
                        # Lógica de Sala
                        sala_alocada = ""
                        if usa_lab and sem_ini < 4:
                            sala_alocada = self.buscar_sala_inteligente(dia, item['Turno'], sem_ini, min(3, sem_fim))
                            # Se for Lab, precisa da Sala nas semanas 1-3 E do Lab nas semanas 4+
                            # Aqui simplificamos: verificamos se a Sala está livre no início
                            # E se o Lab está livre depois
                        elif "Sala Teórica" in espacos_str:
                            sala_alocada = self.buscar_sala_inteligente(dia, item['Turno'], sem_ini, sem_fim)
                        
                        # Verifica Disponibilidade
                        # Fase 1 (Semanas 1-3 se for Lab, ou tudo se for Teórica)
                        rec_f1 = recursos_base + ([sala_alocada] if sala_alocada else [])
                        conf_f1 = self.verificar_disponibilidade(rec_f1, dia, item['Turno'], sem_ini, min(3, sem_fim))
                        
                        # Fase 2 (Semanas 4+ para Lab)
                        conf_f2 = []
                        rec_f2 = []
                        sem_ini_f2 = sem_ini
                        if usa_lab:
                            sem_ini_f2 = max(4, sem_ini)
                            if sem_fim >= sem_ini_f2:
                                labs_reais = [e.strip() for e in espacos_str.split('+') if e.strip() in LABS_AB]
                                rec_f2 = recursos_base + labs_reais
                                conf_f2 = self.verificar_disponibilidade(rec_f2, dia, item['Turno'], sem_ini_f2, sem_fim)

                        if not conf_f1 and not conf_f2:
                            # SUCESSO!
                            if rec_f1: self.reservar(rec_f1, dia, item['Turno'], sem_ini, min(3, sem_fim))
                            if rec_f2: self.reservar(rec_f2, dia, item['Turno'], sem_ini_f2, sem_fim)
                            
                            # Registro
                            ch_alocada = duracao_real * 4
                            status = "✅ Alocado"
                            obs = ""
                            
                            # Validação 80/20
                            if ch_alocada < meta_presencial:
                                status = "⚠️ Parcial"
                                obs = f"Alocado {ch_alocada}h. Meta Presencial: {meta_presencial}h."
                            elif ch_alocada < ch_total:
                                # Se atingiu a meta presencial, é Sucesso (o resto é EAD)
                                status = "✅ Alocado (Híbrido)"
                                obs = f"{ch_alocada}h Presenciais + {ch_total - ch_alocada}h EAD/Assíncrono"

                            # Formatação de Saída
                            if item['Tipo'] == "PAREO":
                                # Explode o pareo de volta em 2 linhas para o relatório
                                # Parte 1 (60h)
                                self.grade.append({
                                    "ID_Turma": item['ID_Turma'], "UC": u60['Nome_UC'], "Dia": dia,
                                    "Turno": item['Turno'], "Docentes": u60['Docentes'],
                                    "Espacos": str(u60['Espacos']) + (f" ({sala_alocada})" if sala_alocada else ""),
                                    "Semana_Inicio": sem_ini, "Semana_Fim": sem_ini + 14, # Aprox 15 sem
                                    "Status": status, "Obs": "Pareado com " + u20['Nome_UC']
                                })
                                # Parte 2 (20h)
                                self.grade.append({
                                    "ID_Turma": item['ID_Turma'], "UC": u20['Nome_UC'], "Dia": dia,
                                    "Turno": item['Turno'], "Docentes": u20['Docentes'],
                                    "Espacos": str(u20['Espacos']) + (f" ({sala_alocada})" if sala_alocada else ""),
                                    "Semana_Inicio": sem_ini + 15, "Semana_Fim": sem_fim,
                                    "Status": status, "Obs": "Pareado com " + u60['Nome_UC']
                                })
                            else:
                                self.grade.append({
                                    "ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "Dia": dia,
                                    "Turno": item['Turno'], "Docentes": item['Docentes'],
                                    "Espacos": str(item['Espacos']) + (f" ({sala_alocada})" if sala_alocada else ""),
                                    "Semana_Inicio": sem_ini, "Semana_Fim": sem_fim,
                                    "Status": status, "Obs": obs
                                })
                            
                            alocado = True
                            break # Sai do Shift
                    if alocado: break # Sai do Bloco
                if alocado: break # Sai do Dia

            if not alocado:
                self.log_erros.append(f"❌ {item['ID_Turma']} - {item['Nome_UC']}: Não alocado.")
                self.grade.append({"ID_Turma": item['ID_Turma'], "UC": item['Nome_UC'], "Status": "❌ Erro"})

            bar.progress((idx + 1) / total)

        # --- GERAÇÃO DE RELATÓRIOS EXTRAS ---
        # 1. Ocupação Espaços
        df_grade = pd.DataFrame(self.grade)
        # (Lógica simplificada para gerar CSVs extras a partir da grade final)
        
        return df_grade, self.log_erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V12"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Otimização Concluída!")
        
        # Gera ZIP com 4 arquivos
        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade_Geral.csv", converter_df_para_csv(df_res))
            z.writestr("02_Relatorio_Erros.csv", converter_df_para_csv(pd.DataFrame(erros, columns=["Erro"])))
            
            # Relatórios Derivados
            if not df_res.empty and 'Espacos' in df_res.columns:
                df_ok = df_res[df_res['Status'].str.contains("Alocado", na=False)]
                
                # Relatório Espaços
                df_esp = df_ok[['Dia', 'Turno', 'Espacos', 'ID_Turma', 'UC']].sort_values(['Dia', 'Turno'])
                z.writestr("03_Ocupacao_Espacos.csv", converter_df_para_csv(df_esp))
                
                # Relatório Docentes
                df_doc_rep = df_ok[['Docentes', 'Dia', 'Turno', 'ID_Turma', 'UC']].sort_values(['Docentes', 'Dia'])
                z.writestr("04_Agenda_Docentes.csv", converter_df_para_csv(df_doc_rep))

        st.download_button("📦 Baixar Pacote Completo (ZIP)", buf.getvalue(), "Relatorios_V12.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
