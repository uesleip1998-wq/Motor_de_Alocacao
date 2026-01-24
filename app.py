import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Motor Alocação IFSC v11.0", layout="wide")
st.title("🧩 Motor de Alocação IFSC - Estratégia de Ociosidade (V11)")
st.markdown("""
**Lógica V11:**
1.  **Regulares Primeiro:** Aloca turmas grandes para definir a "Sala Base".
2.  **FIC por Último:** Aproveita as salas que ficaram vazias quando as regulares foram para o laboratório.
3.  **Sala Pendente:** Se não houver sala, garante o horário e marca sala como "A Definir".
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

# --- MOTOR V11 ---
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
        # 1. Tenta Salas Teóricas Padrão (Busca "buracos")
        for sala in SALAS_TEORICAS:
            if not self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim):
                return sala
        # 2. Tenta Backups (Info 1, Info 2, Restaurante)
        for sala in SALAS_BACKUP:
            if not self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim):
                return sala
        # 3. Último Caso: Sala Pendente (Não trava a alocação)
        return "Sala Pendente (A Definir)"

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        
        # ESTRATÉGIA V11: INVERSÃO DE PRIORIDADE
        # 0 = Regulares (Donos da Casa) - Aloca primeiro para definir estrutura
        # 1 = FIC (Oportunistas) - Aloca depois para pegar as sobras
        def calc_prioridade(row):
            if "FIC" in str(row['ID_Turma']): return 1 
            return 0
            
        self.demandas['Prioridade'] = self.demandas.apply(calc_prioridade, axis=1)
        # Ordena: Regulares primeiro, depois FIC
        # Dentro de cada grupo, prioriza quem tem Dia Travado
        demandas_ordenadas = self.demandas.sort_values(['Prioridade', 'Dia_Travado'], ascending=[True, False])
        
        total = len(demandas_ordenadas)
        bar = st.progress(0)

        for idx, row in demandas_ordenadas.iterrows():
            alocado = False
            
            docentes = [d.strip() for d in str(row['Docentes']).split(',') if d.strip()]
            espacos = [e.strip() for e in str(row['Espacos']).split('+') if e.strip()]
            id_turma = str(row['ID_Turma']).strip()
            ch_total = float(row['Carga_Horaria_Total'] or 0)
            duracao_ideal = int(np.ceil(ch_total / 4))
            
            # Se for FIC, respeita RIGOROSAMENTE o dia travado (se houver)
            # Se for Regular, tenta o dia travado mas aceita mudar
            eh_fic = "FIC" in id_turma
            
            if row['Dia_Travado']:
                dias_tentativa = [row['Dia_Travado']]
                if not eh_fic: # Regulares podem tentar outros dias se falhar
                    dias_backup = [d for d in dias_uteis if d != row['Dia_Travado']]
                    dias_tentativa += dias_backup
            else:
                dias_tentativa = dias_uteis

            motivo_falha = ""

            for dia in dias_tentativa:
                if alocado: break

                # Checa bloqueio docente
                bloqueado = False
                for doc in docentes:
                    if self.verificar_bloqueio_docente(doc, dia, row['Turno']):
                        bloqueado = True
                        motivo_falha = f"Bloqueio de {doc}"
                        break
                if bloqueado: continue

                # Blocos
                pontos_partida = [1]
                if duracao_ideal <= 11: pontos_partida.append(11)
                if row['Semana_Inicio']: pontos_partida = [int(row['Semana_Inicio'])]

                for inicio_bloco in pontos_partida:
                    if alocado: break
                    
                    inicio_real = inicio_bloco
                    if inicio_real == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']:
                        inicio_real = 2

                    # Sliding Window
                    for shift in range(11):
                        sem_ini = inicio_real + shift
                        sem_fim = sem_ini + duracao_ideal - 1
                        if sem_fim > 22: sem_fim = 22
                        duracao_real = sem_fim - sem_ini + 1
                        if duracao_real <= 0: break

                        usa_lab = any(e in LABS_AB for e in espacos)
                        rec_f1 = []
                        rec_f2 = []
                        
                        # Lógica de Espaços
                        if usa_lab and sem_ini < 4:
                            # Se precisa de teórica antes do Lab
                            sala_t = self.buscar_sala_inteligente(dia, row['Turno'], sem_ini, min(3, sem_fim))
                            rec_f1 = docentes + [sala_t, id_turma]
                            if sem_fim >= 4:
                                rec_f2 = docentes + espacos + [id_turma]
                        else:
                            # Se pediu "Sala Teórica" genérica
                            if "Sala Teórica" in str(row['Espacos']):
                                sala_t = self.buscar_sala_inteligente(dia, row['Turno'], sem_ini, sem_fim)
                                novos_espacos = [sala_t if e == "Sala Teórica" else e for e in espacos]
                                rec_f2 = docentes + novos_espacos + [id_turma]
                            else:
                                rec_f2 = docentes + espacos + [id_turma]

                        conf_f1 = []
                        conf_f2 = []
                        if rec_f1: conf_f1 = self.verificar_disponibilidade(rec_f1, dia, row['Turno'], sem_ini, min(3, sem_fim))
                        sem_ini_f2 = max(4, sem_ini) if usa_lab else sem_ini
                        if rec_f2 and sem_fim >= sem_ini_f2:
                            conf_f2 = self.verificar_disponibilidade(rec_f2, dia, row['Turno'], sem_ini_f2, sem_fim)

                        if not conf_f1 and not conf_f2:
                            # SUCESSO
                            if rec_f1: self.reservar(rec_f1, dia, row['Turno'], sem_ini, min(3, sem_fim))
                            if rec_f2 and sem_fim >= sem_ini_f2: self.reservar(rec_f2, dia, row['Turno'], sem_ini_f2, sem_fim)
                            
                            status = "✅ Alocado"
                            obs = ""
                            if (duracao_real * 4) < ch_total:
                                status = "⚠️ Parcial"
                                obs = f"Faltam {ch_total - (duracao_real*4)}h"
                            
                            # Verifica se ficou com Sala Pendente
                            espaco_str = " + ".join(espacos)
                            sala_final = ""
                            
                            if rec_f1:
                                sala_final = [r for r in rec_f1 if "Sala" in r or "Lab" in r or "Restaurante" in r][0]
                                espaco_str = f"{sala_final} (Sem {sem_ini}-3) -> {espaco_str}"
                            elif "Sala Teórica" in str(row['Espacos']):
                                sala_final = [r for r in rec_f2 if "Sala" in r or "Lab" in r or "Restaurante" in r][0]
                                espaco_str = sala_final
                            
                            if "Pendente" in str(sala_final):
                                status = "⚠️ Sala Pendente"
                                obs += " | Alocado, mas requer definição manual de sala."

                            self.grade.append({
                                "ID_Turma": id_turma, "UC": row['Nome_UC'], "Dia": dia,
                                "Turno": row['Turno'], "Docentes": ", ".join(docentes),
                                "Espacos": espaco_str, "Semana_Inicio": sem_ini,
                                "Semana_Fim": sem_fim, "Status": status, "Obs": obs
                            })
                            alocado = True
                            break 
                        else:
                            motivo_falha = f"Conflito: {list(set(conf_f1+conf_f2))}"
                    
                    if alocado: break 
                if alocado: break 

            if not alocado:
                self.log_erros.append(f"❌ {id_turma} - {row['Nome_UC']}: {motivo_falha}")
                self.grade.append({"ID_Turma": id_turma, "UC": row['Nome_UC'], "Status": "❌ Erro", "Obs": motivo_falha})

            bar.progress((idx + 1) / total)

        return pd.DataFrame(self.grade), self.log_erros

# --- INTERFACE ---
st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo.xlsx")
st.sidebar.markdown("---")
up = st.sidebar.file_uploader("Upload Planilha", type=['xlsx'])

if up and st.button("🚀 Rodar Otimizador V11"):
    try:
        df_dem = pd.read_excel(up, sheet_name='Demandas')
        try: df_doc = pd.read_excel(up, sheet_name='Docentes')
        except: df_doc = pd.DataFrame()
        
        motor = MotorAlocacao(df_dem, df_doc)
        df_res, erros = motor.executar()
        
        st.success("Otimização Concluída!")
        
        buf = BytesIO()
        with zipfile.ZipFile(buf, "a", zipfile.ZIP_DEFLATED, False) as z:
            z.writestr("01_Grade.csv", converter_df_para_csv(df_res))
            z.writestr("02_Erros.csv", converter_df_para_csv(pd.DataFrame(erros, columns=["Erro"])))
        
        st.download_button("📦 Baixar Resultados (ZIP)", buf.getvalue(), "Resultados_V11.zip", "application/zip")
        st.dataframe(df_res)
        
    except Exception as e:
        st.error(f"Erro: {e}")
