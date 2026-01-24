import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Motor Alocação IFSC v4.0", layout="wide")

st.title("🧩 Motor de Alocação de Horários - IFSC 2026/1")
st.markdown("""
**Versão 4.0** | Suporte a Multi-Docentes, Multi-Espaços e Calendário Inteligente.
""")

# --- 1. FUNÇÕES AUXILIARES ---

def gerar_template():
    """Gera uma planilha de exemplo para o usuário baixar"""
    df = pd.DataFrame(columns=[
        "ID_Turma", "Nome_UC", "Turno", "Docentes", "Espacos", 
        "Tipo_Alocacao", "Carga_Horaria_Total", "Regra_Especial", 
        "Dia_Travado", "Semana_Inicio", "Semana_Fim"
    ])
    # Adiciona uma linha de exemplo
    df.loc[0] = [
        "CONFEITARIA 2", "CONF. ARTÍSTICA (Pt 1)", "Matutino", "MARIANA MARTELLI", 
        "Lab. Panif/Conf", "Sequencial", 48, "Bloco 1", "", 1, 12
    ]
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demandas', index=False)
    return buffer

# --- 2. CLASSE DO MOTOR DE ALOCAÇÃO ---

class MotorAlocacao:
    def __init__(self, df_demandas):
        self.demandas = df_demandas.fillna("") # Remove valores nulos
        self.grade = []
        self.log_erros = []
        
        # Matriz de Ocupação Global (Para evitar choques)
        # Chave: "TIPO|NOME|DIA|TURNO" -> Valor: Lista de semanas ocupadas [1, 2, 3...]
        self.ocupacao = {}

    def verificar_disponibilidade(self, recursos, dia, turno, sem_ini, sem_fim):
        """
        Verifica se Professores e Salas estão livres no intervalo de semanas solicitado.
        """
        conflitos = []
        semanas_solicitadas = set(range(sem_ini, sem_fim + 1))

        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            
            if chave in self.ocupacao:
                semanas_ocupadas = self.ocupacao[chave]
                # Se houver interseção entre as semanas solicitadas e as ocupadas
                if not semanas_solicitadas.isdisjoint(semanas_ocupadas):
                    conflitos.append(recurso)
        
        return conflitos

    def reservar_recursos(self, recursos, dia, turno, sem_ini, sem_fim):
        """
        Marca os recursos como ocupados nas semanas definidas.
        """
        semanas_novas = set(range(sem_ini, sem_fim + 1))
        
        for recurso in recursos:
            chave = f"{recurso}|{dia}|{turno}"
            if chave not in self.ocupacao:
                self.ocupacao[chave] = set()
            self.ocupacao[chave].update(semanas_novas)

    def ajustar_calendario_semana_1(self, dia, sem_ini_original, duracao_semanas):
        """
        LÓGICA INTELIGENTE 2026/1:
        O semestre começa 19/02 (Quinta-feira).
        - Se a aula for Quinta ou Sexta -> Pode começar na Semana 1.
        - Se a aula for Seg, Ter, Qua -> Só pode começar na Semana 2.
        """
        dias_validos_semana_1 = ['Quinta-Feira', 'Sexta-Feira']
        
        novo_inicio = int(sem_ini_original)
        
        # Se pediu semana 1, mas o dia não permite (é Seg/Ter/Qua)
        if novo_inicio == 1 and dia not in dias_validos_semana_1:
            novo_inicio = 2
            
        # Calcula o fim baseado na duração necessária
        novo_fim = novo_inicio + duracao_semanas - 1
        
        return novo_inicio, novo_fim

    def executar(self):
        dias_uteis = ['Segunda-Feira', 'Terça-Feira', 'Quarta-Feira', 'Quinta-Feira', 'Sexta-Feira']
        
        total_items = len(self.demandas)
        progress_bar = st.progress(0)

        for idx, row in self.demandas.iterrows():
            alocado = False
            
            # --- 1. PREPARAÇÃO DOS DADOS ---
            # Limpeza e separação das listas (Professores e Salas)
            docentes = [d.strip() for d in str(row['Docentes']).split(',') if d.strip()]
            espacos = [e.strip() for e in str(row['Espacos']).split('+') if e.strip()]
            
            # Se for EAD, ignoramos conflito de sala física, mas mantemos docente
            recursos_para_checar = docentes.copy()
            if "EAD" not in str(row['Regra_Especial']).upper() and "EAD" not in str(row['Espacos']).upper():
                recursos_para_checar.extend(espacos)

            # Cálculo da Duração em Semanas (Estimativa baseada na carga horária)
            # Assumindo aulas de 4h por turno. Ex: 80h = 20 semanas. 40h = 10 semanas.
            ch_total = float(row['Carga_Horaria_Total']) if row['Carga_Horaria_Total'] else 0
            duracao_estimada = int(np.ceil(ch_total / 4)) # Arredonda para cima
            
            # Se o usuário definiu semanas fixas no Excel, usa elas. Se não, usa a duração calculada.
            sem_ini_base = int(row['Semana_Inicio']) if row['Semana_Inicio'] != "" else 1
            
            # --- 2. TENTATIVA DE ALOCAÇÃO ---
            # Se tiver dia travado, tenta só ele. Se não, tenta todos.
            dias_para_tentar = [row['Dia_Travado']] if row['Dia_Travado'] else dias_uteis

            for dia in dias_para_tentar:
                if alocado: break
                
                # APLICA A REGRA DO CALENDÁRIO (Semana 1 vs Semana 2)
                sem_ini_real, sem_fim_real = self.ajustar_calendario_semana_1(dia, sem_ini_base, duracao_estimada)
                
                # Verifica disponibilidade
                conflitos = self.verificar_disponibilidade(
                    recursos_para_checar, dia, row['Turno'], sem_ini_real, sem_fim_real
                )

                if not conflitos:
                    # SUCESSO!
                    self.reservar_recursos(recursos_para_checar, dia, row['Turno'], sem_ini_real, sem_fim_real)
                    
                    self.grade.append({
                        "ID_Turma": row['ID_Turma'],
                        "UC": row['Nome_UC'],
                        "Dia": dia,
                        "Turno": row['Turno'],
                        "Docentes": ", ".join(docentes),
                        "Espacos": " + ".join(espacos),
                        "Semana_Inicio": sem_ini_real,
                        "Semana_Fim": sem_fim_real,
                        "Status": "✅ OK"
                    })
                    alocado = True
                
            if not alocado:
                motivo = f"Conflito com {conflitos}" if 'conflitos' in locals() and conflitos else "Sem dias disponíveis"
                self.log_erros.append(f"❌ {row['ID_Turma']} - {row['Nome_UC']}: {motivo}")
                self.grade.append({
                        "ID_Turma": row['ID_Turma'],
                        "UC": row['Nome_UC'],
                        "Dia": "N/A",
                        "Turno": row['Turno'],
                        "Docentes": str(row['Docentes']),
                        "Espacos": str(row['Espacos']),
                        "Semana_Inicio": "-",
                        "Semana_Fim": "-",
                        "Status": "❌ Erro"
                    })

            progress_bar.progress((idx + 1) / total_items)

        return pd.DataFrame(self.grade), self.log_erros

# --- 3. INTERFACE DO USUÁRIO ---

st.sidebar.header("📂 Área de Trabalho")

# Botão para baixar o modelo
st.sidebar.download_button(
    label="📥 Baixar Modelo de Planilha (Excel)",
    data=gerar_template(),
    file_name="modelo_demandas_ifsc.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

st.sidebar.markdown("---")

uploaded_file = st.sidebar.file_uploader("Carregar Planilha Preenchida", type=['xlsx'])

if uploaded_file:
    try:
        df_input = pd.read_excel(uploaded_file, sheet_name='Demandas')
        
        st.info(f"Planilha carregada com {len(df_input)} linhas de demanda.")
        
        if st.button("🚀 Iniciar Alocação Automática"):
            motor = MotorAlocacao(df_input)
            df_resultado, erros = motor.executar()
            
            # Exibição dos Resultados
            st.markdown("---")
            st.subheader("📊 Resultado da Grade")
            
            # Filtros de Visualização
            filtro_turma = st.selectbox("Filtrar por Turma:", ["Todas"] + list(df_resultado['ID_Turma'].unique()))
            
            df_view = df_resultado
            if filtro_turma != "Todas":
                df_view = df_resultado[df_resultado['ID_Turma'] == filtro_turma]
            
            st.dataframe(df_view, use_container_width=True)
            
            # Área de Erros
            if erros:
                with st.expander(f"⚠️ Atenção: {len(erros)} aulas não puderam ser alocadas", expanded=True):
                    for erro in erros:
                        st.error(erro)
            else:
                st.success("Parabéns! Todas as aulas foram alocadas sem conflitos.")
                
            # Download Final
            csv = df_resultado.to_csv(index=False).encode('utf-8')
            st.download_button(
                "💾 Baixar Grade Final (CSV)",
                csv,
                "grade_final_ifsc.csv",
                "text/csv",
                key='download-csv'
            )
            
    except Exception as e:
        st.error(f"Erro ao ler o arquivo. Verifique se a aba se chama 'Demandas' e se as colunas estão corretas. Detalhe: {e}")

else:
    st.markdown("""
    ### 👋 Bem-vindo ao Motor de Alocação
    
    Para começar:
    1. Baixe o **Modelo de Planilha** na barra lateral.
    2. Preencha a aba **Demandas** com as turmas, professores e regras.
    3. Faça o upload do arquivo aqui.
    
    **Nota sobre a Semana 1:** O sistema ajusta automaticamente. Se você pedir Semana 1 para uma aula de Segunda-feira, ele alocará a partir da Semana 2 (pois o semestre começa numa Quinta).
    """)
