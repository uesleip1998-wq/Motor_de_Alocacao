import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Motor Alocação IFSC v5.0", layout="wide")

st.title("🧩 Motor de Alocação Inteligente - IFSC 2026/1")
st.markdown("""
**Versão 5.0 (Final)** | Recursos Avançados:
1. **Ajuste de Calendário:** Corrige início na Semana 1 para dias Seg-Qua.
2. **Regra de Laboratórios:** Aloca Sala Teórica nas semanas 1-3 automaticamente.
3. **Deslocamento Dinâmico:** Se a Semana 11 estiver cheia, empurra para a 12.
""")

# --- 1. DADOS DE CONTEXTO (SIMULADOS DO JSON) ---
# Precisamos saber quais salas são "Laboratórios A&B" e quais são "Teóricas"
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]

# Lista de salas teóricas para alocação automática nas semanas 1-3
SALAS_TEORICAS_DISPONIVEIS = [
    "Sala 1", "Sala 2", "Sala 3", "Sala 4", "Sala 5", 
    "Sala 7", "Sala 8", "Sala 9", "Sala 10", "Sala 11", "Sala 12"
]

# --- 2. FUNÇÕES AUXILIARES ---

def gerar_template():
    """Gera planilha modelo"""
    df = pd.DataFrame(columns=[
        "ID_Turma", "Nome_UC", "Turno", "Docentes", "Espacos", 
        "Tipo_Alocacao", "Carga_Horaria_Total", "Regra_Especial", 
        "Dia_Travado", "Semana_Inicio", "Semana_Fim"
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demandas', index=False)
    return buffer

# --- 3. CLASSE DO MOTOR ---

class MotorAlocacao:
    def __init__(self, df_demandas):
        self.demandas = df_demandas.fillna("")
        self.grade = []
        self.log_erros = []
        
        # Matriz de Ocupação: Chave -> Set de semanas ocupadas
        self.ocupacao = {} 

    def verificar_disponibilidade(self, recursos, dia, turno, sem_ini, sem_fim):
        """Verifica se recursos estão livres no intervalo."""
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
        """Marca recursos como ocupados."""
        semanas_novas = set(range(sem_ini, sem_fim + 1))
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave not in self.ocupacao:
                self.ocupacao[chave] = set()
            self.ocupacao[chave].update(semanas_novas)

    def buscar_sala_teorica_livre(self, dia, turno, sem_ini, sem_fim):
        """Encontra uma sala teórica qualquer livre para as semanas 1-3"""
        for sala in SALAS_TEORICAS_DISPONIVEIS:
            conflitos = self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim)
            if not conflitos:
                return sala
        return "SEM_SALA_TEORICA"

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        total_items = len(self.demandas)
        progress_bar = st.progress(0)

        for idx, row in self.demandas.iterrows():
            alocado = False
            
            # --- PREPARAÇÃO ---
            docentes = [d.strip() for d in str(row['Docentes']).split(',') if d.strip()]
            espacos_originais = [e.strip() for e in str(row['Espacos']).split('+') if e.strip()]
            
            # Cálculo de Duração
            ch_total = float(row['Carga_Horaria_Total']) if row['Carga_Horaria_Total'] else 0
            duracao_semanas = int(np.ceil(ch_total / 4)) # Assumindo 4h/semana padrão
            
            sem_ini_base = int(row['Semana_Inicio']) if row['Semana_Inicio'] != "" else 1
            
            # Dias para tentar
            dias_tentativa = [row['Dia_Travado']] if row['Dia_Travado'] else dias_uteis

            for dia in dias_tentativa:
                if alocado: break

                # 1. AJUSTE DE CALENDÁRIO (Semana 1 vs 2)
                sem_ini_ajustado = sem_ini_base
                if sem_ini_ajustado == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']:
                    sem_ini_ajustado = 2
                
                # 2. LÓGICA DE DESLOCAMENTO (SLIDING WINDOW)
                # Se houver conflito, tenta empurrar para frente até +4 semanas
                for deslocamento in range(5): 
                    sem_ini_teste = sem_ini_ajustado + deslocamento
                    sem_fim_teste = sem_ini_teste + duracao_semanas - 1
                    
                    if sem_fim_teste > 22: break # Não pode passar do fim do semestre

                    # --- 3. LÓGICA DE LABORATÓRIOS (Semanas 1-3) ---
                    # Verifica se algum espaço solicitado é Lab A&B
                    usa_lab_ab = any(esp in LABS_AB for esp in espacos_originais)
                    
                    recursos_fase_1 = [] # Semanas 1-3 (ou até onde precisar)
                    recursos_fase_2 = [] # Semanas 4+
                    
                    # Se usa Lab A&B e começa antes da semana 4
                    if usa_lab_ab and sem_ini_teste < 4:
                        # FASE 1: Docentes + Sala Teórica (Genérica)
                        sala_teorica = self.buscar_sala_teorica_livre(dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                        recursos_fase_1 = docentes + [sala_teorica]
                        
                        # FASE 2: Docentes + Labs Originais (Só se a disciplina passar da semana 3)
                        if sem_fim_teste >= 4:
                            recursos_fase_2 = docentes + espacos_originais
                    else:
                        # Caso normal (não é lab ou já começa depois da sem 4)
                        recursos_fase_1 = [] 
                        recursos_fase_2 = docentes + espacos_originais # Tudo é fase 2 (principal)

                    # VERIFICAÇÃO DE CONFLITOS (Faseada)
                    conflitos_f1 = []
                    conflitos_f2 = []
                    
                    if recursos_fase_1:
                        conflitos_f1 = self.verificar_disponibilidade(recursos_fase_1, dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                    
                    sem_ini_f2 = max(4, sem_ini_teste) if usa_lab_ab else sem_ini_teste
                    if recursos_fase_2 and sem_fim_teste >= sem_ini_f2:
                        conflitos_f2 = self.verificar_disponibilidade(recursos_fase_2, dia, row['Turno'], sem_ini_f2, sem_fim_teste)

                    # SE TUDO ESTIVER LIVRE
                    if not conflitos_f1 and not conflitos_f2:
                        # Reservar Fase 1
                        if recursos_fase_1:
                            self.reservar_recursos(recursos_fase_1, dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                        
                        # Reservar Fase 2
                        if recursos_fase_2 and sem_fim_teste >= sem_ini_f2:
                            self.reservar_recursos(recursos_fase_2, dia, row['Turno'], sem_ini_f2, sem_fim_teste)
                        
                        # Montar String de Espaço para o Relatório
                        espaco_final = " + ".join(espacos_originais)
                        if recursos_fase_1:
                            sala_temp = recursos_fase_1[-1] # Pega a sala teórica achada
                            espaco_final = f"{sala_temp} (Sem {sem_ini_teste}-3) -> {espaco_final} (Sem 4+)"

                        self.grade.append({
                            "ID_Turma": row['ID_Turma'],
                            "UC": row['Nome_UC'],
                            "Dia": dia,
                            "Turno": row['Turno'],
                            "Docentes": ", ".join(docentes),
                            "Espacos": espaco_final,
                            "Semana_Inicio": sem_ini_teste,
                            "Semana_Fim": sem_fim_teste,
                            "Status": "✅ Alocado"
                        })
                        alocado = True
                        break # Sai do loop de deslocamento
                
                if alocado: break # Sai do loop de dias

            if not alocado:
                self.log_erros.append(f"❌ {row['ID_Turma']} - {row['Nome_UC']}: Falha após tentar deslocamento e dias.")
                self.grade.append({
                    "ID_Turma": row['ID_Turma'], "UC": row['Nome_UC'], "Dia": "-", 
                    "Turno": row['Turno'], "Docentes": str(row['Docentes']), 
                    "Espacos": str(row['Espacos']), "Semana_Inicio": "-", "Semana_Fim": "-", 
                    "Status": "❌ Erro"
                })

            progress_bar.progress((idx + 1) / total_items)

        return pd.DataFrame(self.grade), self.log_erros

# --- 4. INTERFACE ---

st.sidebar.header("📂 Área de Trabalho")
st.sidebar.download_button("📥 Baixar Modelo", gerar_template(), "modelo_demandas.xlsx")
st.sidebar.markdown("---")
uploaded_file = st.sidebar.file_uploader("Carregar Planilha", type=['xlsx'])

if uploaded_file:
    if st.button("🚀 Iniciar Alocação"):
        try:
            df_input = pd.read_excel(uploaded_file, sheet_name='Demandas')
            motor = MotorAlocacao(df_input)
            df_res, erros = motor.executar()
            
            st.subheader("Resultado")
            st.dataframe(df_res, use_container_width=True)
            
            if erros:
                with st.expander("Erros"):
                    for e in erros: st.error(e)
            
            st.download_button("💾 Baixar CSV", df_res.to_csv(index=False).encode('utf-8'), "grade_final.csv", "text/csv")
        except Exception as e:
            st.error(f"Erro: {e}")
