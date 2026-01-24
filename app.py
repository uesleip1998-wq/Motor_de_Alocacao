import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import zipfile

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Motor Alocação IFSC v7.0", layout="wide")

st.title("🧩 Motor de Alocação IFSC - Versão Gerencial (V7)")
st.markdown("""
**Novidades da Versão 7.0:**
1.  **Prioridade por Licença:** Professores com licença furam a fila e são alocados primeiro.
2.  **Alocação Parcial:** Se não couber tudo, aloca o máximo possível e avisa o restante (EAD).
3.  **Pacote de Relatórios:** Gera ZIP com 4 planilhas (Grade, Erros, Espaços, Docentes).
""")

# --- 1. DADOS DE CONTEXTO ---
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]

SALAS_TEORICAS_DISPONIVEIS = [f"Sala {i}" for i in range(1, 13) if i != 6]

# --- 2. FUNÇÕES AUXILIARES ---

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

# --- 3. CLASSE DO MOTOR ---

class MotorAlocacao:
    def __init__(self, df_demandas, df_docentes_restricoes):
        self.demandas = df_demandas.fillna("")
        self.restricoes_docentes = df_docentes_restricoes.fillna("")
        self.grade = []
        self.log_erros = []
        self.ocupacao = {} 

    def verificar_restricao_docente(self, docente, sem_ini, sem_fim):
        """Verifica se o docente tem licença no período solicitado"""
        # Simplificação: Procura o nome do docente na aba de restrições
        # Se encontrar licença que conflita com as semanas, retorna True (Tem restrição)
        try:
            regra = self.restricoes_docentes[self.restricoes_docentes['Nome_Docente'] == docente]
            if not regra.empty:
                obs = str(regra.iloc[0]['Restricoes_Extras']).lower()
                # Lógica básica de detecção de texto (pode ser refinada)
                if "licença" in obs:
                    # Aqui poderíamos fazer um parser complexo de datas, 
                    # mas por enquanto vamos assumir que se tem licença, é crítico.
                    return True
        except:
            pass
        return False

    def verificar_disponibilidade(self, recursos, dia, turno, sem_ini, sem_fim):
        conflitos = []
        semanas_solicitadas = set(range(sem_ini, sem_fim + 1))

        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave in self.ocupacao:
                semanas_ocupadas = self.ocupacao[chave]
                if not semanas_solicitadas.isdisjoint(semanas_ocupadas):
                    conflitos.append(recurso)
        return conflitos

    def reservar_recursos(self, recursos, dia, turno, sem_ini, sem_fim):
        semanas_novas = set(range(sem_ini, sem_fim + 1))
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave not in self.ocupacao:
                self.ocupacao[chave] = set()
            self.ocupacao[chave].update(semanas_novas)

    def buscar_sala_teorica_livre(self, dia, turno, sem_ini, sem_fim):
        for sala in SALAS_TEORICAS_DISPONIVEIS:
            conflitos = self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim)
            if not conflitos:
                return sala
        return "SEM_SALA_TEORICA"

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        
        # --- INTELIGÊNCIA V7: ORDENAÇÃO POR RISCO ---
        # Calcula prioridade:
        # Nível 1 (Máximo): Docente com Licença ou Dia Travado
        # Nível 2: Uso de Laboratório (Recurso Escasso)
        # Nível 3: Normal
        
        def calcular_prioridade(row):
            docentes = [d.strip() for d in str(row['Docentes']).split(',')]
            tem_licenca = any(self.verificar_restricao_docente(d, 1, 22) for d in docentes)
            dia_travado = bool(row['Dia_Travado'])
            usa_lab = any(l in str(row['Espacos']) for l in LABS_AB)
            
            if tem_licenca or dia_travado: return 0 # Processa PRIMEIRO
            if usa_lab: return 1
            return 2

        self.demandas['Prioridade_Calc'] = self.demandas.apply(calcular_prioridade, axis=1)
        demandas_ordenadas = self.demandas.sort_values('Prioridade_Calc')
        
        total_items = len(demandas_ordenadas)
        progress_bar = st.progress(0)

        for idx, row in demandas_ordenadas.iterrows():
            alocado = False
            
            # Parsing
            docentes = [d.strip() for d in str(row['Docentes']).split(',') if d.strip()]
            espacos_originais = [e.strip() for e in str(row['Espacos']).split('+') if e.strip()]
            id_turma = str(row['ID_Turma']).strip()
            
            ch_total = float(row['Carga_Horaria_Total']) if row['Carga_Horaria_Total'] else 0
            duracao_semanas_ideal = int(np.ceil(ch_total / 4))
            sem_ini_base = int(row['Semana_Inicio']) if row['Semana_Inicio'] != "" else 1
            
            dias_tentativa = [row['Dia_Travado']] if row['Dia_Travado'] else dias_uteis

            melhor_alocacao = None # Para guardar alocação parcial se necessário

            for dia in dias_tentativa:
                if alocado: break

                # Ajuste Calendário
                sem_ini_ajustado = sem_ini_base
                if sem_ini_ajustado == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']:
                    sem_ini_ajustado = 2
                
                # Tenta encaixar (Sliding Window)
                for deslocamento in range(15): # Aumentei o range para tentar achar vaga longe
                    sem_ini_teste = sem_ini_ajustado + deslocamento
                    sem_fim_teste = sem_ini_teste + duracao_semanas_ideal - 1
                    
                    # Se passar do fim do semestre, trunca (Alocação Parcial)
                    fim_semestre = 22
                    if sem_fim_teste > fim_semestre:
                        sem_fim_teste = fim_semestre
                    
                    duracao_real = sem_fim_teste - sem_ini_teste + 1
                    if duracao_real <= 0: break

                    # Lógica de Laboratórios
                    usa_lab_ab = any(esp in LABS_AB for esp in espacos_originais)
                    
                    recursos_fase_1 = [] 
                    recursos_fase_2 = [] 
                    
                    if usa_lab_ab and sem_ini_teste < 4:
                        sala_teorica = self.buscar_sala_teorica_livre(dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                        recursos_fase_1 = docentes + [sala_teorica, id_turma]
                        if sem_fim_teste >= 4:
                            recursos_fase_2 = docentes + espacos_originais + [id_turma]
                    else:
                        recursos_fase_2 = docentes + espacos_originais + [id_turma]

                    # Verificação
                    conflitos_f1 = []
                    conflitos_f2 = []
                    
                    if recursos_fase_1:
                        conflitos_f1 = self.verificar_disponibilidade(recursos_fase_1, dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                    
                    sem_ini_f2 = max(4, sem_ini_teste) if usa_lab_ab else sem_ini_teste
                    if recursos_fase_2 and sem_fim_teste >= sem_ini_f2:
                        conflitos_f2 = self.verificar_disponibilidade(recursos_fase_2, dia, row['Turno'], sem_ini_f2, sem_fim_teste)

                    if not conflitos_f1 and not conflitos_f2:
                        # SUCESSO!
                        if recursos_fase_1: self.reservar_recursos(recursos_fase_1, dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                        if recursos_fase_2 and sem_fim_teste >= sem_ini_f2: self.reservar_recursos(recursos_fase_2, dia, row['Turno'], sem_ini_f2, sem_fim_teste)
                        
                        # Verifica se foi parcial
                        ch_alocada = duracao_real * 4
                        status = "✅ Alocado"
                        obs = ""
                        if ch_alocada < ch_total:
                            status = "⚠️ Parcial"
                            obs = f"Faltam {ch_total - ch_alocada}h (Sugerido EAD)"
                            self.log_erros.append(f"⚠️ {row['ID_Turma']} - {row['Nome_UC']}: Alocação Parcial. {obs}")

                        espaco_final = " + ".join(espacos_originais)
                        if recursos_fase_1:
                            sala_temp = [r for r in recursos_fase_1 if "Sala" in r][0]
                            espaco_final = f"{sala_temp} (Sem {sem_ini_teste}-3) -> {espaco_final} (Sem 4+)"

                        self.grade.append({
                            "ID_Turma": row['ID_Turma'], "UC": row['Nome_UC'], "Dia": dia,
                            "Turno": row['Turno'], "Docentes": ", ".join(docentes),
                            "Espacos": espaco_final, "Semana_Inicio": sem_ini_teste,
                            "Semana_Fim": sem_fim_teste, "Status": status, "Obs": obs
                        })
                        alocado = True
                        break 
                
                if alocado: break

            if not alocado:
                self.log_erros.append(f"❌ {row['ID_Turma']} - {row['Nome_UC']}: Não foi possível alocar em nenhum dia.")
                self.grade.append({
                    "ID_Turma": row['ID_Turma'], "UC": row['Nome_UC'], "Status": "❌ Erro"
                })

            progress_bar.progress((idx + 1) / total_items)

        return pd.DataFrame(self.grade), self.log_erros

# --- 4. INTERFACE ---

st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo_demandas.xlsx")
st.sidebar.markdown("---")
uploaded_file = st.sidebar.file_uploader("Carregar Planilha", type=['xlsx'])

if uploaded_file:
    if st.button("🚀 Iniciar Alocação Gerencial"):
        try:
            # Lê as duas abas necessárias
            df_demandas = pd.read_excel(uploaded_file, sheet_name='Demandas')
            try:
                df_docentes = pd.read_excel(uploaded_file, sheet_name='Docentes')
            except:
                df_docentes = pd.DataFrame() # Cria vazio se não tiver

            motor = MotorAlocacao(df_demandas, df_docentes)
            df_grade, erros = motor.executar()
            
            # --- GERAÇÃO DE RELATÓRIOS ---
            st.success("Processamento Concluído!")
            
            # 1. Grade Geral
            csv_grade = converter_df_para_csv(df_grade)
            
            # 2. Relatório de Erros
            df_erros = pd.DataFrame(erros, columns=["Mensagem"])
            csv_erros = converter_df_para_csv(df_erros)
            
            # 3. Ocupação de Espaços (Pivot Table)
            # Filtra apenas alocados
            df_ok = df_grade[df_grade['Status'].str.contains("Alocado|Parcial")].copy()
            if not df_ok.empty:
                df_espacos = df_ok[['Dia', 'Turno', 'Espacos', 'ID_Turma', 'UC']].sort_values(['Dia', 'Turno', 'Espacos'])
                csv_espacos = converter_df_para_csv(df_espacos)
                
                # 4. Agenda Docentes
                df_docentes_report = df_ok[['Docentes', 'Dia', 'Turno', 'ID_Turma', 'UC']].sort_values(['Docentes', 'Dia'])
                csv_docentes = converter_df_para_csv(df_docentes_report)
            else:
                csv_espacos = b""
                csv_docentes = b""

            # --- CRIAÇÃO DO ZIP ---
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                zip_file.writestr("01_Grade_Geral.csv", csv_grade)
                zip_file.writestr("02_Relatorio_Erros.csv", csv_erros)
                if not df_ok.empty:
                    zip_file.writestr("03_Ocupacao_Espacos.csv", csv_espacos)
                    zip_file.writestr("04_Agenda_Docentes.csv", csv_docentes)
            
            st.download_button(
                label="📦 Baixar Pacote Completo (ZIP)",
                data=zip_buffer.getvalue(),
                file_name="Relatorios_Alocacao_IFSC.zip",
                mime="application/zip"
            )
            
            # Preview na tela
            st.subheader("Visualização Rápida (Grade)")
            st.dataframe(df_grade)
            
        except Exception as e:
            st.error(f"Erro Crítico: {e}")
