import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Motor Alocação IFSC v6.0", layout="wide")

st.title("🧩 Motor de Alocação IFSC - Versão Final (Blindada)")
st.markdown("""
**Correção Crítica V6.0:**
✅ **Trava de Turma:** Impede que a mesma turma tenha 2 aulas no mesmo horário.
✅ **Ajuste de Calendário:** Corrige início na Semana 1 para dias Seg-Qua.
✅ **Regra de Laboratórios:** Aloca Sala Teórica nas semanas 1-3 automaticamente.
✅ **Deslocamento Dinâmico:** Se a Semana 11 estiver cheia, empurra para a 12.
""")

# --- 1. DADOS DE CONTEXTO ---
LABS_AB = [
    "Lab. Panificação", "Lab. Confeitaria", "Lab. Habilidades", 
    "Lab. Produção", "Lab. Cozinha Regional", "Lab. Bebidas", "Lab. Panif/Conf"
]

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
        self.ocupacao = {} 

    def verificar_disponibilidade(self, recursos, dia, turno, sem_ini, sem_fim):
        """Verifica se recursos (Docentes, Salas E TURMAS) estão livres."""
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
        for sala in SALAS_TEORICAS_DISPONIVEIS:
            # Aqui checamos apenas a sala, pois a turma e docente já serão checados na função principal
            conflitos = self.verificar_disponibilidade([sala], dia, turno, sem_ini, sem_fim)
            if not conflitos:
                return sala
        return "SEM_SALA_TEORICA"

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        
        # Ordenar demandas: UCs com "Dia Travado" ou "Labs" têm prioridade
        self.demandas['Prioridade'] = self.demandas.apply(
            lambda x: 1 if x['Dia_Travado'] or any(l in str(x['Espacos']) for l in LABS_AB) else 2, axis=1
        )
        demandas_ordenadas = self.demandas.sort_values('Prioridade')
        
        total_items = len(demandas_ordenadas)
        progress_bar = st.progress(0)

        for idx, row in demandas_ordenadas.iterrows():
            alocado = False
            
            # --- PREPARAÇÃO ---
            docentes = [d.strip() for d in str(row['Docentes']).split(',') if d.strip()]
            espacos_originais = [e.strip() for e in str(row['Espacos']).split('+') if e.strip()]
            
            # CORREÇÃO V6.0: A Turma também é um recurso que precisa ser checado!
            id_turma = str(row['ID_Turma']).strip()
            
            ch_total = float(row['Carga_Horaria_Total']) if row['Carga_Horaria_Total'] else 0
            duracao_semanas = int(np.ceil(ch_total / 4))
            sem_ini_base = int(row['Semana_Inicio']) if row['Semana_Inicio'] != "" else 1
            
            dias_tentativa = [row['Dia_Travado']] if row['Dia_Travado'] else dias_uteis

            for dia in dias_tentativa:
                if alocado: break

                # Ajuste Calendário
                sem_ini_ajustado = sem_ini_base
                if sem_ini_ajustado == 1 and dia in ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira']:
                    sem_ini_ajustado = 2
                
                # Deslocamento Dinâmico (Tenta até +8 semanas se necessário)
                for deslocamento in range(9): 
                    sem_ini_teste = sem_ini_ajustado + deslocamento
                    sem_fim_teste = sem_ini_teste + duracao_semanas - 1
                    
                    if sem_fim_teste > 22: break 

                    # Lógica de Laboratórios
                    usa_lab_ab = any(esp in LABS_AB for esp in espacos_originais)
                    
                    recursos_fase_1 = [] 
                    recursos_fase_2 = [] 
                    
                    if usa_lab_ab and sem_ini_teste < 4:
                        sala_teorica = self.buscar_sala_teorica_livre(dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                        # Fase 1: Docentes + Sala Teórica + TURMA
                        recursos_fase_1 = docentes + [sala_teorica, id_turma]
                        
                        if sem_fim_teste >= 4:
                            # Fase 2: Docentes + Labs + TURMA
                            recursos_fase_2 = docentes + espacos_originais + [id_turma]
                    else:
                        # Fase Única: Docentes + Espaços + TURMA
                        recursos_fase_2 = docentes + espacos_originais + [id_turma]

                    # Verificação de Conflitos
                    conflitos_f1 = []
                    conflitos_f2 = []
                    
                    if recursos_fase_1:
                        conflitos_f1 = self.verificar_disponibilidade(recursos_fase_1, dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                    
                    sem_ini_f2 = max(4, sem_ini_teste) if usa_lab_ab else sem_ini_teste
                    if recursos_fase_2 and sem_fim_teste >= sem_ini_f2:
                        conflitos_f2 = self.verificar_disponibilidade(recursos_fase_2, dia, row['Turno'], sem_ini_f2, sem_fim_teste)

                    if not conflitos_f1 and not conflitos_f2:
                        # Reservar
                        if recursos_fase_1:
                            self.reservar_recursos(recursos_fase_1, dia, row['Turno'], sem_ini_teste, min(3, sem_fim_teste))
                        if recursos_fase_2 and sem_fim_teste >= sem_ini_f2:
                            self.reservar_recursos(recursos_fase_2, dia, row['Turno'], sem_ini_f2, sem_fim_teste)
                        
                        # Formatar Saída
                        espaco_final = " + ".join(espacos_originais)
                        if recursos_fase_1:
                            sala_temp = [r for r in recursos_fase_1 if "Sala" in r][0]
                            espaco_final = f"{sala_temp} (Sem {sem_ini_teste}-3) -> {espaco_final} (Sem 4+)"

                        self.grade.append({
                            "ID_Turma": row['ID_Turma'], "UC": row['Nome_UC'], "Dia": dia,
                            "Turno": row['Turno'], "Docentes": ", ".join(docentes),
                            "Espacos": espaco_final, "Semana_Inicio": sem_ini_teste,
                            "Semana_Fim": sem_fim_teste, "Status": "✅ Alocado"
                        })
                        alocado = True
                        break 
                
                if alocado: break

            if not alocado:
                self.log_erros.append(f"❌ {row['ID_Turma']} - {row['Nome_UC']}: Conflito de Turma, Docente ou Sala.")
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
            
            # Filtro por Turma
            turmas = ["Todas"] + list(df_res['ID_Turma'].unique())
            filtro = st.selectbox("Filtrar Turma:", turmas)
            if filtro != "Todas":
                st.dataframe(df_res[df_res['ID_Turma'] == filtro], use_container_width=True)
            else:
                st.dataframe(df_res, use_container_width=True)
            
            if erros:
                with st.expander("Erros"):
                    for e in erros: st.error(e)
            
            st.download_button("💾 Baixar CSV", df_res.to_csv(index=False).encode('utf-8'), "grade_final.csv", "text/csv")
        except Exception as e:
            st.error(f"Erro: {e}")
